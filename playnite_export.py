#!/usr/bin/env python3
"""Export the ludodex catalog to the canonical Playnite interchange JSON
(see playnite.py). playnite_bridge.ps1 -Import reads it to create/enrich games.

One record per deduped game, merging attributes across its sources. When a game
was originally imported from Playnite, its lossless source_attrs record seeds the
output (round-trip); otherwise the record is synthesized from ludodex data.

  python3 playnite_export.py [out.json]
"""
import os
import sys
import json
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from playnite import LIST_KINDS, SCALAR_KINDS

# which ludodex source to claim as the game's provider identity, best first
PROVIDER_RANK = ["steam", "gog", "epic", "itch", "ea", "ubisoft", "battlenet",
                 "xbox", "amazon", "playnite", "archive", "emulation"]
_NUM = {"playtime", "play_count", "user_score", "critic_score",
        "community_score", "install_size"}


def main(argv):
    db = config.get("library_db")
    if not db or not os.path.exists(db):
        sys.exit("no catalog — run update.sh first")
    out = (argv[0] if argv else config.get("playnite_export_json")
           or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "ludodex_to_playnite.json"))
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    # game attributes (queryable, aggregated)
    attrs = {}
    for r in con.execute("SELECT game_id, kind, value FROM game_attributes"):
        attrs.setdefault(r["game_id"], {}).setdefault(r["kind"], []).append(r["value"])
    # lossless per-source records (for round-trip identity/fields)
    sattr = {}
    for r in con.execute("SELECT game_id, source, source_id, attrs_json FROM source_attrs"):
        sattr.setdefault(r["game_id"], []).append(r)
    # sources (for identity when no Playnite record exists)
    srcs = {}
    for r in con.execute("SELECT game_id, source, platform, source_id FROM sources"):
        srcs.setdefault(r["game_id"], []).append(r)

    records = []
    for g in con.execute("SELECT id, canonical_title FROM games"):
        gid = g["id"]
        rec = {"name": g["canonical_title"]}
        # seed identity/fields from an original Playnite record if present
        seed = None
        for sa in sattr.get(gid, []):
            try:
                seed = json.loads(sa["attrs_json"])
                break
            except ValueError:
                pass
        if seed:
            rec.update(seed)
            rec["name"] = g["canonical_title"]
        else:
            # synthesize identity from the best available source
            best = sorted(srcs.get(gid, []),
                          key=lambda s: PROVIDER_RANK.index(s["source"])
                          if s["source"] in PROVIDER_RANK else 99)
            if best:
                rec["source"] = best[0]["source"]
                rec["source_id"] = best[0]["source_id"]
        # overlay aggregated attributes (authoritative for lists; fill scalars)
        a = attrs.get(gid, {})
        for k in LIST_KINDS:
            if a.get(k):
                rec[k] = sorted(set(a[k]))
        for k in SCALAR_KINDS:
            if k in rec:
                continue
            vals = a.get(k)
            if not vals:
                continue
            if k in _NUM:
                rec[k] = max(int(v) for v in vals if str(v).lstrip("-").isdigit())
            elif k in ("favorite", "hidden"):
                rec[k] = any(v in ("True", "1", "true") for v in vals)
            elif k == "release_year":
                rec[k] = min(int(v) for v in vals if str(v).isdigit())
            else:
                rec[k] = vals[0]
        records.append(rec)
    con.close()

    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print("wrote %d records -> %s" % (len(records), out), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
