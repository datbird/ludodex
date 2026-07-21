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
import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.parse

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config

INDEX = os.path.join(DATA, "media-index.sqlite")
META_CACHE = os.path.join(DATA, "metadata-cache.sqlite")

# Steam store CDN. library_600x900 (portrait cover), library_hero (wide hero),
# header.jpg (capsule/header banner), logo. Not every appid has every asset —
# verified lazily when materialized.
STEAM_CDN = "https://steamcdn-a.akamaihd.net/steam/apps/%s/%s"
# Each kind lists every filename Steam is known to serve it under, tried in
# order — prune_dead drops whichever 404s, so older/newer games both resolve.
# The vertical cover capsule has two names: `library_600x900.jpg` (modern,
# 2019+) and the legacy `portrait.png` — older titles (e.g. pre-2019 indies)
# only have the latter, so requesting just the modern name silently loses them.
STEAM_ART = {"cover": ["library_600x900.jpg", "portrait.png"],
             "hero": ["library_hero.jpg"],
             # older titles lack `library_hero.jpg`; Steam still serves a wide
             # store background under these generated names (37/40 of hero-less
             # games have `page_bg_generated_v6b.jpg`). Modeled as `background`
             # so it fills the detail backdrop without shadowing a real hero.
             "background": ["page_bg_generated_v6b.jpg",
                            "page_bg_generated.jpg", "page.bg.jpg"],
             "header": ["header.jpg"], "logo": ["logo.png"]}

# IGDB image sizes per canonical kind (https://api-docs.igdb.com/#images).
IGDB_SIZE = {"cover": "t_cover_big", "background": "t_1080p",
             "screenshot": "t_screenshot_huge"}
IGDB_IMG = "https://images.igdb.com/igdb/image/upload/%s/%s.jpg"

SGDB = "https://www.steamgriddb.com/api/v2"


# --- resolved-identity key (DESIGN §11.9) ------------------------------------ #
# Every media row carries a `game_key`: the identity the art belongs to, so the
# serve path matches art to a game instead of guessing from title + system. Two
# namespaces, provider-qualified so they can never collide:
#   * identified   -> "igdb:<id>"      (the game the title resolved to)
#   * unidentified -> "title:<norm_key>"  (no console suffix — a resolved title's
#     neutral art is always igdb:<id>, so an era-collision entry, whose game_key is
#     title:<nk>, never matches it and correctly forfeits it; an UNidentified game's
#     own neutral art is title:<nk> and matches its title:<nk> entry, so it shows.)
# Media identity is title-level here (keyed off igdb_resolution, which is
# norm_key-keyed). The per-ENTRY decision — a stray retro-handheld port ADOPTS its
# parent's igdb identity, an era-collision gets its own title identity — lives in
# build_library (Phase 2); the two meet at serve time on a plain game_key match.
_RESMAP = None


def invalidate_resmap():
    """Drop the cached resolution map.

    MUST be called whenever an identity changes inside a long-running process. As a CLI
    script the cache was harmless (one process, one pass), but the server imports this
    module and lives for days: after the first fetch, a wand pin/detach/re-identify would
    fetch the new art and then stamp it with the STALE game_key, and the media serve-gate
    would hide precisely the cover the wand just fetched — the "wand says done, cover
    doesn't appear" failure the one-operation contract forbids."""
    global _RESMAP
    _RESMAP = None


def _resmap():
    """Cached {norm_key -> igdb_id} for resolved games (igdb_id>0). Empty if the
    metadata cache is absent or unpopulated (fresh install / IGDB disabled)."""
    global _RESMAP
    if _RESMAP is None:
        _RESMAP = {}
        if os.path.exists(META_CACHE):
            try:
                mc = sqlite3.connect(META_CACHE)
                _RESMAP = {nk: iid for nk, iid in mc.execute(
                    "SELECT norm_key, igdb_id FROM igdb_resolution WHERE igdb_id>0")}
                mc.close()
            except sqlite3.OperationalError:
                _RESMAP = {}                    # cache exists but enrich hasn't run
    return _RESMAP


