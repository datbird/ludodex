#!/usr/bin/env python3
"""Add REMOTE media references to media-index.sqlite (keyed by norm_key).

Complements media_index.py (local ES-DE / Steam-grid scan) with remote art,
stored as URL references (ref_type='url') — bytes are only pulled later for the
CHOSEN asset (media_choose.py), per the hybrid model.

Providers (in increasing cost; later ones gap-fill what earlier ones lack):
  * steam       — Steam store CDN by appid (library_600x900 / hero / logo). No
                  auth, instant; URLs are well-known and verified lazily on use.
  * igdb        — IGDB cover/artwork/screenshot images, by IGDB id. Reuses the
                  resolutions igdb_enrich.py already cached (metadata-cache.sqlite
                  igdb_resolution) — only a light by-id image fetch, no searches.
  * steamgriddb — community grids/heroes/logos/icons. API key required; THROTTLED,
                  so it only targets games still missing a kind (gap-fill), capped.

  python3 media_fetch.py                       # steam + igdb (cheap)
  python3 media_fetch.py --provider steam
  python3 media_fetch.py --steamgriddb [--limit N]   # gap-fill (opt-in, slow)
"""
import os
import sqlite3
import sys
import time
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config

INDEX = os.path.join(DIR, "media-index.sqlite")
META_CACHE = os.path.join(DIR, "metadata-cache.sqlite")

# Steam store CDN. library_600x900 (portrait cover), library_hero (wide hero),
# header.jpg (capsule/header banner), logo. Not every appid has every asset —
# verified lazily when materialized.
STEAM_CDN = "https://steamcdn-a.akamaihd.net/steam/apps/%s/%s"
STEAM_ART = {"cover": "library_600x900.jpg", "hero": "library_hero.jpg",
             "header": "header.jpg", "logo": "logo.png"}

# IGDB image sizes per canonical kind (https://api-docs.igdb.com/#images).
IGDB_SIZE = {"cover": "t_cover_big", "background": "t_1080p",
             "screenshot": "t_screenshot_huge"}
IGDB_IMG = "https://images.igdb.com/igdb/image/upload/%s/%s.jpg"

SGDB = "https://www.steamgriddb.com/api/v2"


