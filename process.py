#!/usr/bin/env python3
"""Stage 2 of the local-archive pipeline: examine unprocessed crawled files and
extract structured facts into the `extracted` table (crawl-index.sqlite).

For each new file it derives: system (from the path), a cleaned game title and
its dedupe key, plus attributes parsed from the name (region, languages,
version, revision, disc, dump flags, format). It also flags whether the file is
just a *variant of a game already in the catalog* (is_variant) and records the
game key it belongs to (base_norm_key).

Only files with processed=0 are handled; each is marked processed afterward, so
re-runs do incremental work. build_library.py ingests `extracted`.

  python3 process.py            # process all pending files
  python3 process.py --all      # re-process everything (clears processed flags)
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from romtags import parse_name
from titlenorm import norm

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "crawl-index.sqlite")


def log(m):
    print(m, file=sys.stderr, flush=True)


def main(argv):
    if not os.path.exists(DB):
        log("no crawl index yet — run crawl.py first")
        return
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS extracted(
        file_id INTEGER PRIMARY KEY,
        archive TEXT, kind TEXT, system TEXT, title TEXT, norm_key TEXT,
        region TEXT, languages TEXT, version TEXT, revision TEXT, disc TEXT,
        flags TEXT, ext TEXT, is_variant INTEGER, base_norm_key TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_ext_norm ON extracted(norm_key)")
    if "--all" in argv:
        con.execute("UPDATE files SET processed=0")
        con.execute("DELETE FROM extracted")
        con.commit()

    roots = {a["name"]: a["path"] for a in config.archives_list()}

    # "already in the db" = catalog games + anything already extracted
    seen = set()
    lib = config.get("library_db")
    if lib and os.path.exists(lib):
        try:
            lc = sqlite3.connect(lib)
            seen.update(k for (k,) in lc.execute("SELECT norm_key FROM games"))
            lc.close()
        except sqlite3.OperationalError:
            pass
    seen.update(k for (k,) in con.execute("SELECT norm_key FROM extracted"))

    pending = con.execute("SELECT id,archive,kind,fullpath,filename,ext "
                          "FROM files WHERE processed=0").fetchall()
    n = nvar = 0
    for fid, archive, kind, fullpath, filename, ext in pending:
        system = ""
        root = roots.get(archive)
        if root and kind == "rom":
            parts = os.path.relpath(fullpath, root).split(os.sep)
            if len(parts) > 1:
                system = parts[0]
        title, region, langs, ver, rev, disc, flags, _ = parse_name(filename)
        if not title:
            title = os.path.splitext(filename)[0]
        nk = norm(title) or norm(filename)
        is_var = 1 if nk in seen else 0
        seen.add(nk)
        con.execute(
            "INSERT OR REPLACE INTO extracted(file_id,archive,kind,system,title,"
            "norm_key,region,languages,version,revision,disc,flags,ext,is_variant,"
            "base_norm_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, archive, kind, system, title, nk, region, langs, ver, rev, disc,
             flags, ext, is_var, nk))
        con.execute("UPDATE files SET processed=1 WHERE id=?", (fid,))
        n += 1
        nvar += is_var
    con.commit()
    con.close()
    log("processed %d file(s): %d new game(s), %d variant(s) of existing games"
        % (n, n - nvar, nvar))


if __name__ == "__main__":
    main(sys.argv[1:])