def game_key(nk, system=None):
    """The identity key for media on title `nk` (the `system` arg is kept for call
    compatibility but no longer part of the key — see the namespace note above)."""
    iid = _resmap().get(nk)
    return "igdb:%s" % iid if iid else "title:%s" % nk


def _backfill_game_key(con):
    """One-time (idempotent) fill of game_key for rows that predate the column or
    came from a producer that doesn't stamp yet (media_index / importers). Cheap
    no-op once every row is stamped — guarded by an existence check. Identified
    rows take the igdb key (SQL join to the metadata cache); the rest fall to the
    title bucket. Never fails a fetch run: any error leaves game_key NULL (safe —
    nothing reads it in Phase 1) and the next run retries."""
    try:
        # normalize any legacy suffixed title keys (title:<nk>@<system>, written by an
        # earlier build) to the suffix-free scheme so entry/media title keys align.
        if con.execute("SELECT 1 FROM media WHERE game_key LIKE 'title:%@%' "
                       "LIMIT 1").fetchone():
            con.execute("UPDATE media SET game_key='title:'||norm_key "
                        "WHERE game_key LIKE 'title:%@%'")
            con.commit()
        if not con.execute("SELECT 1 FROM media WHERE game_key IS NULL "
                           "OR game_key='' LIMIT 1").fetchone():
            return                              # already fully stamped
        if os.path.exists(META_CACHE):
            try:
                con.execute("ATTACH ? AS mc", (META_CACHE,))
                con.execute(
                    "UPDATE media SET game_key='igdb:'||"
                    "(SELECT r.igdb_id FROM mc.igdb_resolution r "
                    " WHERE r.norm_key=media.norm_key AND r.igdb_id>0) "
                    "WHERE (game_key IS NULL OR game_key='') AND norm_key IN "
                    "(SELECT norm_key FROM mc.igdb_resolution WHERE igdb_id>0)")
                con.commit()                    # release mc read-lock before DETACH
            finally:
                try:
                    con.execute("DETACH mc")
                except sqlite3.OperationalError:
                    pass
        con.execute(
            "UPDATE media SET game_key='title:'||norm_key "
            "WHERE game_key IS NULL OR game_key=''")
        con.commit()
    except sqlite3.OperationalError as e:
        print("media_fetch: game_key backfill deferred: %s" % str(e)[:120],
              file=sys.stderr)


def con_index():
    con = sqlite3.connect(INDEX)
    con.execute("PRAGMA busy_timeout=30000")   # wait out the live server's locks
    con.execute("""CREATE TABLE IF NOT EXISTS media(
      id INTEGER PRIMARY KEY, norm_key TEXT NOT NULL, system TEXT,
      kind TEXT NOT NULL, provider TEXT NOT NULL, mount TEXT,
      ref_type TEXT NOT NULL, ref TEXT NOT NULL, ext TEXT, sha1 TEXT,
      width INTEGER, height INTEGER, chosen INTEGER DEFAULT 0,
      matched INTEGER DEFAULT 0, meta TEXT, indexed_at INTEGER,
      hidden INTEGER DEFAULT 0, game_key TEXT,
      UNIQUE(provider, kind, ref))""")
    _cols = {r[1] for r in con.execute("PRAGMA table_info(media)")}
    if "hidden" not in _cols:
        con.execute("ALTER TABLE media ADD COLUMN hidden INTEGER DEFAULT 0")
    # DESIGN §11.9 — resolved-identity key for media (Phase 1: add + backfill + stamp
    # on fetch; NOT yet read by the serve path, so this stays invisible until Phase 3).
    if "game_key" not in _cols:
        con.execute("ALTER TABLE media ADD COLUMN game_key TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS ix_media_nk ON media(norm_key)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_media_gk ON media(game_key, system, kind)")
    _backfill_game_key(con)
    return con


