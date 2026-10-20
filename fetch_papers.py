import json
import sqlite3
import feedparser
import hashlib
import datetime
import re
import time
import random
import urllib.parse
import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

with open("config.json", "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

DB = "papers.db"
API = "https://export.arxiv.org/api/query"

# Number of results requested per API page.
# Pagination ensures we do not silently miss results beyond this number.
MAX_RESULTS = 100

# arXiv asks clients to wait at least 3 seconds between consecutive requests.
MIN_REQUEST_INTERVAL = 3.0

# Retry configuration for HTTP 429 responses.
MAX_RETRIES = 5
INITIAL_BACKOFF = 10.0
MAX_BACKOFF = 300.0

# A descriptive User-Agent is good practice.
USER_AGENT = (
    "arxiv-digest/2.0 "
    "(personal research tool; contact: your-email@example.com)"
)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            id TEXT PRIMARY KEY,
            title TEXT,
            authors TEXT,
            abstract TEXT,
            link TEXT,
            published TEXT,
            matched_keywords TEXT,
            matched_authors TEXT,
            fetched_at TEXT,
            dismissed INTEGER DEFAULT 0
        )
    """)
    con.commit()


def migrate_db(con):
    cols = [
        row[1]
        for row in con.execute("PRAGMA table_info(papers)").fetchall()
    ]

    if "dismissed" not in cols:
        con.execute(
            "ALTER TABLE papers ADD COLUMN dismissed INTEGER DEFAULT 0"
        )
        con.commit()


# ---------------------------------------------------------------------------
# Text and query helpers
# ---------------------------------------------------------------------------

def normalize(s):
    """Normalize whitespace and case for local matching."""
    return re.sub(r"\s+", " ", s.strip().lower())


def deduplicate(values):
    """
    Remove exact duplicates while preserving order.
    Comparison is case-insensitive and ignores surrounding whitespace.
    """
    seen = set()
    result = []

    for value in values:
        value = value.strip()
        key = value.casefold()

        if key not in seen:
            seen.add(key)
            result.append(value)

    return result


def quote_api_value(value):
    """
    Escape a value for use inside an arXiv API quoted search term.

    arXiv search syntax is not a general-purpose SQL parser, but escaping
    embedded double quotes is still preferable to inserting them directly.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_or_filter(field, values):
    """
    Build a Boolean OR filter, e.g.:

        au:"Carletti" OR au:"Serrano"
    """
    return " OR ".join(
        f'{field}:"{quote_api_value(value)}"'
        for value in values
    )


def build_keyword_filter(keywords):
    """
    Build a Boolean OR filter searching titles and abstracts, e.g.:

        (ti:"sheaf" OR abs:"sheaf")
        OR (ti:"hypergraph" OR abs:"hypergraph")
    """
    return " OR ".join(
        f'(ti:"{quote_api_value(keyword)}" '
        f'OR abs:"{quote_api_value(keyword)}")'
        for keyword in keywords
    )


def build_category_filter(categories):
    """
    Build a category filter such as:

        (cat:cs.LG OR cat:cs.AI OR cat:stat.ML)
    """
    categories = deduplicate(categories)

    if not categories:
        return ""

    return "(" + " OR ".join(
        f"cat:{category}"
        for category in categories
    ) + ")"