def con_index():
    con = sqlite3.connect(INDEX)
    con.execute("""CREATE TABLE IF NOT EXISTS media(
      id INTEGER PRIMARY KEY, norm_key TEXT NOT NULL, system TEXT,
      kind TEXT NOT NULL, provider TEXT NOT NULL, mount TEXT,
      ref_type TEXT NOT NULL, ref TEXT NOT NULL, ext TEXT, sha1 TEXT,
      width INTEGER, height INTEGER, chosen INTEGER DEFAULT 0,
      matched INTEGER DEFAULT 0, meta TEXT, indexed_at INTEGER,
      UNIQUE(provider, kind, ref))""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_media_nk ON media(norm_key)")
    return con


def steam_games():
    """{appid -> norm_key} for owned Steam games."""
    lib = config.get("library_db")
    out = {}
    if not (lib and os.path.exists(lib)):
        return out
    c = sqlite3.connect(lib)
    for nk, sid in c.execute(
            "SELECT g.norm_key, s.source_id FROM games g JOIN sources s "
            "ON s.game_id=g.id WHERE s.source='steam'"):
        if sid and str(sid).isdigit():
            out[str(sid)] = nk
    c.close()
    return out


def put(con, nk, kind, provider, url, now, ext="jpg", system=None, meta=None):
    con.execute("INSERT OR REPLACE INTO media(norm_key,system,kind,provider,"
                "ref_type,ref,ext,matched,meta,indexed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (nk, system, kind, provider, "url", url, ext, 1, meta, now))


# --------------------------------------------------------------------------- #
def fetch_steam(con, now):
    games = steam_games()
    n = 0
    for appid, nk in games.items():
        for kind, leaf in STEAM_ART.items():
            put(con, nk, kind, "steam", STEAM_CDN % (appid, leaf), now,
                ext=leaf.rsplit(".", 1)[-1], meta=appid)
            n += 1
    con.commit()
    print("media_fetch: steam — %d candidate URLs for %d games"
          % (n, len(games)), file=sys.stderr)


def fetch_igdb(con, now):
    if not os.path.exists(META_CACHE):
        print("media_fetch: igdb — no metadata-cache yet (run igdb_enrich.py)",
              file=sys.stderr)
        return
    cid, csec = config.igdb_creds()
    if not (cid and csec):
        print("media_fetch: igdb — no Twitch creds; skipping", file=sys.stderr)
        return
    import igdb
    mc = sqlite3.connect(META_CACHE)
    res = {iid: nk for nk, iid in mc.execute(
        "SELECT norm_key, igdb_id FROM igdb_resolution WHERE igdb_id>0")}
    mc.close()
    if not res:
        print("media_fetch: igdb — no resolutions cached yet", file=sys.stderr)
        return
    tok, _ = igdb.get_token(cid, csec)
    ids = sorted(res)
    n = 0
    for i in range(0, len(ids), 200):
        batch = ids[i:i + 200]
        body = ("fields id,cover.image_id,artworks.image_id,"
                "screenshots.image_id; where id=(%s); limit 500;"
                % ",".join(str(x) for x in batch))
        for g in igdb.query("games", body, cid, tok):
            nk = res.get(g["id"])
            if not nk:
                continue
            cov = (g.get("cover") or {}).get("image_id")
            if cov:
                put(con, nk, "cover", "igdb",
                    IGDB_IMG % (IGDB_SIZE["cover"], cov), now, meta=str(g["id"]))
                n += 1
            for art in (g.get("artworks") or [])[:1]:
                if art.get("image_id"):
                    put(con, nk, "background", "igdb",
                        IGDB_IMG % (IGDB_SIZE["background"], art["image_id"]),
                        now, meta=str(g["id"]))
                    n += 1
            for sh in (g.get("screenshots") or [])[:3]:
                if sh.get("image_id"):
                    put(con, nk, "screenshot", "igdb",
                        IGDB_IMG % (IGDB_SIZE["screenshot"], sh["image_id"]),
                        now, meta=str(g["id"]))
                    n += 1
        con.commit()
    print("media_fetch: igdb — %d image URLs from %d resolved games"
          % (n, len(res)), file=sys.stderr)


def _sgdb_get(path, key):
    req = urllib.request.Request(SGDB + path,
                                 headers={"Authorization": "Bearer " + key,
                                          "User-Agent": "ludodex"})
    import json
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_steamgriddb(con, now, limit=None):
    key = config.steamgriddb_key()
    if not key:
        print("media_fetch: steamgriddb — no API key; skipping", file=sys.stderr)
        return
    # gap targets: owned steam games missing a chosen-eligible kind locally/remote
    have = {(nk, k) for nk, k in con.execute(
        "SELECT norm_key, kind FROM media WHERE kind IN "
        "('cover','hero','logo','icon')")}
    games = steam_games()
    todo = [(a, nk) for a, nk in games.items()
            if any((nk, k) not in have for k in ("cover", "hero", "logo"))]
    if limit:
        todo = todo[:limit]
    KINDS = {"grids": ("cover", "600x900"), "heroes": ("hero", None),
             "logos": ("logo", None), "icons": ("icon", None)}
    n = 0
    for appid, nk in todo:
        try:
            g = _sgdb_get("/games/steam/%s" % appid, key)
            gid = (g.get("data") or {}).get("id")
            if not gid:
                continue
            for ep, (kind, dim) in KINDS.items():
                q = "/%s/game/%s" % (ep, gid) + ("?dimensions=%s" % dim if dim else "")
                try:
                    d = _sgdb_get(q, key)
                except Exception:
                    continue
                items = d.get("data") or []
                if items and items[0].get("url"):
                    url = items[0]["url"]
                    put(con, nk, kind, "steamgriddb", url, now,
                        ext=url.rsplit(".", 1)[-1].split("?")[0], meta=str(gid))
                    n += 1
                time.sleep(0.2)
        except Exception as e:
            print("media_fetch: steamgriddb appid %s: %s" % (appid, e),
                  file=sys.stderr)
        con.commit()
    print("media_fetch: steamgriddb — %d URLs across %d gap games"
          % (n, len(todo)), file=sys.stderr)


def fetch_screenscraper(con, now):
    """Ingest media URLs from the local ScreenScraper cache (no API calls — they
    came free with each metadata scrape). Stored as URL refs; downloading them
    later appends auth via screenscraper.media_url_with_auth."""
    cache = os.path.join(DIR, "screenscraper-cache.sqlite")
    if not os.path.exists(cache):
        print("media_fetch: screenscraper — no cache yet (run ss_scrape.py)",
              file=sys.stderr)
        return
    import json
    import screenscraper as ss
    sc = sqlite3.connect(cache)
    try:
        rows = sc.execute("SELECT norm_key, system, payload_json FROM ss_game "
                          "WHERE status='ok' AND payload_json IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        rows = []
    sc.close()
    n = 0
    for nk, system, payload in rows:
        try:
            jeu = json.loads(payload)
        except (ValueError, TypeError):
            continue
        for m in ss.extract_media(jeu):
            put(con, nk, m["kind"], "screenscraper", m["url"], now,
                ext=(m.get("format") or "jpg"), system=system, meta=m.get("type"))
            n += 1
    con.commit()
    print("media_fetch: screenscraper — %d media refs from %d scraped games"
          % (n, len(rows)), file=sys.stderr)


def main(argv):
    only = argv[argv.index("--provider") + 1] if "--provider" in argv else None
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    con = con_index()
    now = int(time.time())
    if only in (None, "steam") and config.media_enabled("steam"):
        con.execute("DELETE FROM media WHERE provider='steam'")
        fetch_steam(con, now)
    if only in (None, "igdb") and config.media_enabled("igdb"):
        con.execute("DELETE FROM media WHERE provider='igdb'")
        fetch_igdb(con, now)
    if only in (None, "screenscraper") and \
            config.metadata_enabled("screenscraper") and \
            config.get_bool("screenscraper_media", True):
        con.execute("DELETE FROM media WHERE provider='screenscraper'")
        fetch_screenscraper(con, now)
    if (only == "steamgriddb" or "--steamgriddb" in argv) and \
            config.media_enabled("steamgriddb"):
        con.execute("DELETE FROM media WHERE provider='steamgriddb'")
        fetch_steamgriddb(con, now, limit)
    tot = con.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    con.commit()
    con.close()
    print("media_fetch: %d total assets in index" % tot, file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