def steam_games():
    """{appid -> norm_key} for Steam games — OWNED plus wishlist-WANTED. Both carry
    a Steam appid (wanted ones live in the `wanted` table, with no owned source), so
    both get free Steam-CDN cover art by appid."""
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
    try:                                    # wishlist-wanted Steam appids
        for nk, sid in c.execute(
                "SELECT g.norm_key, w.store_id FROM games g JOIN wanted w "
                "ON w.game_id=g.id WHERE w.store='steam'"):
            if sid and str(sid).isdigit():
                out.setdefault(str(sid), nk)
    except sqlite3.OperationalError:
        pass                                # older catalog without the wanted table
    c.close()
    return out


_BANNED = None


def _banned():
    """Cached set of banned (nk, kind, provider, ref) — never (re)download these."""
    global _BANNED
    if _BANNED is None:
        try:
            import mediaflags
            _BANNED = mediaflags.banned_set()
        except Exception:
            _BANNED = set()
    return _BANNED


def put(con, nk, kind, provider, url, now, ext="jpg", system=None, meta=None,
        attrs=None, gkey=None):
    if (nk, kind, provider, url) in _banned():
        return                              # user banned this asset — don't re-add
    if attrs:                               # structured per-asset metadata -> JSON meta
        meta = json.dumps({k: v for k, v in attrs.items() if v not in (None, "")},
                          separators=(",", ":"))
    # gkey pins an explicit identity — used for per-entry same-title splits (a ROM entry
    # whose igdb id differs from the title-level resolution, DESIGN §11.9); default keys
    # the row by the title-level resolution via game_key(nk).
    # ON CONFLICT ... DO UPDATE, never INSERT OR REPLACE: the unique key is
    # (provider, kind, ref), and REPLACE deletes the existing row — silently dropping its
    # `sha1`, which is the pointer to the ALREADY-DOWNLOADED bytes in the media repo. That
    # made every re-fetch of an unchanged asset re-download it. The ref is identical (it's
    # part of the conflict key), so the bytes are still valid: keep the sha1 and refresh
    # only the metadata that can actually change.
    con.execute("INSERT INTO media(norm_key,system,kind,provider,"
                "ref_type,ref,ext,matched,meta,indexed_at,game_key) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(provider,kind,ref) DO UPDATE SET "
                "norm_key=excluded.norm_key, system=excluded.system, ext=excluded.ext, "
                "matched=excluded.matched, meta=excluded.meta, "
                "indexed_at=excluded.indexed_at, game_key=excluded.game_key",
                (nk, system, kind, provider, "url", url, ext, 1, meta, now,
                 gkey or game_key(nk, system)))


# --------------------------------------------------------------------------- #
def fetch_steam(con, now):
    games = steam_games()
    n = 0
    for appid, nk in games.items():
        for kind, leaves in STEAM_ART.items():
            for leaf in leaves:
                put(con, nk, kind, "steam", STEAM_CDN % (appid, leaf), now,
                    ext=leaf.rsplit(".", 1)[-1], meta=appid)
                n += 1
    con.commit()
    print("media_fetch: steam — %d candidate URLs for %d games"
          % (n, len(games)), file=sys.stderr)


