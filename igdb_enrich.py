#!/usr/bin/env python3
"""Resolve catalog games to IGDB and cache their metadata (igdb.com).

IGDB is a metadata PROVIDER, not a source. This reads the built catalog
(game-library.sqlite) for a worklist, resolves each game to an IGDB id
(by Steam appid via external_games, else by name search), fetches the record,
and caches everything in metadata-cache.sqlite. build_library.py then merges the
cache into game_attributes (FILL-GAPS — store/Playnite data always wins).

Production-grade caching: re-runs are cheap — only games not yet resolved and
records older than igdb_meta_ttl_days hit the network; resolutions persist.
Self-healing: --all ignores the caches and redoes everything.

  python3 igdb_enrich.py            # incremental (resolve new, refresh stale)
  python3 igdb_enrich.py --all      # ignore caches, re-resolve + re-fetch all
  python3 igdb_enrich.py --limit N  # cap name-searches this run (testing)
"""
import json
import os
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config
import igdb
from titlenorm import norm

CACHE = os.path.join(DIR, "metadata-cache.sqlite")
LIB = config.get("library_db")


def cache_con():
    con = sqlite3.connect(CACHE)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS igdb_token(client_id TEXT PRIMARY KEY,
      token TEXT, expires_at INTEGER);
    CREATE TABLE IF NOT EXISTS igdb_resolution(norm_key TEXT PRIMARY KEY,
      igdb_id INTEGER, slug TEXT, matched_by TEXT, resolved_at INTEGER);
    CREATE TABLE IF NOT EXISTS igdb_meta(igdb_id INTEGER PRIMARY KEY,
      payload_json TEXT, fetched_at INTEGER);
    """)
    return con


def token(con, cid, csec):
    now = int(time.time())
    row = con.execute("SELECT token,expires_at FROM igdb_token WHERE client_id=?",
                      (cid,)).fetchone()
    if row and row[1] - 60 > now:
        return row[0]
    tok, ttl = igdb.get_token(cid, csec)
    con.execute("INSERT INTO igdb_token(client_id,token,expires_at) VALUES(?,?,?) "
                "ON CONFLICT(client_id) DO UPDATE SET token=excluded.token, "
                "expires_at=excluded.expires_at", (cid, tok, now + ttl))
    con.commit()
    return tok


def main(argv):
    do_all = "--all" in argv
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None

    if not config.metadata_enabled("igdb"):
        print("igdb: disabled (config.py enable igdb)", file=sys.stderr)
        return
    cid, csec = config.igdb_creds()
    if not (cid and csec):
        print("igdb: no Twitch credentials — set igdb_client_id/igdb_client_secret "
              "(or igdb_op_item). See README. Skipping.", file=sys.stderr)
        return
    if not os.path.exists(LIB):
        print("igdb: no catalog yet — run build_library.py first.", file=sys.stderr)
        return

    con = cache_con()
    tok = token(con, cid, csec)
    ttl = int(config.get("igdb_meta_ttl_days") or 30) * 86400
    now = int(time.time())

    # ---- worklist: each game's norm_key, title, and a Steam appid if present ----
    lib = sqlite3.connect(LIB)
    games = {nk: {"title": title, "appid": None}
             for nk, title in lib.execute(
                 "SELECT norm_key, canonical_title FROM games")}
    for nk, sid in lib.execute(
            "SELECT g.norm_key, s.source_id FROM games g JOIN sources s "
            "ON s.game_id=g.id WHERE s.source='steam'"):
        if nk in games and sid and str(sid).isdigit():
            games[nk]["appid"] = str(sid)
    lib.close()

    resolved = {}                       # norm_key -> igdb_id (>0 = found)
    if not do_all:
        for nk, iid in con.execute(
                "SELECT norm_key, igdb_id FROM igdb_resolution"):
            resolved[nk] = iid
    todo = [nk for nk in games if nk not in resolved]
    print("igdb: %d games | %d already resolved | %d to resolve"
          % (len(games), len(resolved), len(todo)), file=sys.stderr)

    # ---- pass 1: resolve by Steam appid via external_games (batched) ----
    appid_map = {}                      # appid -> norm_key
    for nk in todo:
        a = games[nk]["appid"]
        if a:
            appid_map.setdefault(a, nk)
    appids = list(appid_map)
    for i in range(0, len(appids), 200):
        batch = appids[i:i + 200]
        uids = ",".join('"%s"' % a for a in batch)
        body = ("fields game,uid; where external_game_source = %d "
                "& uid = (%s); limit 500;" % (igdb.STEAM_SOURCE, uids))
        for row in igdb.query("external_games", body, cid, tok):
            nk, gid = appid_map.get(str(row.get("uid"))), row.get("game")
            if nk and gid:
                con.execute(
                    "INSERT OR REPLACE INTO igdb_resolution"
                    "(norm_key,igdb_id,slug,matched_by,resolved_at) "
                    "VALUES(?,?,?,?,?)", (nk, gid, None, "steam_appid", now))
                resolved[nk] = gid
        con.commit()
    if appids:
        print("igdb: resolved %d of %d games that had a Steam appid"
              % (sum(1 for a in appids if appid_map[a] in resolved), len(appids)),
              file=sys.stderr)

    # ---- pass 2: resolve the rest by name search (1 request each) ----
    remaining = [nk for nk in todo if nk not in resolved]
    if limit is not None:
        remaining = remaining[:limit]
    for n, nk in enumerate(remaining, 1):
        title = games[nk]["title"].replace('"', " ").strip()
        iid, slug = 0, None
        try:
            hits = igdb.query("games", 'search "%s"; fields id,name,slug; limit 8;'
                              % title, cid, tok)
        except Exception:               # one bad title shouldn't abort the run
            hits = []
        for h in hits:                  # prefer an exact normalized-title match
            if norm(h.get("name", "")) == nk:
                iid, slug = h["id"], h.get("slug")
                break
        if not iid and hits:            # else fall back to IGDB's top hit
            iid, slug = hits[0]["id"], hits[0].get("slug")
        con.execute("INSERT OR REPLACE INTO igdb_resolution"
                    "(norm_key,igdb_id,slug,matched_by,resolved_at) "
                    "VALUES(?,?,?,?,?)",
                    (nk, iid or 0, slug, "name" if iid else "none", now))
        if iid:
            resolved[nk] = iid
        if n % 50 == 0:
            con.commit()
            print("igdb: name-search %d/%d" % (n, len(remaining)), file=sys.stderr)
    con.commit()

    # ---- fetch metadata for resolved ids that are missing or stale ----
    want = sorted({iid for iid in resolved.values() if iid})
    have = {}
    if not do_all:
        for iid, ts in con.execute("SELECT igdb_id, fetched_at FROM igdb_meta"):
            have[iid] = ts
    need = [iid for iid in want if iid not in have or now - have[iid] > ttl]
    print("igdb: %d games linked to a record | fetching %d record(s)"
          % (len(want), len(need)), file=sys.stderr)
    for i in range(0, len(need), 200):
        batch = need[i:i + 200]
        body = ("fields %s; where id = (%s); limit 500;"
                % (igdb.GAME_FIELDS, ",".join(str(x) for x in batch)))
        for g in igdb.query("games", body, cid, tok):
            con.execute("INSERT OR REPLACE INTO igdb_meta"
                        "(igdb_id,payload_json,fetched_at) VALUES(?,?,?)",
                        (g["id"], json.dumps(g, ensure_ascii=False), now))
        con.commit()
        print("igdb: fetched %d/%d" % (min(i + 200, len(need)), len(need)),
              file=sys.stderr)
    con.close()
    print("igdb: done.", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