def build_date_filter(days_back):
    """
    Build an arXiv submittedDate range covering the last N days.

    arXiv date syntax uses UTC timestamps in YYYYMMDDHHMM format.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(days=days_back)

    start_str = start.strftime("%Y%m%d%H%M")
    end_str = now.strftime("%Y%m%d%H%M")

    return f"submittedDate:[{start_str} TO {end_str}]"


def combine_query(*parts):
    """Combine non-empty query fragments with AND."""
    parts = [f"({part})" for part in parts if part]

    if not parts:
        return ""

    return " AND ".join(parts)


# ---------------------------------------------------------------------------
# Rate limiting and API access
# ---------------------------------------------------------------------------

class ArxivClient:
    """
    Small arXiv API client with:
      - minimum spacing between requests;
      - retry/backoff for HTTP 429;
      - support for Retry-After;
      - pagination.
    """

    def __init__(
        self,
        api_url=API,
        min_interval=MIN_REQUEST_INTERVAL,
        max_retries=MAX_RETRIES,
    ):
        self.api_url = api_url
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.last_request_time = 0.0

    def _wait_before_request(self):
        elapsed = time.monotonic() - self.last_request_time
        remaining = self.min_interval - elapsed

        if remaining > 0:
            time.sleep(remaining)

    def _request_page(self, search_query, start, max_results):
        params = urllib.parse.urlencode({
            "search_query": search_query,
            "start": start,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        })

        url = f"{self.api_url}?{params}"

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/atom+xml",
            },
        )

        for attempt in range(self.max_retries + 1):
            self._wait_before_request()

            try:
                self.last_request_time = time.monotonic()

                with urllib.request.urlopen(request, timeout=30) as response:
                    body = response.read()

                return feedparser.parse(body)

            except urllib.error.HTTPError as error:
                if error.code != 429:
                    raise

                if attempt >= self.max_retries:
                    raise RuntimeError(
                        "arXiv returned HTTP 429 too many times; "
                        "giving up for this query"
                    ) from error

                retry_after = error.headers.get("Retry-After")

                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = INITIAL_BACKOFF
                else:
                    delay = min(
                        MAX_BACKOFF,
                        INITIAL_BACKOFF * (2 ** attempt)
                    )

                # Small jitter avoids repeatedly retrying at exactly
                # the same time as other clients.
                delay += random.uniform(0, 3)

                print(
                    f"  HTTP 429 from arXiv; "
                    f"waiting {delay:.1f}s before retry "
                    f"({attempt + 1}/{self.max_retries})"
                )

                time.sleep(delay)

        raise RuntimeError("Unexpected API retry state")

    def query_all(self, search_query, max_results=MAX_RESULTS):
        """
        Retrieve all pages for a query.

        Returns a list of feedparser entries.

        We stop when:
          - the API returns fewer entries than requested; or
          - the API reports that no more results exist.
        """
        all_entries = []
        start = 0

        while True:
            feed = self._request_page(
                search_query=search_query,
                start=start,
                max_results=max_results,
            )

            entries = feed.entries
            all_entries.extend(entries)

            total_results = None
            try:
                total_results = int(
                    feed.feed.get("opensearch_totalresults", 0)
                )
            except (TypeError, ValueError):
                pass

            print(
                f"  Retrieved {len(entries)} results "
                f"(total collected: {len(all_entries)})"
            )

            if not entries:
                break

            start += len(entries)

            # If the API tells us the total, use it.
            if total_results is not None and start >= total_results:
                break

            # Otherwise, a short page normally indicates the end.
            if len(entries) < max_results:
                break

        return all_entries


# ---------------------------------------------------------------------------
# Paper processing
# ---------------------------------------------------------------------------

def date_cutoff(days_back):
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=days_back)
    )
    return cutoff.date()


def extract_arxiv_id(entry):
    """
    entry.id is normally like:
        http://arxiv.org/abs/2412.01234v1

    Store the base ID without the version suffix.
    """
    raw = entry.get("id", "")

    match = re.search(r"arxiv\.org/abs/([^v]+)", raw)

    if match:
        return match.group(1)

    return raw


def author_matches(configured_author, paper_authors):
    """
    Match a configured author against the paper's author names.

    We retain the original script's useful substring behaviour, but compare
    against individual author names rather than the entire comma-separated
    author string. This reduces accidental matches across name boundaries.

    Examples:
      "Kolk" matches "van der Kolk"
      "Domenico" matches "Manlio de Domenico"
    """
    target = normalize(configured_author)

    return any(
        target in normalize(paper_author)
        for paper_author in paper_authors
    )


def process_entries(
    entries,
    cutoff,
    config,
    con,
    now,
):
    """
    Process API entries, identify actual keyword/author matches, and insert
    new papers into the existing SQLite database.
    """
    added = 0

    configured_keywords = deduplicate(config.get("keywords", []))
    configured_authors = deduplicate(config.get("authors", []))

    for entry in entries:
        # ---------------------------------------------------------------
        # Date filtering (kept as a safety check even though the API query
        # already includes a submittedDate range).
        # ---------------------------------------------------------------
        published = entry.get("published", "")

        try:
            pub_date = datetime.datetime.strptime(
                published[:10],
                "%Y-%m-%d"
            ).date()

            if pub_date < cutoff:
                continue

        except (ValueError, TypeError):
            # Keep entries whose date cannot be parsed, matching the
            # permissive behaviour of the original script.
            pass

        # ---------------------------------------------------------------
        # Basic metadata
        # ---------------------------------------------------------------
        arxiv_id = extract_arxiv_id(entry)

        if not arxiv_id:
            continue

        pid = hashlib.md5(arxiv_id.encode("utf-8")).hexdigest()

        title = entry.get("title", "").strip()
        abstract = entry.get("summary", "").strip()

        title_abs = normalize(title + " " + abstract)

        paper_authors = [
            author.name
            for author in entry.get("authors", [])
            if getattr(author, "name", None)
        ]

        # ---------------------------------------------------------------
        # Identify actual matches locally
        # ---------------------------------------------------------------
        matched_kws = [
            keyword
            for keyword in configured_keywords
            if normalize(keyword) in title_abs
        ]

        matched_auths = [
            author
            for author in configured_authors
            if author_matches(author, paper_authors)
        ]

        if not matched_kws and not matched_auths:
            continue

        # ---------------------------------------------------------------
        # Insert new paper
        # ---------------------------------------------------------------
        link = f"https://arxiv.org/abs/{arxiv_id}"

        try:
            con.execute("""
                INSERT OR IGNORE INTO papers (
                    id,
                    title,
                    authors,
                    abstract,
                    link,
                    published,
                    matched_keywords,
                    matched_authors,
                    fetched_at,
                    dismissed
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pid,
                title,
                ", ".join(paper_authors),
                abstract,
                link,
                published[:10],
                json.dumps(matched_kws, ensure_ascii=False),
                json.dumps(matched_auths, ensure_ascii=False),
                now,
                0,
            ))

            if con.execute("SELECT changes()").fetchone()[0]:
                added += 1

        except Exception as error:
            print(f"  skip {arxiv_id}: {error}")

    return added


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    con = sqlite3.connect(DB)

    try:
        init_db(con)
        migrate_db(con)

        now = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()

        days_back = CONFIG.get("days_back", 7)
        cutoff = date_cutoff(days_back)

        # Deduplicate configuration values in memory.
        authors = deduplicate(CONFIG.get("authors", []))
        keywords = deduplicate(CONFIG.get("keywords", []))
        categories = deduplicate(CONFIG.get("categories", []))

        cat_filter = build_category_filter(categories)
        date_filter = build_date_filter(days_back)

        client = ArxivClient()

        added = 0
        total_entries_seen = 0

        # ---------------------------------------------------------------
        # Combined author query
        # ---------------------------------------------------------------
        if authors:
            author_filter = build_or_filter("au", authors)

            query = combine_query(
                author_filter,
                cat_filter,
                date_filter,
            )

            print(
                f"Querying {len(authors)} authors in one request group"
            )

            try:
                entries = client.query_all(query)
                total_entries_seen += len(entries)

                n = process_entries(
                    entries=entries,
                    cutoff=cutoff,
                    config=CONFIG,
                    con=con,
                    now=now,
                )

                print(f"  {len(entries)} author-query results, {n} new")
                added += n

            except Exception as error:
                print(f"  author query error: {error}")

        # ---------------------------------------------------------------
        # Combined keyword query
        # ---------------------------------------------------------------
        if keywords:
            keyword_filter = build_keyword_filter(keywords)

            query = combine_query(
                keyword_filter,
                cat_filter,
                date_filter,
            )

            print(
                f"Querying {len(keywords)} keywords in one request group"
            )

            try:
                entries = client.query_all(query)
                total_entries_seen += len(entries)

                n = process_entries(
                    entries=entries,
                    cutoff=cutoff,
                    config=CONFIG,
                    con=con,
                    now=now,
                )

                print(f"  {len(entries)} keyword-query results, {n} new")
                added += n

            except Exception as error:
                print(f"  keyword query error: {error}")

        con.commit()

        print(
            f"\nDone — {added} new papers saved "
            f"({total_entries_seen} API results processed)."
        )

    finally:
        con.close()


