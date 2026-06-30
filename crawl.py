#!/usr/bin/env python3
"""Crawl local archive directories into crawl-index.sqlite for the catalog.

Archives are registered in config (see `config.py archive add`). Each has a
kind:
  rom  - recurse the tree; the first folder under the root is the system; only
         ROM/disc files are indexed; titles are cleaned via romtags.
  flat - each immediate child of the root (file or folder) is one title.

build_library.py ingests the result as the `archive` source kind (one per
archive name). Only enabled archives are crawled.

  python3 crawl.py            # crawl all enabled archives
  python3 crawl.py <name> …   # crawl only the named archive(s)
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from romtags import parse_name, ROM_EXTS

DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DIR, "crawl-index.sqlite")
SKIP_DIRS = {".git", "@eaDir", "#recycle", "lost+found", "System Volume Information"}


def log(m):
    print(m, file=sys.stderr, flush=True)


def crawl_rom(root, name):
    rows = []
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        rel = os.path.relpath(dp, root)
        system = "" if rel == "." else rel.split(os.sep)[0]
        for fn in files:
            ext = fn.rsplit(".", 1)[1].lower() if "." in fn else ""
            if ext not in ROM_EXTS:
                continue
            title, region, *_ = parse_name(fn)
            if not title:
                title = os.path.splitext(fn)[0]
            full = os.path.join(dp, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            rows.append((name, "rom", system, title, ext,
                         os.path.relpath(full, root), size, region))
    return rows


def crawl_flat(root, name):
    rows = []
    for entry in sorted(os.listdir(root)):
        if entry.startswith(".") or entry in SKIP_DIRS:
            continue
        full = os.path.join(root, entry)
        if os.path.isdir(full):
            title, ext, size = entry, "", 0
        else:
            stem, dot, e = entry.rpartition(".")
            title = stem if dot else entry
            ext = e.lower() if dot else ""
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
        rows.append((name, "flat", "", title, ext, entry, size, ""))
    return rows


def main(argv):
    want = set(argv)
    archives = [a for a in config.archives_list(only_enabled=True)
                if not want or a["name"] in want]
    if not archives:
        log("no enabled archives to crawl "
            "(add: config.py archive add <name> <path> [rom|flat])")
        return
    con = sqlite3.connect(OUT)
    con.execute("CREATE TABLE IF NOT EXISTS items(archive TEXT, kind TEXT, "
                "system TEXT, title TEXT, ext TEXT, relpath TEXT, "
                "size_bytes INTEGER, detail TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_items_arch ON items(archive)")
    for a in archives:
        name, path, kind = a["name"], a["path"], a["kind"]
        if not os.path.isdir(path):
            log("  %s: SKIP — not a directory: %s" % (name, path))
            continue
        rows = crawl_rom(path, name) if kind == "rom" else crawl_flat(path, name)
        con.execute("DELETE FROM items WHERE archive=?", (name,))   # replace
        con.executemany("INSERT INTO items(archive,kind,system,title,ext,relpath,"
                        "size_bytes,detail) VALUES(?,?,?,?,?,?,?,?)", rows)
        con.commit()
        log("  %s (%s): %d items" % (name, kind, len(rows)))
    con.close()
    log("crawl done -> %s" % OUT)


if __name__ == "__main__":
    main(sys.argv[1:])
