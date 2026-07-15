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
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config
import igdb
from titlenorm import norm

CACHE = os.path.join(DATA, "metadata-cache.sqlite")
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


def _live_token():
    """(client_id, token) for live IGDB calls, or (None, None) when IGDB is
    disabled / uncredentialed. Reuses the cached OAuth token."""
    if not config.metadata_enabled("igdb"):
        return None, None
    cid, csec = config.igdb_creds()
    if not (cid and csec):
        return None, None
    con = cache_con()
    try:
        return cid, token(con, cid, csec)
    finally:
        con.close()


def _has_release_fields(g):
    return bool(g and (g.get("platforms") or g.get("release_dates")))


def releases_for(norm_key, allow_fetch=True):
    """A game's cross-platform release list from IGDB, cache-first + self-healing.
    Older cached records (fetched before platforms/release_dates were requested) are
    transparently re-fetched once. Returns
    {resolved, igdb_id, name, releases:[{id,name,abbr,year,human}], source}."""
    con = cache_con()
    try:
        row = con.execute("SELECT igdb_id FROM igdb_resolution WHERE norm_key=?",
                          (norm_key,)).fetchone()
        iid = row[0] if row else 0
        if not iid:
            return {"resolved": False, "releases": []}
        m = con.execute("SELECT payload_json FROM igdb_meta WHERE igdb_id=?",
                        (iid,)).fetchone()
        g = json.loads(m[0]) if m and m[0] else None
        source = "cache"
        if allow_fetch and not _has_release_fields(g):
            cid, tok = _live_token()
            if cid:
                body = "fields %s; where id = %d; limit 1;" % (igdb.GAME_FIELDS, iid)
                hits = igdb.query("games", body, cid, tok)
                if hits:
                    g = hits[0]
                    con.execute("INSERT OR REPLACE INTO igdb_meta"
                                "(igdb_id,payload_json,fetched_at) VALUES(?,?,?)",
                                (iid, json.dumps(g, ensure_ascii=False),
                                 int(time.time())))
                    con.commit()
                    source = "live"
        if not g:
            return {"resolved": True, "igdb_id": iid, "releases": [], "source": None}
        return {"resolved": True, "igdb_id": iid, "name": g.get("name"),
                "releases": igdb.releases(g), "source": source}
    finally:
        con.close()


def all_platforms(allow_fetch=True):
    """The full IGDB platform catalog (fetched once, then cached) as
    [{id,name,abbr}] — powers the ownership overlay's 'search any system'."""
    con = cache_con()
    try:
        con.execute("CREATE TABLE IF NOT EXISTS igdb_platforms("
                    "igdb_id INTEGER PRIMARY KEY, name TEXT, abbreviation TEXT,"
                    " fetched_at INTEGER)")
        con.commit()
        rows = con.execute("SELECT name, abbreviation FROM igdb_platforms").fetchall()
        if not rows and allow_fetch:
            cid, tok = _live_token()
            if cid:
                off, now = 0, int(time.time())
                while True:
                    hits = igdb.query(
                        "platforms",
                        "fields id,name,abbreviation; limit 500; offset %d;" % off,
                        cid, tok)
                    for p in hits:
                        con.execute("INSERT OR REPLACE INTO igdb_platforms"
                                    "(igdb_id,name,abbreviation,fetched_at) "
                                    "VALUES(?,?,?,?)", (p["id"], p.get("name"),
                                                        p.get("abbreviation"), now))
                    off += 500
                    if len(hits) < 500:
                        break
                con.commit()
                rows = con.execute(
                    "SELECT name, abbreviation FROM igdb_platforms").fetchall()
        seen, out = set(), []
        for n, a in rows:
            e = igdb.platform_entry({"name": n, "abbreviation": a})
            if e and e["id"] not in seen:
                seen.add(e["id"])
                out.append(e)
        return sorted(out, key=lambda e: e["name"].lower())
    finally:
        con.close()