def fetch_igdb(con, now, only=None):
    """Fetch IGDB cover/artwork/screenshot URLs for resolved games. `only` (a set of
    norm_keys) scopes the fetch to specific games — the surgical per-game wand path;
    None = the whole catalog. Also fetches PER-ENTRY override ids (same-title splits,
    DESIGN §11.9) and stamps their rows with the override's own game_key, so a split
    ROM entry (the 1986 Portal) gets ITS game's art instead of the title-level (store)
    game's — otherwise its cover could never be sourced."""
    invalidate_resmap()   # identities may have changed since the last call
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
    res = {}                                # igdb_id -> [nk, ...] title-level resolutions.
    for nk, iid in mc.execute(              # a MULTIMAP: two different norm_keys can share an
            "SELECT norm_key, igdb_id FROM igdb_resolution WHERE igdb_id>0"):  # id (Intl
        if only is None or nk in only:      # Karate + as "international karate" AND "…plus"),
            res.setdefault(iid, []).append(nk)   # and BOTH must get the fetched art, not just
                                            # whichever won a dict slot (the media-loss bug).
    eres = {}                               # igdb_id -> [nk, ...] per-entry overrides
    try:
        for nk, iid in mc.execute(
                "SELECT norm_key, igdb_id FROM entry_resolution WHERE igdb_id>0"):
            if only is None or nk in only:
                eres.setdefault(iid, []).append(nk)
    except sqlite3.OperationalError:
        pass                                # no per-entry overrides table yet
    mc.close()
    if not res and not eres:
        print("media_fetch: igdb — no resolutions cached yet"
              + ("" if only is None else " for the requested games"), file=sys.stderr)
        return
    tok, _ = igdb.get_token(cid, csec)
    ids = sorted(set(res) | set(eres))

    def _emit(nk, g, gkey):
        c = 0
        cov = (g.get("cover") or {}).get("image_id")
        if cov:
            put(con, nk, "cover", "igdb", IGDB_IMG % (IGDB_SIZE["cover"], cov),
                now, meta=str(g["id"]), gkey=gkey)
            c += 1
        for art in (g.get("artworks") or [])[:1]:
            if art.get("image_id"):
                put(con, nk, "background", "igdb",
                    IGDB_IMG % (IGDB_SIZE["background"], art["image_id"]),
                    now, meta=str(g["id"]), gkey=gkey)
                c += 1
        for sh in (g.get("screenshots") or [])[:3]:
            if sh.get("image_id"):
                put(con, nk, "screenshot", "igdb",
                    IGDB_IMG % (IGDB_SIZE["screenshot"], sh["image_id"]),
                    now, meta=str(g["id"]), gkey=gkey)
                c += 1
        return c

    n = 0
    for i in range(0, len(ids), 200):
        batch = ids[i:i + 200]
        body = ("fields id,cover.image_id,artworks.image_id,"
                "screenshots.image_id; where id=(%s); limit 500;"
                % ",".join(str(x) for x in batch))
        for g in igdb.query("games", body, cid, tok):
            gid = g["id"]
            for nk in res.get(gid, ()):     # title-level: EVERY norm_key sharing this id
                n += _emit(nk, g, None)
            for nk in eres.get(gid, ()):    # per-entry override: pinned game_key
                n += _emit(nk, g, "igdb:%d" % gid)
        con.commit()
    print("media_fetch: igdb — %d image URLs from %d resolved + %d override entries"
          % (n, sum(len(v) for v in res.values()),
             sum(len(v) for v in eres.values())), file=sys.stderr)


def _sgdb_get(path, key):
    req = urllib.request.Request(SGDB + path,
                                 headers={"Authorization": "Bearer " + key,
                                          "User-Agent": "ludodex"})
    import json
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


SGDB_KINDS = {"grids": ("cover", "600x900"), "heroes": ("hero", None),
              "logos": ("logo", None), "icons": ("icon", None)}


def _sgdb_game_id(key, appid=None, title=None):
    """Resolve a SteamGridDB game id — by Steam appid (exact) when we have one,
    else by name search (best autocomplete match). None if nothing matches."""
    if appid:
        try:
            g = _sgdb_get("/games/steam/%s" % appid, key)
            gid = (g.get("data") or {}).get("id")
            if gid:
                return gid
        except Exception:
            pass
    if title:
        try:
            d = _sgdb_get("/search/autocomplete/%s"
                          % urllib.parse.quote(title.strip()), key)
            items = d.get("data") or []
            if items:
                return items[0].get("id")
        except Exception:
            pass
    return None


def fetch_steamgriddb_targets(con, now, targets, limit=None):
    """Gap-fill hero/logo/cover/icon from SteamGridDB for arbitrary identified
    games — NOT just Steam. `targets` = [(norm_key, title, steam_appid_or_None)];
    each resolves by appid or by title search. Returns the number of URLs added."""
    invalidate_resmap()   # identities may have changed since the last call
    key = config.steamgriddb_key()
    if not key:
        print("media_fetch: steamgriddb — no API key; skipping", file=sys.stderr)
        return 0
    have = {(nk, k) for nk, k in con.execute(
        "SELECT norm_key, kind FROM media WHERE kind IN "
        "('cover','hero','logo','icon')")}
    todo = [t for t in targets
            if any((t[0], k) not in have for k in ("cover", "hero", "logo"))]
    if limit:
        todo = todo[:limit]
    n = 0
    for nk, title, appid in todo:
        try:
            gid = _sgdb_game_id(key, appid, title)
            if not gid:
                continue
            for ep, (kind, dim) in SGDB_KINDS.items():
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
            print("media_fetch: steamgriddb %r: %s" % (title or nk, e), file=sys.stderr)
        con.commit()
    print("media_fetch: steamgriddb — %d URLs across %d gap games"
          % (n, len(todo)), file=sys.stderr)
    return n


