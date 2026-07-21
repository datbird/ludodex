#!/usr/bin/env python3
"""DRY-RUN the per-entry identity pipeline against the LIVE catalog for given norm_keys —
real IGDB + real AI, writes NOTHING. Replicates server/app.py's fetch so it can run without
importing the full server. Usage (inside the container):
    python3 verify_per_entry_live.py "tomb raider" "alice in wonderland" "star fox"
"""
import json
import os
import sqlite3
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "server"))  # `ai` lives in server/

import config
import igdb
import igdb_enrich
import ai
import titlenorm

DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(DATA, "game-library.sqlite")
MC = os.path.join(DATA, "metadata-cache.sqlite")
_FIELDS = ("fields id,name,slug,first_release_date,"
           "platforms.abbreviation,cover.image_id;")


def _year(ts):
    try:
        return time.gmtime(ts).tm_year
    except Exception:
        return None


def candidates(name, nk):
    cid, secret = config.get("igdb_client_id"), config.get("igdb_client_secret")
    if not (cid and secret):
        print("  (no IGDB creds)")
        return []
    tok, _ = igdb.get_token(cid, secret)
    safe = name.replace('"', "").replace("\\", "").replace("*", "")
    body = '%s where name ~ "%s"; sort first_release_date asc; limit 20;' % (_FIELDS, safe)
    out = []
    for h in (igdb.query("games", body, cid, tok) or []):
        if titlenorm.norm(h.get("name") or "") != nk:
            continue
        out.append({"id": h.get("id"), "name": h.get("name"),
                    "year": _year(h.get("first_release_date")),
                    "platforms": [{"name": p.get("abbreviation")}
                                  for p in (h.get("platforms") or []) if p.get("abbreviation")]})
    return out


def run(nk):
    lc = sqlite3.connect("file:%s?mode=ro" % LIB, uri=True)
    ents = [{"platform": p, "title": t} for p, t in lc.execute(
        "SELECT DISTINCT platform, canonical_title FROM games WHERE norm_key=? "
        "AND has_emulation=1 AND platform IS NOT NULL", (nk,))]
    lc.close()
    mc = sqlite3.connect("file:%s?mode=ro" % MC, uri=True)
    pr = mc.execute("SELECT igdb_id FROM igdb_resolution WHERE norm_key=?", (nk,)).fetchone()
    primary = pr[0] if pr and pr[0] else None
    pm = (mc.execute("SELECT payload_json FROM igdb_meta WHERE igdb_id=?", (primary,)).fetchone()
          if primary else None)
    mc.close()
    pname = json.loads(pm[0]).get("name") if pm and pm[0] else None
    print("=== %r  primary=%s  name=%r  platforms=%s"
          % (nk, primary, pname, [e["platform"] for e in ents]))
    if not (primary and pname and ents):
        print("  (skip: not identified at title level / no emulation entries)")
        return
    cands = candidates(pname, nk)
    for c in cands:
        print("  candidate id=%s %r (%s) platforms=%s"
              % (c["id"], c["name"], c["year"], [p["name"] for p in c["platforms"]]))
    plan = igdb_enrich.plan_title(nk, primary, ents, cands,
                                  adjudicate=lambda items: ai.adjudicate_entry(items))
    for pe in plan:
        flag = "  <-- CHANGE" if (pe["action"] == "detach"
                                  or (pe["action"] == "set" and pe["igdb_id"] != primary)) else ""
        print("  %-18s kind=%-14s action=%-6s igdb=%s%s"
              % (pe["platform"], pe["kind"], pe["action"], pe["igdb_id"], flag))


if __name__ == "__main__":
    for nk in sys.argv[1:]:
        try:
            run(nk)
        except Exception as e:
            print("  ERROR on %r: %s" % (nk, e))
