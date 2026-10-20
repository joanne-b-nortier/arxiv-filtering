#!/bin/bash


source arxiv/bin/activate

mydate=$(stat -f "%Sm" -t "%m-%d" papers.db)

if [ -f "papers.db" ]; then
    mv papers.db past-papers/papers-${mydate}.db
fi

python fetch_papers.py 
python serve.py