def fetch_steamgriddb(con, now, limit=None):
    """CLI path: gap-fill SGDB art for all owned Steam games (resolved by appid)."""
    return fetch_steamgriddb_targets(
        con, now, [(nk, None, appid) for appid, nk in steam_games().items()], limit)


# Games that DESERVE art from a name lookup: a provider match or a real store /
# manual source — never a bare ROM file (its "title" is a filename; matched ROMs
# already carry their real canonical_title and are included via metadata_links).
_NONID_SRC = ("emulation", "archive", "physical", "rom", "digital")


def identified_targets():
    """[(norm_key, title, steam_appid_or_None)] for every IDENTIFIED game — used
    to gap-fill art by name (or Steam appid) when the importing source shipped
    none. This is what gets a non-Steam store game (Epic/GOG/PSN/Xbox) a cover
    and backdrop even though its source has no art CDN we fetch."""
    lib = config.get("library_db")
    out = []
    if not (lib and os.path.exists(lib)):
        return out
    c = sqlite3.connect(lib)
    try:
        rows = c.execute(
            "SELECT g.norm_key, g.canonical_title, "
            "  (SELECT s.source_id FROM sources s WHERE s.game_id=g.id "
            "     AND s.source='steam' LIMIT 1) AS appid "
            "FROM games g WHERE "
            "  EXISTS(SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id) "
            "  OR EXISTS(SELECT 1 FROM sources s WHERE s.game_id=g.id "
            "     AND s.source NOT IN %s)" % (_NONID_SRC,)).fetchall()
    finally:
        c.close()
    for nk, title, appid in rows:
        aid = str(appid) if appid and str(appid).isdigit() else None
        out.append((nk, title or "", aid))
    return out


def art_less_keys(con, kinds=("cover", "hero", "logo", "background")):
    """Identified games that have NO art at all of the given kinds. The gap-fill set —
    keeping the sync's art step proportional to what's missing instead of the catalog."""
    have = {nk for (nk,) in con.execute(
        "SELECT DISTINCT norm_key FROM media WHERE kind IN (%s)"
        % ",".join("?" * len(kinds)), tuple(kinds))}
    return [t for t in identified_targets() if t[0] not in have]


def fetch_missing_art(con, now, limit=None):
    """Built-in art gap-fill for every identified game still missing art.

    IGDB first — once a game is matched its art costs nothing extra and no API key, and it
    covers the stores with no art CDN of their own (Epic/GOG/PSN/Xbox). SteamGridDB then
    fills whatever IGDB couldn't, if a key is configured.

    INCREMENTAL BY CONSTRUCTION: both passes are scoped to games that currently have no art,
    so a routine sync costs API calls only for newly-imported art-less games. This is
    deliberately NOT `fetch_igdb(con, now)` with no scope — that refetches the whole catalog
    and, combined with put()'s upsert, used to reset every sha1 and re-download the bytes."""
    invalidate_resmap()   # identities may have changed since the last call
    targets = art_less_keys(con)
    n = 0
    if targets and config.media_enabled("igdb"):
        only = {t[0] for t in targets}
        try:
            n += fetch_igdb(con, now, only=only) or 0
        except Exception as e:
            print("media_fetch: backfill-art igdb — %s" % str(e)[:150], file=sys.stderr)
    if not config.steamgriddb_key():
        print("media_fetch: backfill-art — %d art-less game(s); IGDB filled %d, "
              "no SteamGridDB key so skipping that pass" % (len(targets), n),
              file=sys.stderr)
        return n
    still = art_less_keys(con)          # re-read: IGDB may have covered some
    return n + (fetch_steamgriddb_targets(con, now, still, limit) or 0)