if __name__ == "__main__":
    main()


# import json, sqlite3, feedparser, hashlib, datetime, re, time, urllib.parse, urllib.request

# CONFIG = json.load(open("config.json"))
# DB = "papers.db"
# API = "https://export.arxiv.org/api/query"
# MAX_RESULTS = 100  # per query, max allowed by API is 2000 but 100 is safe

# def init_db(con):
#     con.execute("""CREATE TABLE IF NOT EXISTS papers (
#         id TEXT PRIMARY KEY, title TEXT, authors TEXT,
#         abstract TEXT, link TEXT, published TEXT,
#         matched_keywords TEXT, matched_authors TEXT,
#         fetched_at TEXT, dismissed INTEGER DEFAULT 0)""")
#     con.commit()

# def migrate_db(con):
#     cols = [r[1] for r in con.execute("PRAGMA table_info(papers)").fetchall()]
#     if "dismissed" not in cols:
#         con.execute("ALTER TABLE papers ADD COLUMN dismissed INTEGER DEFAULT 0")
#         con.commit()

# def normalize(s): return re.sub(r'\s+', ' ', s.strip().lower())

# def fetch_api(search_query, max_results=MAX_RESULTS):
#     params = urllib.parse.urlencode({
#         "search_query": search_query,
#         "max_results": max_results,
#         "sortBy": "submittedDate",
#         "sortOrder": "descending",
#     })
#     url = f"{API}?{params}"
#     req = urllib.request.Request(url, headers={"User-Agent": "arxiv-digest/1.0 (personal research tool)"})
#     with urllib.request.urlopen(req, timeout=20) as resp:
#         return feedparser.parse(resp.read())

