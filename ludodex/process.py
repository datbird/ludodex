#!/usr/bin/env python3
"""Stage 2 of the local-archive pipeline: examine unprocessed crawled files and
extract structured facts into the `extracted` table (crawl-index.sqlite).

For each new file it derives: system (from the path), a cleaned game title and
its dedupe key, plus attributes parsed from the name (region, languages,
version, revision, disc, dump flags, format). It also flags whether the file is
just a *variant of a game already in the catalog* (is_variant) and records the
game key it is a variant OF (base_norm_key).

BOTH OF THOSE ARE PER (GAME, PLATFORM), because that is the unit of identity here — a
Genesis file is not "a variant" of a SNES game that happens to share a title, and saying
so is the same title-level confusion the per-platform entry model exists to end.
`base_norm_key` names the game a variant belongs to, so it is set only when there IS one:
copying `nk` into it unconditionally made the column a second copy of `norm_key` and it
carried no information at all.

Only files with processed=0 are handled; each is marked processed afterward, so
re-runs do incremental work. build_library.py ingests `extracted`.

  python3 ludodex/process.py            # process all pending files
  python3 ludodex/process.py --all      # re-process everything (clears processed flags)
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import platmap
import titlenorm
from romtags import parse_name
from titlenorm import norm

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
DB = os.path.join(DATA, "crawl-index.sqlite")


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

    # "already in the db" = catalog entries + anything already extracted, keyed by
    # (game, PLATFORM) — the catalog's own unit of identity.
    seen = set()
    lib = config.get("library_db")
    if lib and os.path.exists(lib):
        try:
            lc = sqlite3.connect(lib)
            seen.update((k, platmap.canon(p or ""))
                        for k, p in lc.execute("SELECT norm_key, platform FROM games"))
            lc.close()
        except sqlite3.OperationalError:
            pass
    seen.update((k, platmap.canon(sy or ""))
                for k, sy in con.execute("SELECT norm_key, system FROM extracted"))

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
        # The CATALOG's key, merges included, so a file lands on the same game the
        # catalog files it under rather than on a key nothing else uses.
        nk = (titlenorm.catalog_key(title, system or None)
              or titlenorm.catalog_key(filename, system or None))
        ekey = (nk, platmap.canon(system or ""))
        is_var = 1 if ekey in seen else 0
        seen.add(ekey)
        con.execute(
            "INSERT OR REPLACE INTO extracted(file_id,archive,kind,system,title,"
            "norm_key,region,languages,version,revision,disc,flags,ext,is_variant,"
            "base_norm_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, archive, kind, system, title, nk, region, langs, ver, rev, disc,
             flags, ext, is_var, nk if is_var else None))
        con.execute("UPDATE files SET processed=1 WHERE id=?", (fid,))
        n += 1
        nvar += is_var
    con.commit()
    con.close()
    log("processed %d file(s): %d new game(s), %d variant(s) of existing games"
        % (n, n - nvar, nvar))


if __name__ == "__main__":
    main(sys.argv[1:])
