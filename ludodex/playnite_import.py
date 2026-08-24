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

  python3 ludodex/playnite_import.py [playnite_games.json]
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
    # No blanket DELETE — see the same change in launchbox_import. Rows this run did not
    # see are swept below, so a surviving file keeps its sha1/measurements/ai_pick.
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
            # media_index.put_local, not INSERT OR REPLACE — see the same change in
            # launchbox_import: REPLACE drops sha1/width/height/ai_pick/hidden, and it
            # ignored the ban list, so a banned Playnite image returned on every import.
            if media_index.put_local(con, nk, kind, "playnite", path, ext, now,
                                     system=system, mount="playnite",
                                     matched=int(is_match)):
                rows += 1
                matched += int(is_match)
    media_index.sweep(con, "playnite", now)
    con.commit()
    con.close()
    print("playnite_import: indexed %d art refs (%d matched) as 'playnite'"
          % (rows, matched), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
