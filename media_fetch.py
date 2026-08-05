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
import re
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
# Steam appdetails ATTRIBUTES cache (tiered ingest, Algo tier): genres/devs/pubs/
# release/description/type extracted from the same appdetails call the media pass
# already makes, so build_library can feed Steam's own data as a metadata provider
# (authoritative for Steam-owned games) — no extra network round-trip.
STEAM_META = os.path.join(DATA, "steam-meta.sqlite")

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
                # Mirror build_library's bundle refusal (game_type 3=bundle/13=pack):
                # an identity-refused entry's games row says `title:<nk>`, so its media
                # must be stamped the same or the serve-time game_key match fails and
                # the entry loses its art. The art itself still fetches — the bundle
                # record's images are the right images for the owned product.
                try:
                    _b = {r[0] for r in mc.execute(
                        "SELECT igdb_id FROM igdb_meta "
                        "WHERE json_extract(payload_json,'$.game_type') IN (3,13)")}
                    if _b:
                        _RESMAP = {nk: iid for nk, iid in _RESMAP.items()
                                   if iid not in _b}
                except sqlite3.OperationalError:
                    pass
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
    """Stamp + REPAIR game_key on media rows. Idempotent.

    Not merely a fill: two repair passes run before the fully-stamped early-return,
    because a row can be stamped and still WRONG. Identity changes after media is
    fetched (bundle refusal in one direction, a later match in the other), and a stale
    stamp makes the asset invisible rather than merely unlabelled.

    Original contract — fill of game_key for rows that predate the column or
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
        # Repair rows stamped with a BUNDLE identity before the refusal existed:
        # their entry now says `title:<nk>` (build_library refuses game_type 3/13),
        # so media keyed `igdb:<bundle>` would never match at serve time and the
        # entry loses its art. Runs BEFORE the fully-stamped early-return — these
        # rows are stamped, just wrongly.
        if os.path.exists(META_CACHE):
            try:
                con.execute("ATTACH ? AS mcb", (META_CACHE,))
                con.execute(
                    "UPDATE media SET game_key='title:'||norm_key WHERE game_key IN "
                    "(SELECT 'igdb:'||igdb_id FROM mcb.igdb_meta "
                    " WHERE json_extract(payload_json,'$.game_type') IN (3,13))")
                con.commit()
            except sqlite3.OperationalError:
                pass
            finally:
                try:
                    con.execute("DETACH mcb")
                except sqlite3.OperationalError:
                    pass
        # Repair NEUTRAL rows whose stamp disagrees with the entry's identity. Media is
        # stamped at fetch time; identity can arrive later (a wand match, a member
        # ingest, an accepted finding), and the entry's game_key moves while the media's
        # does not. Neutral art only serves when the two agree (DESIGN §11.9), so the
        # asset goes permanently invisible — a game can hold a 600x900 cover and render
        # a 264x352 one because the good row is keyed to an identity the entry no longer
        # has. Runs BEFORE the fully-stamped early-return: these rows are stamped, just
        # stale.
        #
        # This reads the CATALOG, not `igdb_resolution`. The previous version re-derived
        # identity here and applied its own policy — refusing anything IGDB calls a bundle
        # (game_type 3/13) — while the catalog was free to give an entry exactly such an
        # identity. Live, 41 entries carried `igdb:<bundle>` with all 990 of their neutral
        # media rows still on `title:<base_key>`: Halo MCC, Crash N. Sane Trilogy, the
        # Contra/Castlevania Anniversary Collections, the D&D and Forgotten Realms series.
        # Every one showed Screenshots 0 / Videos 0 / Manuals 0 while holding 15-40
        # screenshots, and still rendered a cover, because own-console art matches on
        # norm_key+system and never consults game_key. Two derivations of one fact, and
        # the fix is one fewer: the catalog decides, the stamp follows. The bundle refusal
        # needs no special case here — an entry that refused one simply reads back
        # `title:<nk>`.
        _lib = os.path.join(DATA, "game-library.sqlite")
        if os.path.exists(_lib):
            try:
                con.execute("ATTACH ? AS lgk", (_lib,))
                # Only where every entry sharing the base_key agrees: one stamp per
                # norm_key cannot satisfy two identities, and picking one would silently
                # hide the other entry's art (DESIGN §11.9 era splits). Ambiguous means
                # leave it, exactly like _member_identity refuses an ambiguous match.
                con.execute(
                    "UPDATE media SET game_key=("
                    "  SELECT MIN(g.game_key) FROM lgk.games g "
                    "  WHERE g.base_key=media.norm_key) "
                    "WHERE COALESCE(system,'')='' AND norm_key IN ("
                    "  SELECT base_key FROM lgk.games GROUP BY base_key "
                    "  HAVING COUNT(DISTINCT COALESCE(game_key,''))=1 "
                    "     AND MIN(COALESCE(game_key,''))!='') "
                    "AND COALESCE(game_key,'') != ("
                    "  SELECT MIN(g.game_key) FROM lgk.games g "
                    "  WHERE g.base_key=media.norm_key)")
                con.commit()
            except sqlite3.OperationalError as e:
                print("media_fetch: entry-derived game_key repair deferred: %s"
                      % str(e)[:120], file=sys.stderr)
            finally:
                try:
                    con.execute("DETACH lgk")
                except sqlite3.OperationalError:
                    pass
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
                    "(SELECT norm_key FROM mc.igdb_resolution WHERE igdb_id>0 "
                    " AND igdb_id NOT IN (SELECT igdb_id FROM mc.igdb_meta "
                    "  WHERE json_extract(payload_json,'$.game_type') IN (3,13)))")
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
    con.execute("PRAGMA journal_mode=WAL")     # readers never block the writer — avoids the
                                               # reader/writer deadlock two concurrent media
                                               # jobs hit in rollback mode ("database is locked")
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
def _steam_meta_con():
    c = sqlite3.connect(STEAM_META)
    c.execute("CREATE TABLE IF NOT EXISTS steam_meta("
              "appid TEXT PRIMARY KEY, norm_key TEXT, payload_json TEXT, fetched REAL)")
    # Identity facts live in COLUMNS, never in payload_json: build_library turns every
    # payload key into a game attribute, so a name/appid in there would become junk
    # metadata. store_name is Steam's own product title (the ownership API reports a
    # per-app label instead — "Ys I" for Ys I & II Chronicles+); canonical_appid is
    # appdetails' `steam_appid`, which resolves a sub-app to its parent product and is
    # how a store-granted bundle is recognised deterministically.
    have = {r[1] for r in c.execute("PRAGMA table_info(steam_meta)")}
    for col in ("store_name", "canonical_appid"):
        if col not in have:
            c.execute("ALTER TABLE steam_meta ADD COLUMN %s TEXT" % col)
    return c


def _extract_steam_attrs(data):
    """Deterministically map a Steam appdetails `data` block to ludodex attribute
    kinds (Algo tier — no AI). Genres/categories are lists; devs/pubs lists; a scalar
    release_year/release_date/description/content_type. Empty/missing → omitted."""
    out = {}
    genres = [g.get("description") for g in (data.get("genres") or []) if g.get("description")]
    if genres:
        out["genres"] = genres
    cats = [c.get("description") for c in (data.get("categories") or []) if c.get("description")]
    if cats:
        out["categories"] = cats
    if data.get("developers"):
        out["developers"] = list(data["developers"])
    if data.get("publishers"):
        out["publishers"] = list(data["publishers"])
    date = ((data.get("release_date") or {}).get("date") or "").strip()
    if date:
        out["release_date"] = date
        m = re.search(r"(19|20)\d{2}", date)
        if m:
            out["release_year"] = m.group(0)
    desc = (data.get("short_description") or "").strip()
    if desc:
        out["description"] = desc
    # Steam's own game/dlc/tool/… classification — feeds game-vs-utility (content_type).
    typ = (data.get("type") or "").strip()
    if typ:
        out["content_type"] = "Game" if typ == "game" else typ.capitalize()
    return out


def _put_steam_art(con, nk, appid, now):
    """Emit the constructed-CDN art candidates (cover/hero/background/header/logo)
    for one Steam appid. Idempotent via put()'s ON CONFLICT; prune_dead drops any
    leaf that 404s. Shared by fetch_steam (catalog-wide) and fetch_steam_media
    (per-game fold), so a Steam-media pass yields the COMPLETE set in one go."""
    n = 0
    for kind, leaves in STEAM_ART.items():
        for leaf in leaves:
            put(con, nk, kind, "steam", STEAM_CDN % (appid, leaf), now,
                ext=leaf.rsplit(".", 1)[-1], meta=appid)
            n += 1
    return n


def fetch_steam(con, now, only=None):
    games = steam_games()
    if only is not None:
        want = set(only)
        games = {a: nk for a, nk in games.items() if nk in want}
    n = 0
    for appid, nk in games.items():
        n += _put_steam_art(con, nk, appid, now)
    con.commit()
    print("media_fetch: steam — %d candidate URLs for %d games"
          % (n, len(games)), file=sys.stderr)


UA = "ludodex/1.0 (+https://github.com/datbird/ludodex)"
# PIN THE LANGUAGE. Without `l=`, Steam localises appdetails by the requesting IP, so
# the server's egress decides what language your catalog is in — live, this instance was
# getting Brazilian Portuguese, giving 3DMark a pt-BR description and the genre
# "Utilitários".
#
# That is not merely cosmetic. `NON_GAME_GENRES` is an English vocabulary, so a localised
# genre silently defeats the non-game filter: "Utilitários" is not "utilities", and every
# benchmark, wallpaper tool and video player stays visible no matter how the filter is
# configured. The attribute vocabulary, the genre rules and the AI prompts are all
# English, so the ingest language has to be too.
STEAM_APPDETAILS = ("https://store.steampowered.com/api/appdetails"
                    "?appids=%s&l=english&cc=us")
# Highest-quality trailer file, constructed from the movie id (appdetails stopped
# listing direct files, but this path still serves a real playable .webm).
STEAM_MOVIE = "https://cdn.akamai.steamstatic.com/steam/apps/%s/movie_max.webm"
# Steam's store API is unauthenticated but rate-limited (~200 req / 5 min / IP), so
# space the per-appid calls out — same cooldown os_fetch uses.
STEAM_STORE_COOLDOWN = float(os.environ.get("STEAM_STORE_COOLDOWN_MS", "1500")) / 1000.0


def _steam_media_seen(con):
    """appids whose appdetails media we've already pulled — the incremental gate, so a
    resync doesn't re-hit the (slow, rate-limited) store API for games already done."""
    con.execute("CREATE TABLE IF NOT EXISTS steam_media_seen("
                "appid TEXT PRIMARY KEY, fetched REAL)")
    return {r[0] for r in con.execute("SELECT appid FROM steam_media_seen")}


