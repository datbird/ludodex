#!/usr/bin/env python3
"""Stage 1 of the local-archive pipeline: crawl registered archive directories
into a persistent, append-only `files` inventory (crawl-index.sqlite).

It records raw file facts only — full path, name, extension, size, mtime — and
adds ONLY new files (existing ones just get last_seen touched; a file whose
size/mtime changed is flagged for re-processing). Extraction of system/game/etc.
is the job of process.py (Stage 2).

  python3 crawl.py            # crawl all enabled archives
  python3 crawl.py <name> …   # crawl only the named archive(s)
"""
import os
import sys
import time
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from romtags import ROM_EXTS

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "crawl-index.sqlite")
SKIP_DIRS = {".git", "@eaDir", "#recycle", "lost+found", "System Volume Information"}


def log(m):
    print(m, file=sys.stderr, flush=True)


def _con():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS files(
        id INTEGER PRIMARY KEY,
        archive TEXT, kind TEXT, fullpath TEXT UNIQUE, filename TEXT, ext TEXT,
        size_bytes INTEGER, mtime REAL,
        first_seen REAL, last_seen REAL, processed INTEGER DEFAULT 0)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_files_arch ON files(archive)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_files_proc ON files(processed)")
    return con


def _entries(path, kind):
    """Yield (fullpath, filename, ext) for crawlable items under an archive."""
    if kind == "flat":
        for entry in sorted(os.listdir(path)):
            if entry.startswith(".") or entry in SKIP_DIRS:
                continue
            stem, dot, e = entry.rpartition(".")
            full = os.path.join(path, entry)
            ext = e.lower() if (dot and not os.path.isdir(full)) else ""
            yield full, entry, ext
    else:  # rom: recurse, ROM/disc files only
        for dp, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in files:
                ext = fn.rsplit(".", 1)[1].lower() if "." in fn else ""
                if ext in ROM_EXTS:
                    yield os.path.join(dp, fn), fn, ext


def main(argv):
    want = set(argv)
    archives = [a for a in config.archives_list(only_enabled=True)
                if not want or a["name"] in want]
    if not archives:
        log("no enabled archives (add: config.py archive add <name> <path> [rom|flat])")
        return
    now = time.time()
    con = _con()
    total_new = total_changed = 0
    for a in archives:
        name, path, kind = a["name"], a["path"], a["kind"]
        if not os.path.isdir(path):
            # removable drive unplugged / not mounted yet — skip, keep inventory
            log("  %s: SKIP — not mounted/missing: %s (existing entries kept)" %
                (name, path))
            continue
        new = changed = seen = 0
        for full, fn, ext in _entries(path, kind):
            try:
                st = os.stat(full)
                size, mtime = st.st_size, st.st_mtime
            except OSError:
                continue
            seen += 1
            row = con.execute("SELECT id,size_bytes,mtime FROM files WHERE fullpath=?",
                              (full,)).fetchone()
            if row is None:
                con.execute("INSERT INTO files(archive,kind,fullpath,filename,ext,"
                            "size_bytes,mtime,first_seen,last_seen,processed) "
                            "VALUES(?,?,?,?,?,?,?,?,?,0)",
                            (name, kind, full, fn, ext, size, mtime, now, now))
                new += 1
            elif row[1] != size or row[2] != mtime:
                con.execute("UPDATE files SET size_bytes=?,mtime=?,last_seen=?,"
                            "processed=0 WHERE id=?", (size, mtime, now, row[0]))
                changed += 1
            else:
                con.execute("UPDATE files SET last_seen=? WHERE id=?", (now, row[0]))
        con.commit()
        log("  %s (%s): %d new, %d changed, %d total on disk" %
            (name, kind, new, changed, seen))
        total_new += new
        total_changed += changed
    con.close()
    log("crawl done: %d new, %d changed -> %s" % (total_new, total_changed, DB))


if __name__ == "__main__":
    main(sys.argv[1:])
