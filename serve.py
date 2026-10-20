import json, sqlite3, http.server, urllib.parse, datetime

DB = "papers.db"
PORT = 8765

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>arXiv Digest</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 860px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
  h1 {{ font-size: 1.3rem; font-weight: 500; margin-bottom: 0.25rem; }}
  .meta {{ font-size: 0.8rem; color: #888; margin-bottom: 1.5rem; }}
  .filters {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 1.5rem; }}
  .filters a {{ font-size: 0.8rem; padding: 4px 12px; border: 1px solid #ddd; border-radius: 99px; text-decoration: none; color: #444; }}
  .filters a.active {{ border-color: #4a6cf7; color: #4a6cf7; background: #f0f3ff; }}
  .paper {{ border: 1px solid #eee; border-radius: 10px; padding: 1rem 1.25rem; margin-bottom: 10px; position: relative; }}
  .paper h2 {{ font-size: 0.95rem; font-weight: 500; margin: 0 0 0.3rem; padding-right: 2rem; }}
  .paper h2 a {{ color: inherit; text-decoration: none; }}
  .paper h2 a:hover {{ text-decoration: underline; }}
  .authors {{ font-size: 0.8rem; color: #555; margin-bottom: 0.5rem; }}
  .abstract {{ font-size: 0.82rem; color: #666; line-height: 1.6; margin-bottom: 0.6rem;
               display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }}
  .tags {{ display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }}
  .tag {{ font-size: 0.7rem; padding: 2px 8px; border-radius: 99px; border: 1px solid #e0e0f0;
           background: #f0f3ff; color: #4a6cf7; }}
  .tag.auth {{ background: #f5f0ff; border-color: #ddd0ff; color: #7c4af7; }}
  .date {{ font-size: 0.7rem; color: #aaa; margin-left: auto; }}
  .empty {{ color: #999; font-size: 0.9rem; padding: 2rem 0; }}

  /* star button */
  .star-btn {{
    position: absolute; top: 0.9rem; right: 1rem;
    background: none; border: none; cursor: pointer;
    font-size: 1.1rem; line-height: 1; padding: 2px;
    color: #ccc; transition: color 0.15s;
  }}
  .star-btn:hover {{ color: #f5a623; }}
  .star-btn.starred {{ color: #f5a623; }}

  /* context menu */
  #ctx-menu {{
    display: none; position: fixed; z-index: 1000;
    background: #fff; border: 1px solid #ddd; border-radius: 8px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.12); overflow: hidden; min-width: 180px;
  }}
  #ctx-menu button {{
    display: block; width: 100%; text-align: left;
    padding: 9px 16px; font-size: 0.85rem; border: none;
    background: none; cursor: pointer; color: #1a1a1a;
  }}
  #ctx-menu button:hover {{ background: #f5f5f5; }}
  #ctx-menu button.restore {{ color: #2a9d2a; }}
  #ctx-menu button.dismiss {{ color: #c0392b; }}
  #ctx-menu hr {{ border: none; border-top: 1px solid #eee; margin: 2px 0; }}

  @media (prefers-color-scheme: dark) {{
    body {{ background: #111; color: #e8e8e8; }}
    .paper {{ border-color: #2a2a2a; background: #1a1a1a; }}
    .authors {{ color: #aaa; }}
    .abstract {{ color: #999; }}
    .filters a {{ border-color: #333; color: #aaa; background: #111; }}
    .filters a.active {{ border-color: #4a6cf7; color: #8aacff; background: #1a2040; }}
    #ctx-menu {{ background: #222; border-color: #444; }}
    #ctx-menu button {{ color: #e8e8e8; }}
    #ctx-menu button:hover {{ background: #333; }}
    #ctx-menu hr {{ border-color: #444; }}
    .star-btn {{ color: #555; }}
  }}
</style>
</head>
<body>
<h1>arXiv digest</h1>
<p class="meta">{count} matched papers · {filter_label}</p>
<div class="filters">
  <a href="?" class="{all_active}">All</a>
  <a href="?by=author" class="{author_active}">By author</a>
  <a href="?by=keyword" class="{kw_active}">By keyword</a>
  <a href="?by=recent" class="{recent_active}">Last 7 days</a>
  <a href="?by=starred" class="{starred_active}">⭐ Saved</a>
  <a href="?by=dismissed" class="{dismissed_active}">Dismissed</a>
</div>
{papers_html}

<div id="ctx-menu">
  <button class="dismiss" onclick="handleDismiss()">Dismiss this paper</button>
  <button class="restore" onclick="handleRestore()" style="display:none">Restore this paper</button>
  <hr>
  <button class="star-toggle" onclick="handleStarToggle()">Save for later</button>
</div>

<script>
  const menu = document.getElementById('ctx-menu');
  let activePaperId = null;
  const currentView = new URLSearchParams(location.search).get('by');

  document.querySelectorAll('.paper').forEach(el => {{
    el.addEventListener('contextmenu', e => {{
      e.preventDefault();
      activePaperId = el.dataset.id;
      const isStarred = el.dataset.starred === '1';
      menu.querySelector('.dismiss').style.display  = currentView === 'dismissed' ? 'none' : 'block';
      menu.querySelector('.restore').style.display  = currentView === 'dismissed' ? 'block' : 'none';
      menu.querySelector('.star-toggle').textContent = isStarred ? 'Remove from saved' : 'Save for later';
      menu.style.display = 'block';
      menu.style.left = Math.min(e.clientX, window.innerWidth - 200) + 'px';
      menu.style.top  = Math.min(e.clientY, window.innerHeight - 120) + 'px';
    }});
  }});

  document.addEventListener('click', () => {{ menu.style.display = 'none'; }});
  document.addEventListener('keydown', e => {{ if (e.key === 'Escape') menu.style.display = 'none'; }});

  function handleDismiss() {{
    menu.style.display = 'none';
    fetch('/dismiss?id=' + activePaperId)
      .then(r => r.ok
        ? document.querySelector(`[data-id="${{activePaperId}}"]`).remove()
        : alert('Error dismissing paper'));
  }}

  function handleRestore() {{
    menu.style.display = 'none';
    fetch('/restore?id=' + activePaperId)
      .then(r => r.ok
        ? document.querySelector(`[data-id="${{activePaperId}}"]`).remove()
        : alert('Error restoring paper'));
  }}

  function handleStarToggle() {{
    menu.style.display = 'none';
    const el = document.querySelector(`[data-id="${{activePaperId}}"]`);
    const isStarred = el.dataset.starred === '1';
    fetch('/star?id=' + activePaperId + '&val=' + (isStarred ? '0' : '1'))
      .then(r => {{
        if (!r.ok) {{ alert('Error updating star'); return; }}
        el.dataset.starred = isStarred ? '0' : '1';
        const btn = el.querySelector('.star-btn');
        btn.textContent = isStarred ? '☆' : '★';
        btn.classList.toggle('starred', !isStarred);
        if (currentView === 'starred') el.remove();
      }});
  }}

  function clickStar(paperId) {{
    const el = document.querySelector(`[data-id="${{paperId}}"]`);
    const isStarred = el.dataset.starred === '1';
    fetch('/star?id=' + paperId + '&val=' + (isStarred ? '0' : '1'))
      .then(r => {{
        if (!r.ok) {{ alert('Error updating star'); return; }}
        el.dataset.starred = isStarred ? '0' : '1';
        const btn = el.querySelector('.star-btn');
        btn.textContent = isStarred ? '☆' : '★';
        btn.classList.toggle('starred', !isStarred);
        if (currentView === 'starred') el.remove();
      }});
  }}
</script>
</body></html>"""

def migrate_db(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info(papers)").fetchall()]
    if "dismissed" not in cols:
        con.execute("ALTER TABLE papers ADD COLUMN dismissed INTEGER DEFAULT 0")
    if "starred" not in cols:
        con.execute("ALTER TABLE papers ADD COLUMN starred INTEGER DEFAULT 0")
    con.commit()

def init_db(con):
    con.execute("""CREATE TABLE IF NOT EXISTS papers (
        id TEXT PRIMARY KEY, title TEXT, authors TEXT,
        abstract TEXT, link TEXT, published TEXT,
        matched_keywords TEXT, matched_authors TEXT,
        fetched_at TEXT, dismissed INTEGER DEFAULT 0,
        starred INTEGER DEFAULT 0)""")
    con.commit()
    migrate_db(con)

def get_papers(by=None):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    if by == "author":
        rows = con.execute("SELECT * FROM papers WHERE matched_authors != '[]' AND dismissed=0 ORDER BY published DESC").fetchall()
    elif by == "keyword":
        rows = con.execute("SELECT * FROM papers WHERE matched_keywords != '[]' AND matched_authors = '[]' AND dismissed=0 ORDER BY published DESC").fetchall()
    elif by == "recent":
        cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        rows = con.execute("SELECT * FROM papers WHERE published>=? AND dismissed=0 ORDER BY published DESC", (cutoff,)).fetchall()
    elif by == "starred":
        rows = con.execute("SELECT * FROM papers WHERE starred=1 AND dismissed=0 ORDER BY published DESC").fetchall()
    elif by == "dismissed":
        rows = con.execute("SELECT * FROM papers WHERE dismissed=1 ORDER BY published DESC").fetchall()
    else:
        rows = con.execute("SELECT * FROM papers WHERE dismissed=0 ORDER BY published DESC").fetchall()
    con.close()
    return rows

def render_paper(row):
    kws   = json.loads(row["matched_keywords"])
    auths = json.loads(row["matched_authors"])
    tags  = "".join(f'<span class="tag">{k}</span>' for k in kws)
    tags += "".join(f'<span class="tag auth">{a}</span>' for a in auths)
    date  = row["published"] or ""
    authors_hl = row["authors"] or ""
    for a in auths:
        authors_hl = authors_hl.replace(a, f"<strong>{a}</strong>")
    starred    = int(row["starred"] or 0)
    star_class = "star-btn starred" if starred else "star-btn"
    star_char  = "★" if starred else "☆"
    return f"""<div class="paper" data-id="{row['id']}" data-starred="{starred}">
  <button class="{star_class}" onclick="clickStar('{row['id']}')" title="Save for later">{star_char}</button>
  <h2><a href="{row['link']}" target="_blank">{row['title']}</a></h2>
  <div class="authors">{authors_hl}</div>
  <div class="abstract">{row['abstract'][:600]}</div>
  <div class="tags">{tags}<span class="date">{date}</span></div>
</div>"""

def set_field(paper_id, field, value):
    con = sqlite3.connect(DB)
    con.execute(f"UPDATE papers SET {field}=? WHERE id=?", (value, paper_id))
    con.commit()
    con.close()

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs     = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/dismiss":
            if pid := qs.get("id", [None])[0]:
                set_field(pid, "dismissed", 1)
            self.send_response(200); self.end_headers(); return

        if parsed.path == "/restore":
            if pid := qs.get("id", [None])[0]:
                set_field(pid, "dismissed", 0)
            self.send_response(200); self.end_headers(); return

        if parsed.path == "/star":
            if pid := qs.get("id", [None])[0]:
                val = int(qs.get("val", ["1"])[0])
                set_field(pid, "starred", val)
            self.send_response(200); self.end_headers(); return

        con = sqlite3.connect(DB)
        init_db(con)
        con.close()

        by         = qs.get("by", [None])[0]
        rows       = get_papers(by)
        papers_html = "".join(render_paper(r) for r in rows) or '<p class="empty">No papers here.</p>'
        labels     = {
            "author":    "matched authors",
            "keyword":   "matched keywords",
            "recent":    "last 7 days",
            "starred":   "saved for later",
            "dismissed": "dismissed",
            None:        "all time",
        }
        html = HTML_TEMPLATE.format(
            count        = len(rows),
            filter_label = labels.get(by, "all time"),
            papers_html  = papers_html,
            all_active      = "active" if not by else "",
            author_active   = "active" if by == "author"   else "",
            kw_active       = "active" if by == "keyword"  else "",
            recent_active   = "active" if by == "recent"   else "",
            starred_active  = "active" if by == "starred"  else "",
            dismissed_active= "active" if by == "dismissed" else "",
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

if __name__ == "__main__":
    print(f"Serving at http://localhost:{PORT}")
    http.server.HTTPServer(("", PORT), Handler).serve_forever()