# def date_cutoff(days_back):
#     cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_back)
#     return cutoff.date()

# def extract_arxiv_id(entry):
#     # entry.id is like http://arxiv.org/abs/2412.01234v1 — use the base ID
#     raw = entry.get("id", "")
#     m = re.search(r"arxiv\.org/abs/([^v]+)", raw)
#     return m.group(1) if m else raw

# def process_entries(entries, cutoff, matched_kws, matched_auths, config, con, now):
#     added = 0
#     for entry in entries:
#         # filter by date
#         published = entry.get("published", "")
#         try:
#             pub_date = datetime.datetime.strptime(published[:10], "%Y-%m-%d").date()
#             if pub_date < cutoff:
#                 continue
#         except ValueError:
#             pass

#         arxiv_id = extract_arxiv_id(entry)
#         pid = hashlib.md5(arxiv_id.encode()).hexdigest()

#         # re-check keywords and authors against this paper's full text
#         title_abs = normalize(entry.get("title", "") + " " + entry.get("summary", ""))
#         authors_list = [a.name for a in entry.get("authors", [])]
#         authors_str = normalize(", ".join(authors_list))

#         kws = [kw for kw in config["keywords"] if kw.lower() in title_abs]
#         auths = [a for a in config["authors"] if normalize(a) in authors_str]

#         # include if it matches either list (it came from an author or keyword query)
#         kws = kws or matched_kws
#         auths = auths or matched_auths
#         if not kws and not auths:
#             continue

#         link = f"https://arxiv.org/abs/{arxiv_id}"
#         try:
#             con.execute("INSERT OR IGNORE INTO papers VALUES (?,?,?,?,?,?,?,?,?,?)", (
#                 pid,
#                 entry.get("title", "").strip(),
#                 ", ".join(authors_list),
#                 entry.get("summary", "").strip(),
#                 link,
#                 published[:10],
#                 json.dumps(kws),
#                 json.dumps(auths),
#                 now,
#                 0,
#             ))
#             if con.execute("SELECT changes()").fetchone()[0]:
#                 added += 1
#         except Exception as e:
#             print(f"  skip {arxiv_id}: {e}")
#     return added

# def build_category_filter(categories):
#     if not categories:
#         return ""
#     parts = [f"cat:{c}" for c in categories]
#     return " OR ".join(parts)

# def main():
#     con = sqlite3.connect(DB)
#     init_db(con)
#     migrate_db(con)
#     now = datetime.datetime.now(datetime.UTC).isoformat()
#     cutoff = date_cutoff(CONFIG.get("days_back", 7))
#     cat_filter = build_category_filter(CONFIG.get("categories", []))
#     added = 0

#     # --- author queries (restricted to your chosen categories) ---
#     for author in CONFIG.get("authors", []):
#         query = f'au:"{author}"'
#         if cat_filter:
#             query += f" AND ({cat_filter})"
#         print(f"Querying author: {author}")
#         try:
#             feed = fetch_api(query)
#             n = process_entries(feed.entries, cutoff, [], [author], CONFIG, con, now)
#             print(f"  {len(feed.entries)} results, {n} new")
#             added += n
#         except Exception as e:
#             print(f"  error: {e}")
#         time.sleep(3)  # arXiv asks for 3s between requests

#     # --- keyword queries (restricted to your chosen categories) ---
#     for kw in CONFIG.get("keywords", []):
#         query = f'(ti:"{kw}" OR abs:"{kw}")'
#         if cat_filter:
#             query += f" AND ({cat_filter})"
#         print(f"Querying keyword: {kw}")
#         try:
#             feed = fetch_api(query)
#             n = process_entries(feed.entries, cutoff, [kw], [], CONFIG, con, now)
#             print(f"  {len(feed.entries)} results, {n} new")
#             added += n
#         except Exception as e:
#             print(f"  error: {e}")
#         time.sleep(3)

#     con.commit()
#     con.close()
#     print(f"\nDone — {added} new papers saved.")

# if __name__ == "__main__":
#     main()