def fetch_screenscraper(con, now, only=None):
    """Ingest media URLs from the local ScreenScraper cache (no API calls — they
    came free with each metadata scrape). Stored as URL refs; downloading them
    later appends auth via screenscraper.media_url_with_auth. `only` = a set of
    norm_keys to restrict to (scoped reconcile after a wand apply); None = all."""
    invalidate_resmap()   # identities may have changed since the last call
    cache = os.path.join(DATA, "screenscraper-cache.sqlite")
    if not os.path.exists(cache):
        print("media_fetch: screenscraper — no cache yet (run ss_scrape.py)",
              file=sys.stderr)
        return
    import json
    import screenscraper as ss
    sc = sqlite3.connect(cache)
    q = ("SELECT norm_key, system, payload_json FROM ss_game "
         "WHERE status='ok' AND payload_json IS NOT NULL")
    params = ()
    if only:
        q += " AND norm_key IN (%s)" % ",".join("?" * len(only))
        params = tuple(only)
    try:
        rows = sc.execute(q, params).fetchall()
    except sqlite3.OperationalError:
        rows = []
    sc.close()
    # scoped reconcile replaces just these games' SS refs so re-runs don't pile up
    if only and rows:
        con.executemany("DELETE FROM media WHERE provider='screenscraper' AND norm_key=?",
                        [(nk,) for nk in only])
    n = 0
    for nk, system, payload in rows:
        try:
            jeu = json.loads(payload)
        except (ValueError, TypeError):
            continue
        for m in ss.extract_media(jeu):
            put(con, nk, m["kind"], "screenscraper", m["url"], now,
                ext=(m.get("format") or "jpg"), system=system,
                attrs={"type": m.get("type"), "region": m.get("region"),
                       "format": m.get("format")})
            n += 1
    con.commit()
    print("media_fetch: screenscraper — %d media refs from %d scraped games"
          % (n, len(rows)), file=sys.stderr)


def prune_dead(con, workers=16):
    """HEAD-check un-materialized public URL refs and drop the definitively-dead
    ones (404/410). Candidate refs are recorded speculatively (esp. Steam CDN,
    which has no per-asset manifest — not every appid has every art type), so
    without this they'd render as blank cards. Cached (sha1) refs are already
    proven; screenscraper is skipped (its URLs need auth, so a bare HEAD lies)."""
    import urllib.error
    from concurrent.futures import ThreadPoolExecutor
    rows = con.execute(
        "SELECT id, ref FROM media WHERE ref_type='url' AND (sha1 IS NULL OR sha1='')"
        " AND provider IN ('steam','igdb','steamgriddb')").fetchall()

    def check(row):
        rid, url = row
        try:
            req = urllib.request.Request(url, method="HEAD")
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=15) as r:
                return rid, r.status
        except urllib.error.HTTPError as e:
            return rid, e.code
        except Exception:
            return rid, None                       # transient — keep the ref
    dead = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rid, st in ex.map(check, rows):
            if st in (404, 410):
                dead.append(rid)
    if dead:
        con.executemany("DELETE FROM media WHERE id=?", [(d,) for d in dead])
        con.commit()
    print("media_fetch: prune — checked %d url refs, removed %d dead"
          % (len(rows), len(dead)), file=sys.stderr)
    return len(dead)


def main(argv):
    only = argv[argv.index("--provider") + 1] if "--provider" in argv else None
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    con = con_index()
    now = int(time.time())
    if argv and argv[0] == "prune":               # standalone cleanup pass
        prune_dead(con)
        con.close()
        return
    if "--backfill-art" in argv:                  # name-based art gap-fill
        n = fetch_missing_art(con, now, limit)
        prune_dead(con)
        con.commit()
        con.close()
        print("media_fetch: backfill-art — added %d art URLs" % n, file=sys.stderr)
        return
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
    # after (re)fetching speculative URL candidates, drop the dead ones so they
    # never surface as blank media cards
    prune_dead(con)
    tot = con.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    con.commit()
    con.close()
    print("media_fetch: %d total assets in index" % tot, file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