def main(argv):
    do_all = "--all" in argv
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None

    if not config.metadata_enabled("igdb"):
        print("igdb: disabled (config.py enable igdb)", file=sys.stderr)
        return
    cid, csec = config.igdb_creds()
    if not (cid and csec):
        print("igdb: no Twitch credentials — set igdb_client_id/igdb_client_secret. "
              "See README. Skipping.", file=sys.stderr)
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
    # Wishlist-WANTED Steam games carry their appid in the `wanted` table (they
    # have no owned source row) — use it so wanted items resolve by appid too,
    # not just by name-search.
    try:
        for nk, sid in lib.execute(
                "SELECT g.norm_key, w.store_id FROM games g JOIN wanted w "
                "ON w.game_id=g.id WHERE w.store='steam'"):
            if nk in games and sid and str(sid).isdigit() and not games[nk]["appid"]:
                games[nk]["appid"] = str(sid)
    except sqlite3.OperationalError:
        pass                                # older catalog without a wanted table
    # --source <id> (repeatable): restrict the worklist to games owned OR wanted
    # via those store sources. A store's Full refresh scopes IGDB to that store's
    # games instead of re-resolving the whole (ROM-dominated) catalog.
    srcs = [argv[i + 1] for i, a in enumerate(argv)
            if a == "--source" and i + 1 < len(argv)]
    if srcs:
        ph = ",".join("?" * len(srcs))
        keep = {nk for (nk,) in lib.execute(
            "SELECT DISTINCT g.norm_key FROM games g JOIN sources s "
            "ON s.game_id=g.id WHERE s.source IN (%s)" % ph, srcs)}
        try:                                # include wishlist-wanted games for the store
            keep |= {nk for (nk,) in lib.execute(
                "SELECT DISTINCT g.norm_key FROM games g JOIN wanted w "
                "ON w.game_id=g.id WHERE w.store IN (%s)" % ph, srcs)}
        except sqlite3.OperationalError:
            pass
        games = {nk: v for nk, v in games.items() if nk in keep}
        print("igdb: scoped to %s (owned+wanted) -> %d games"
              % (",".join(srcs), len(games)), file=sys.stderr)
    lib.close()

    resolved = {}                       # norm_key -> igdb_id (found, >0)
    failed_at = {}                      # norm_key -> when a name-search last failed (id=0)
    if not do_all:
        for nk, iid, rat in con.execute(
                "SELECT norm_key, igdb_id, resolved_at FROM igdb_resolution"):
            if iid and iid > 0:
                resolved[nk] = iid
            else:
                failed_at[nk] = rat or 0
    # Re-attempt previously-FAILED matches so a normal ("New") sync keeps trying
    # to fill unmatched games — picking up titles IGDB has since added. Default 0
    # = retry every run (a name search is one cheap request; failures are few);
    # set igdb_fail_retry_days>0 to only retry misses older than N days if IGDB
    # call volume ever matters. --all (Full) re-resolves everything regardless.
    fail_ttl = int(config.get("igdb_fail_retry_days") or 0) * 86400
    todo = [nk for nk in games
            if nk not in resolved
            and (nk not in failed_at or now - failed_at[nk] >= fail_ttl)]
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
        # Accept ONLY an exact normalized-title match. We used to fall back to IGDB's
        # top fuzzy hit when nothing matched, but `search` ranks by relevance and would
        # bind e.g. "gods" (Bitmap Bros, 1991) -> "God of War Ragnarök", renaming the
        # ROM and poisoning its score/metadata. norm() already folds case, punctuation,
        # articles, roman numerals, &/+, and editions, so an exact match still catches
        # real spelling variants; a genuine miss stays unmatched (keeps its filename
        # title) rather than adopting a wrong game. (User decision 2026-07-15.)
        for h in hits:
            if norm(h.get("name", "")) == nk:
                iid, slug = h["id"], h.get("slug")
                break
        con.execute("INSERT OR REPLACE INTO igdb_resolution"
                    "(norm_key,igdb_id,slug,matched_by,resolved_at) "
                    "VALUES(?,?,?,?,?)",
                    (nk, iid or 0, slug, "name" if iid else "none", now))
        if iid:
            resolved[nk] = iid
        print("PROG\t%d\t%d\t%s\tresolve" % (n, len(remaining), nk), flush=True)
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
        print("PROG\t%d\t%d\t\tfetch" % (min(i + 200, len(need)), len(need)), flush=True)
        print("igdb: fetched %d/%d" % (min(i + 200, len(need)), len(need)),
              file=sys.stderr)
    con.close()
    print("igdb: done.", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