def _screenshot_limit():
    """Max screenshots to keep per game (config; 0 = no limit). Applies to every
    screenshot provider so a game's set is consistent regardless of source."""
    try:
        return max(0, int(config.get("screenshot_limit") or 0))
    except (TypeError, ValueError):
        return 0


def fetch_steam_media(con, now, only=None, refresh=False, limit=None, art=True):
    """Steam `appdetails`: the FULL screenshot set + movie trailers (playable .mp4/.webm)
    for each owned/wanted Steam appid — the media the constructed-URL `fetch_steam` can't
    reach because those have unpredictable ids. Per-appid, rate-limited and incremental
    (skips appids already pulled unless `refresh`), mirroring os_fetch. `only` scopes to a
    set of norm_keys (the wand's per-game path). Kept under provider 'steam' so it shares
    the Steam badge; fetch_steam's refresh is kind-scoped so it never wipes these."""
    games = steam_games()                       # {appid -> nk}
    if only is not None:
        want = set(only)
        games = {a: nk for a, nk in games.items() if nk in want}
    # Fold the constructed-CDN art (cover/hero/background/header/logo) into this
    # same pass so "Steam media" is the COMPLETE set in one go — no separate
    # "Also sync media" toggle (design: Algo runs both). Art is cheap + idempotent
    # (ON CONFLICT), and runs for EVERY scoped game regardless of the appdetails
    # incremental gate below, which only throttles the rate-limited store API.
    if art:
        for appid, nk in games.items():
            _put_steam_art(con, nk, appid, now)
        con.commit()
        # These are SPECULATIVE urls — not every appid has every library asset (older
        # titles have no library_hero/library_600x900 at all). Re-probe just what we
        # re-emitted, for just these games, so a dead hero can't be chosen and render
        # as a blank card. Without this the caller's earlier global prune is undone.
        prune_dead(con, only_nks=list(games.values()), providers=("steam",),
                   kinds=tuple(STEAM_ART))
    seen = _steam_media_seen(con)
    todo = [(a, nk) for a, nk in games.items() if refresh or a not in seen]
    if limit:
        todo = todo[:limit]
    cap = _screenshot_limit()
    smeta = _steam_meta_con()       # cache appdetails attributes alongside the media
    last = 0.0
    n = 0
    for i, (appid, nk) in enumerate(todo, 1):
        print("PROG\t%d\t%d\t%s\tsteam-media" % (i, len(todo), appid), flush=True)
        wait = last + STEAM_STORE_COOLDOWN - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        last = time.monotonic()
        try:
            req = urllib.request.Request(STEAM_APPDETAILS % appid,
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                d = json.load(r)
            entry = d.get(str(appid)) or {}
            if entry.get("success"):
                data = entry.get("data") or {}
                # cache Steam's own attributes (Algo tier feeds build_library) plus the
                # identity facts. Written even when _attrs is empty: a sub-app with no
                # attributes of its own still needs its canonical_appid recorded, or the
                # bundle relationship is lost.
                _attrs = _extract_steam_attrs(data)
                _store_name = (data.get("name") or "").strip() or None
                _canon = data.get("steam_appid")
                _canon = str(_canon) if _canon else None
                if _attrs or _store_name or _canon:
                    smeta.execute(
                        "INSERT OR REPLACE INTO steam_meta"
                        "(appid,norm_key,payload_json,fetched,store_name,canonical_appid)"
                        " VALUES(?,?,?,?,?,?)",
                        (appid, nk, json.dumps(_attrs), now, _store_name, _canon))
                shots = data.get("screenshots") or []
                if cap > 0:
                    shots = shots[:cap]
                for sh in shots:
                    url = sh.get("path_full") or sh.get("path_thumbnail")
                    if url:
                        put(con, nk, "screenshot", "steam", url, now,
                            ext="jpg", meta=appid)
                        n += 1
                # Steam's appdetails now lists only DASH/HLS manifests (dash_h264,
                # hls_h264) — not playable in a plain <video>. But the classic
                # constructed direct file, keyed by the movie id, still resolves to a
                # real .webm (verified), so build that; prune_dead drops any that 404.
                for mv in (data.get("movies") or []):
                    mid = mv.get("id")
                    if not mid:
                        continue
                    put(con, nk, "video", "steam",
                        STEAM_MOVIE % mid, now, ext="webm", meta=appid)
                    n += 1
            con.execute("INSERT OR REPLACE INTO steam_media_seen VALUES(?,?)",
                        (appid, now))
        except Exception as e:                  # noqa: BLE001 — one bad appid never aborts
            print("  steam-media %s: %s" % (appid, str(e)[:120]), file=sys.stderr)
        if i % 25 == 0:
            con.commit()
            smeta.commit()
    con.commit()
    smeta.commit()
    smeta.close()
    print("media_fetch: steam appdetails — %d screenshot/video URLs for %d game(s)"
          % (n, len(todo)), file=sys.stderr)


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
        _cap = _screenshot_limit()
        _shots = g.get("screenshots") or []
        for sh in (_shots[:_cap] if _cap > 0 else _shots):
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
    """Resolve a SteamGridDB game id — by Steam appid (exact) when we have one, else by
    name search (best autocomplete match).

    Returns None only for "looked and found nothing". RAISES when it could not look at
    all, because the caller records the result in a negative cache and those are two
    completely different facts: swallowing the error here turned an API hiccup into a
    stored "SteamGridDB does not have this game". Same defect as ScreenScraper's, found
    by auditing for the class after the Mass Effect 2 report.
    """
    looked = False
    if appid:
        try:
            g = _sgdb_get("/games/steam/%s" % appid, key)
            looked = True
            gid = (g.get("data") or {}).get("id")
            if gid:
                return gid
        except Exception:
            pass
    if title:
        try:
            d = _sgdb_get("/search/autocomplete/%s"
                          # safe="" or a slash in the title breaks OUT of the URL path:
                          # "FINAL FANTASY X/X-2 HD Remaster" became a request for a
                          # different endpoint entirely, which is why two games could
                          # never be asked at all.
                          % urllib.parse.quote(title.strip(), safe=""), key)
            looked = True
            items = d.get("data") or []
            if items:
                return items[0].get("id")
        except Exception:
            pass
    if not looked:
        raise RuntimeError("steamgriddb lookup failed for %r/%r — not a miss"
                           % (appid, title))
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


def prune_dead(con, workers=16, only_nks=None, providers=("steam", "igdb", "steamgriddb"),
               kinds=None):
    """HEAD-check un-materialized public URL refs and drop the definitively-dead
    ones (404/410). Candidate refs are recorded speculatively (esp. Steam CDN,
    which has no per-asset manifest — not every appid has every art type), so
    without this they'd render as blank cards. Cached (sha1) refs are already
    proven; screenscraper is skipped (its URLs need auth, so a bare HEAD lies).

    `only_nks` / `providers` / `kinds` scope the probe — used by the Steam-media
    pass to re-check just the constructed art it re-emitted for the games in that
    run, instead of re-probing the whole index."""
    import urllib.error
    from concurrent.futures import ThreadPoolExecutor
    base = ("SELECT id, ref FROM media WHERE ref_type='url' AND (sha1 IS NULL OR sha1='')"
            " AND provider IN (%s)" % ",".join("?" * len(providers)))
    base_params = list(providers)
    if kinds:
        base += " AND kind IN (%s)" % ",".join("?" * len(kinds))
        base_params += list(kinds)
    if only_nks is None:
        rows = con.execute(base, base_params).fetchall()
    else:
        nks = list(only_nks)
        rows = []
        for i in range(0, len(nks), 400):        # chunk: stay under SQLite's var limit
            chunk = nks[i:i + 400]
            rows += con.execute(
                base + " AND norm_key IN (%s)" % ",".join("?" * len(chunk)),
                base_params + chunk).fetchall()
        if not rows:
            return 0

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
    if "--ss-index" in argv:                      # incremental: index the SS cache without
        if config.get_bool("screenscraper_media", True):   # dropping what's already indexed
            fetch_screenscraper(con, now)        # (rows upsert by ref; sha1 preserved)
            con.commit()
        con.close()
        return
    if "--backfill-art" in argv:                  # name-based art gap-fill
        n = fetch_missing_art(con, now, limit)
        prune_dead(con)
        con.commit()
        con.close()
        print("media_fetch: backfill-art — added %d art URLs" % n, file=sys.stderr)
        return
    if "--steam-media" in argv:                   # per-appid appdetails: full shots + movies
        keys = None
        if "--keys" in argv:
            keys = [k for k in argv[argv.index("--keys") + 1].split("\x1f") if k]
        fetch_steam_media(con, now, only=keys, refresh=("--refresh" in argv), limit=limit)
        con.commit()
        con.close()
        return
    if "--sync-art" in argv:
        # Incremental per-sync art refresh: fetch NEW refs only. UNIQUE(provider,kind,ref)
        # dedupes re-runs, so existing rows keep their sha1 / measured width+height /
        # filler verdicts. The `--provider <p>` path below is a DESTRUCTIVE full refresh
        # (DELETE every one of the provider's rows, then refetch) — right for a manual
        # re-pull, wrong on every routine sync: it would discard the measurements and
        # re-download every chosen asset each run. No global prune_dead() here either —
        # it HTTP-probes every unmaterialized URL in the index (unscoped, slow; the
        # scoped reconcile skips it for the same reason), and IGDB refs come from the
        # API's own image manifest, so dead ones are rare.
        prov = argv[argv.index("--sync-art") + 1]
        if prov == "igdb" and config.media_enabled("igdb"):
            fetch_igdb(con, now)
        tot = con.execute("SELECT COUNT(*) FROM media WHERE provider=?",
                          (prov,)).fetchone()[0]
        con.commit()
        con.close()
        print("media_fetch: sync-art %s — %d refs indexed" % (prov, tot), file=sys.stderr)
        return
    if only in (None, "steam") and config.media_enabled("steam"):
        # kind-scoped delete: refresh only the constructed-URL art this pass owns, so the
        # incremental appdetails screenshots/movies (same provider) are NOT wiped.
        con.execute("DELETE FROM media WHERE provider='steam' AND kind IN "
                    "('cover','hero','background','header','logo')")
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
