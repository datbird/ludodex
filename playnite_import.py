#!/usr/bin/env python3
"""Index Playnite's own cover/background/icon art into media-index.sqlite as the
'playnite' provider, from the bridge's -Export JSON (playnite_import_json).

The textual side of that JSON is merged by build_library.py (Playnite as a
meta-layer). This script handles only its MEDIA: it records each game's existing
art as a reference keyed by norm_key, so your hand-curated Playnite art can win or
seed the chosen set (and, with playnite_media_overwrite=playnite-wins, propagate
to the other frontends and the server).

The paths are absolute Windows paths from inside the Playnite install. They index
fine as references; they're only readable for materialize/serve if that location
is reachable from this machine (e.g. a mounted share) — otherwise the reference is
still honest and just can't be materialized here.

  python3 playnite_import.py [playnite_games.json]
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import media_index
from titlenorm import norm

SLOTS = {"cover": "cover", "background": "background", "icon": "icon"}


def main(argv):
    src = (argv[0] if argv and not argv[0].startswith("--")
           else config.get("playnite_import_json"))
    if not src or not os.path.exists(src):
        sys.exit("no Playnite export JSON — run the bridge -Export, then set "
                 "playnite_import_json (got %r)" % src)
    try:
        recs = json.load(open(src, encoding="utf-8"))
    except (ValueError, OSError) as e:
        sys.exit("cannot read %s: %s" % (src, e))

    owned = set()
    db = config.get("library_db")
    if db and os.path.exists(db):
        import sqlite3
        c = sqlite3.connect(db)
        owned = {k for (k,) in c.execute("SELECT norm_key FROM games")}
        c.close()

    con = media_index.index_con()
    con.execute("DELETE FROM media WHERE provider='playnite'")
    now = int(time.time())
    rows = matched = 0
    for r in (recs or []):
        nk = norm(r.get("name") or "")
        if not nk:
            continue
        plats = r.get("platforms") or []
        system = (plats[0] if plats else "pc")
        for slot, kind in SLOTS.items():
            path = r.get(slot)
            if not path:
                continue
            ext = os.path.splitext(path)[1].lower().lstrip(".") or "jpg"
            is_match = nk in owned
            con.execute(
                "INSERT OR REPLACE INTO media(norm_key,system,kind,provider,mount,"
                "ref_type,ref,ext,matched,indexed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (nk, system, kind, "playnite", "playnite", "file", path, ext,
                 int(is_match), now))
            rows += 1
            matched += int(is_match)
    con.commit()
    con.close()
    print("playnite_import: indexed %d art refs (%d matched) as 'playnite'"
          % (rows, matched), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
