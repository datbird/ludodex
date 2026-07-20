#!/usr/bin/env python3
"""ludodex AI-forward server — Phase 1: REST API + media resolver.

Read-mostly FastAPI over the existing SQLite artifacts (game-library.sqlite +
media-index.sqlite), reusing the pipeline's `media`/`media_choose` modules
in-process. The headline endpoint is the media resolver, which serves the chosen
asset per (game, kind): materialized bytes from the content-addressed repo, else
materialize-on-serve for remote URL refs (and cache them), else 404 for refs that
only live on the producer (the Deck). See HANDOFF.md §6.

Run:  uvicorn server.app:app --host 0.0.0.0 --port 8001
"""
import io
import json
import os
import random
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config          # noqa: E402  pipeline config store (config.sqlite)
import media           # noqa: E402  pipeline vocab/priority (pure data)
import media_choose    # noqa: E402  reuse _materialize_row (non-destructive)
import media_index      # noqa: E402  media-index schema (for the first-run seed)
import titlenorm       # noqa: E402  shared title -> norm_key (matches build_library)
import devices         # noqa: E402  device connections + library-manager pull
import fileops         # noqa: E402  file-operations engine (profiles + runbooks)
import aimeta          # noqa: E402  AI metadata audit/supplement store + context
import overrides       # noqa: E402  per-attribute provenance overrides (re-pointing)
import ownership       # noqa: E402  durable per-format ownership (physical + per-platform wants)
import compilations    # noqa: E402  durable collections/compilations store (ownership fan-out)
import igdb_enrich      # noqa: E402  IGDB cache resolvers (cross-platform releases + systems)
import console_eras     # noqa: E402  emulation platform era windows (year-plausibility gate)
import entry_res        # noqa: E402  per-entry IGDB resolution overrides (same-title split)
import medialang        # noqa: E402  per-asset media language classification + filter
import framing         # noqa: E402  per-game/per-kind image framing (position + zoom)
import mediaflags      # noqa: E402  durable per-asset ban / not-redistributable flags
import merges          # noqa: E402  durable game merges (fold duplicate entries)
import splits          # noqa: E402  durable "peel apart" (split a merged entry out)
import devicesync      # noqa: E402  outbound push (ROM+media+gamelist) to RetroDECK/ES-DE
import auth            # noqa: E402  local username/password accounts + sessions
import cf_access       # noqa: E402  Cloudflare Access SSO (verify the Access JWT)
from . import ai       # noqa: E402  AI features (server package)

LIBRARY_DB = os.path.join(DATA, "game-library.sqlite")
INDEX_DB = os.path.join(DATA, "media-index.sqlite")
RA_DB = os.path.join(DATA, "ra.sqlite")
PINS_DB = os.path.join(DATA, "pins.sqlite")  # durable art pins (survives media rescan)
OS_DB = os.path.join(DATA, "os.sqlite")      # durable OS support (windows/mac/linux) per store entry
TAGS_DB = os.path.join(DATA, "tags.sqlite")  # durable user-defined tags (survives rebuild)
UMEDIA_DB = os.path.join(DATA, "user-media.sqlite")  # durable user uploads (survives rebuild)
SCORES_DB = os.path.join(DATA, "scores.sqlite")  # multi-source ratings + unified score
MANUAL_DB = os.path.join(DATA, "manual-games.sqlite")  # durable hand-added games (survives rebuild)

import shutil  # noqa: E402
LEGENDARY = shutil.which("legendary") or os.path.expanduser("~/.local/bin/legendary")  # Epic OAuth CLI
EPIC_LOGIN_URL = ("https://www.epicgames.com/id/api/redirect"
                  "?clientId=34a02cf8f4414e29b15921876da36f9a&responseType=code")
# GOG Galaxy's public OAuth client (same one gog_owned.py exchanges against). After
# login the browser lands on embed.gog.com/on_login_success?...&code=<code>; the user
# copies that code (or the whole address) into the connect field.
GOG_LOGIN_URL = ("https://auth.gog.com/auth?client_id=%s"
                 "&redirect_uri=https%%3A%%2F%%2Fembed.gog.com%%2Fon_login_success%%3Forigin%%3Dclient"
                 "&response_type=code&layout=client2" % config.gog_creds()[0])

_STARTED = time.time()

# Server-managed SQLite databases, for the Server Operations panel.
# role: "durable" = user/app state (back up before recovery), "cache"/"output" = regenerable.
DATABASES = [
    ("config", "Config", "config.sqlite", "durable"),
    ("pins", "Art pins", "pins.sqlite", "durable"),
    ("os", "OS support", "os.sqlite", "durable"),
    ("tags", "User tags", "tags.sqlite", "durable"),
    ("usermedia", "User media", "user-media.sqlite", "durable"),
    ("scores", "Ratings & scores", "scores.sqlite", "durable"),
    ("manual_games", "Manual games", "manual-games.sqlite", "durable"),
    ("ai_usage", "AI usage", "ai-usage.sqlite", "durable"),
    ("connections", "Device connections", "connections.sqlite", "durable"),
    ("fileops", "File operations", "file-profiles.sqlite", "durable"),
    ("aimeta", "AI metadata", "ai-metadata.sqlite", "durable"),
    ("collections", "Collections", "collections.sqlite", "durable"),
    ("overrides", "Attribute overrides", "attr-overrides.sqlite", "durable"),
    ("merges", "Duplicate merges", "merges.sqlite", "durable"),
    ("splits", "Peeled-apart games", "splits.sqlite", "durable"),
    ("ra", "RetroAchievements", "ra.sqlite", "durable"),
    ("library", "Game library", "game-library.sqlite", "output"),
    ("media", "Media index", "media-index.sqlite", "output"),
    ("metadata", "Metadata cache", "metadata-cache.sqlite", "cache"),
    # NB: roms-index.sqlite is a Deck-side *input* (config roms_index_db →
    # /home/deck/...), not a DB this server owns, so it's intentionally not listed.
]
DB_BY_ID = {d[0]: d for d in DATABASES}
REPO = media_choose.repo_dir()            # content-addressed media repo (DIR/media)
THUMBS = os.path.join(REPO, ".thumbs")
os.makedirs(THUMBS, exist_ok=True)

# Source flags that exist as columns on `games` (the rest live in `sources`).
COLUMN_SOURCES = ("emulation", "steam", "gog", "epic", "itch", "archive")

app = FastAPI(title="ludodex", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# --------------------------------------------------------------------------- #
#  Authentication: local accounts + session cookie. The whole /api surface is
#  gated except the auth endpoints and /api/health; the static SPA stays public
#  so it can render the create-admin / login screens.
# --------------------------------------------------------------------------- #
SESSION_COOKIE = "ludodex_session"
_AUTH_OPEN = ("/api/auth/", "/api/health")


def _cf_cfg():
    return {
        "enabled": config.get("cf_access_enabled") == "1",
        "team_domain": config.get("cf_access_team_domain") or "",
        "aud": config.get("cf_access_aud") or "",
    }


def _current_user(request: Request):
    # 1) a normal ludodex session cookie
    user = auth.session_user(request.cookies.get(SESSION_COOKIE))
    if user:
        return user
    # 2) Cloudflare Access SSO: verify the Access JWT, map its email to a user
    cf = _cf_cfg()
    if cf["enabled"] and cf["team_domain"] and cf["aud"]:
        token = (request.headers.get("Cf-Access-Jwt-Assertion")
                 or request.cookies.get("CF_Authorization"))
        email = cf_access.verify_email(token, cf["team_domain"], cf["aud"])
        if email:
            return auth.user_for_email(email)   # None if that email isn't mapped
    return None


def _set_session_cookie(resp, token):
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                    max_age=auth.SESSION_TTL, path="/")


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith(_AUTH_OPEN):
        if not _current_user(request):
            return JSONResponse({"detail": "authentication required"}, status_code=401)
    return await call_next(request)


@app.get("/api/auth/status")
def auth_status(request: Request):
    """Drives the frontend gate: setup (no accounts) / login / authenticated."""
    user = _current_user(request)
    return {"needs_setup": auth.needs_setup(), "authenticated": bool(user), "user": user}


@app.post("/api/auth/setup")
def auth_setup(body: dict = Body(...)):
    if not auth.needs_setup():
        raise HTTPException(409, "already set up — an account already exists")
    try:
        uid = auth.create_user((body or {}).get("username", ""),
                               (body or {}).get("password", ""), role="admin")
    except ValueError as e:
        raise HTTPException(400, str(e))
    user = {"id": uid, "username": (body or {}).get("username", "").strip(), "role": "admin"}
    resp = JSONResponse({"ok": True, "user": user})
    _set_session_cookie(resp, auth.create_session(uid))
    return resp


@app.post("/api/auth/login")
def auth_login(body: dict = Body(...)):
    user = auth.verify((body or {}).get("username", ""), (body or {}).get("password", ""))
    if not user:
        raise HTTPException(401, "invalid username or password")
    resp = JSONResponse({"ok": True, "user": user})
    _set_session_cookie(resp, auth.create_session(user["id"]))
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    auth.delete_session(request.cookies.get(SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


def _require_admin(request: Request):
    user = _current_user(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(403, "admin access required")
    return user


@app.get("/api/auth/users")
def auth_users(request: Request):
    me = _require_admin(request)
    return {"users": auth.list_users(), "me": me["id"], "roles": list(auth.ROLES)}


@app.post("/api/auth/users")
def auth_add_user(request: Request, body: dict = Body(...)):
    _require_admin(request)
    role = (body or {}).get("role") or "user"
    if role not in auth.ROLES:
        raise HTTPException(400, "invalid role")
    try:
        uid = auth.create_user((body or {}).get("username", ""),
                               (body or {}).get("password", ""), role=role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "user": auth.get_user(uid)}


@app.delete("/api/auth/users/{uid}")
def auth_delete_user(request: Request, uid: int):
    me = _require_admin(request)
    target = auth.get_user(uid)
    if not target:
        raise HTTPException(404, "no such user")
    if uid == me["id"]:
        raise HTTPException(400, "you can't delete your own account")
    if target["role"] == "admin" and auth.admin_count() <= 1:
        raise HTTPException(400, "can't delete the last admin")
    auth.delete_user(uid)
    return {"ok": True}


@app.post("/api/auth/users/{uid}/password")
def auth_reset_password(request: Request, uid: int, body: dict = Body(...)):
    _require_admin(request)
    if not auth.get_user(uid):
        raise HTTPException(404, "no such user")
    try:
        auth.set_password(uid, (body or {}).get("password", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/auth/users/{uid}/role")
def auth_set_role(request: Request, uid: int, body: dict = Body(...)):
    me = _require_admin(request)
    target = auth.get_user(uid)
    if not target:
        raise HTTPException(404, "no such user")
    role = (body or {}).get("role")
    if target["role"] == "admin" and role != "admin" and auth.admin_count() <= 1:
        raise HTTPException(400, "can't demote the last admin")
    if uid == me["id"] and role != "admin":
        raise HTTPException(400, "you can't remove your own admin role")
    try:
        auth.set_role(uid, role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Cloudflare Access SSO: config + email→user mappings (admin)
# --------------------------------------------------------------------------- #
def _cf_state():
    return {**_cf_cfg(), "mappings": auth.list_email_maps(), "users": auth.list_users()}


@app.get("/api/auth/cf-access")
def cf_access_get(request: Request):
    _require_admin(request)
    return _cf_state()


@app.post("/api/auth/cf-access")
def cf_access_set(request: Request, body: dict = Body(...)):
    _require_admin(request)
    b = body or {}
    if "enabled" in b:
        config.set_("cf_access_enabled", "1" if b["enabled"] else "0")
    if "team_domain" in b:
        config.set_("cf_access_team_domain", (b["team_domain"] or "").strip().strip("/"))
    if "aud" in b:
        config.set_("cf_access_aud", (b["aud"] or "").strip())
    return _cf_state()


@app.post("/api/auth/cf-access/map")
def cf_access_map(request: Request, body: dict = Body(...)):
    _require_admin(request)
    try:
        auth.map_email((body or {}).get("email", ""), (body or {}).get("user_id"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "mappings": auth.list_email_maps()}


@app.post("/api/auth/cf-access/unmap")
def cf_access_unmap(request: Request, body: dict = Body(...)):
    _require_admin(request)
    auth.unmap_email((body or {}).get("email", ""))
    return {"ok": True, "mappings": auth.list_email_maps()}


# ----------------------------------------------------------------------------- db
def ro(path):
    """Open a SQLite db read-only (one connection per request — cheap, thread-safe).
    busy_timeout: if a writer briefly holds the db (a pipeline pass mid-write), wait a
    few seconds rather than throwing 'database is locked' at the caller. The catalog
    rebuild itself no longer locks (build_library swaps a temp db in atomically), but
    this covers the other stores (scores, media backfill, etc.) writing concurrently."""
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=8000")
    con.row_factory = sqlite3.Row
    return con


def _tags_con():
    """Durable user-tag store (origin 'ludodex'); survives catalog rebuilds, like
    pins/os. One row per (game, tag)."""
    con = sqlite3.connect(TAGS_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS user_tags(
        norm_key TEXT, tag TEXT, created REAL, PRIMARY KEY(norm_key, tag))""")
    con.row_factory = sqlite3.Row
    return con


def _umedia_con():
    """Durable user-uploaded media store; survives catalog/media rebuilds. Bytes
    live in the content-addressed REPO as <sha1>.<ext>; this indexes them per game."""
    con = sqlite3.connect(UMEDIA_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS user_media(
        id INTEGER PRIMARY KEY AUTOINCREMENT, norm_key TEXT, kind TEXT,
        sha1 TEXT, ext TEXT, width INTEGER, height INTEGER,
        origin TEXT, created REAL)""")
    con.row_factory = sqlite3.Row
    return con


def _scores_con():
    """Durable multi-source ratings + computed Ludodex score store (scores_fetch.py)."""
    con = sqlite3.connect(SCORES_DB)
    con.execute("PRAGMA journal_mode=WAL")   # concurrent read while scores_fetch writes
    con.execute("""CREATE TABLE IF NOT EXISTS ratings(
        norm_key TEXT, source TEXT, kind TEXT, score REAL, votes INTEGER,
        raw TEXT, updated REAL, PRIMARY KEY(norm_key, source, kind))""")
    con.execute("""CREATE TABLE IF NOT EXISTS game_scores(
        norm_key TEXT PRIMARY KEY, universal INTEGER, critic INTEGER,
        user INTEGER, n_sources INTEGER, updated REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS steam_type(
        norm_key TEXT PRIMARY KEY, type TEXT, updated REAL)""")
    con.row_factory = sqlite3.Row
    return con


# Steam appdetails `type`s that aren't games — hidden when hide_non_games is on.
NON_GAME_TYPES = ("application", "tool", "music", "video", "hardware", "series", "mod")


def _non_game_hidden_sql():
    """SQL boolean (+args) that is TRUE for an entry to hide as a NON-game. The manual
    `content_type` override (attr-overrides, aliased `ov`) wins over Steam's detected
    type: a value other than 'Game' hides an item Steam mis-tagged as a game (Wallpaper
    Engine, fpsVR — Steam calls them games), and 'Game' rescues a real game Steam
    mis-tagged as a tool. With no manual override, fall back to the Steam type. Requires
    a connection with `ov` + `sco` attached (i.e. lib())."""
    ph = ",".join("?" * len(NON_GAME_TYPES))
    expr = ("CASE WHEN EXISTS(SELECT 1 FROM ov.overrides o WHERE o.norm_key=g.norm_key "
            "AND o.kind='content_type') "
            "THEN EXISTS(SELECT 1 FROM ov.overrides o WHERE o.norm_key=g.norm_key AND "
            "o.kind='content_type' AND lower(o.value)<>'game') "
            "ELSE g.norm_key IN (SELECT norm_key FROM sco.steam_type WHERE type IN (%s)) "
            "END" % ph)
    return expr, list(NON_GAME_TYPES)

# Storefront labels are Sources, not Systems — PC-store games get platform=source,
# so exclude these (and the generic psn/xbox fallbacks) from the Systems facet.
# Real consoles (ps4/ps5/xbox one/…/windows) are kept.
NON_SYSTEM_PLATFORMS = ("steam", "gog", "epic", "itch", "ea", "psn", "xbox")


def _manual_con():
    """Durable hand-added games (the library '+' add flow); survives rebuilds.
    build_library merges these back in as source rows keyed by norm_key."""
    con = sqlite3.connect(MANUAL_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS manual_games(
        norm_key TEXT, title TEXT, source TEXT, platform TEXT,
        detail TEXT, added REAL, PRIMARY KEY(norm_key, source, platform))""")
    con.row_factory = sqlite3.Row
    return con


_tags_con().close()     # ensure files + schema exist so lib() can ATTACH them ro
_umedia_con().close()
_scores_con().close()
_manual_con().close()
overrides._con().close()   # attr-overrides.sqlite must exist so lib() can ATTACH it ro


def _ensure_catalog():
    """First-run seed: the catalog is a build OUTPUT (build_library.py), absent
    until the user runs a sync. Without it the read-only lib() open fails and the
    whole library view 500s ('unable to open database file'). Seed an empty
    catalog with the same schema so a fresh install shows a clean, empty library.
    A real build drops+recreates this file, so there's no drift risk."""
    if os.path.exists(LIBRARY_DB):
        return
    con = sqlite3.connect(LIBRARY_DB)
    con.executescript("""
    CREATE TABLE games (id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
      n_sources INTEGER, n_kinds INTEGER, sources_summary TEXT,
      has_emulation INT, has_steam INT, has_gog INT, has_epic INT, has_itch INT,
      has_archive INT, in_playnite INT, in_launchbox INT);
    CREATE TABLE sources (game_id INTEGER, source TEXT, platform TEXT,
      source_id TEXT, title_raw TEXT, detail TEXT);
    CREATE TABLE source_attrs (game_id INTEGER, source TEXT, source_id TEXT,
      attrs_json TEXT);
    CREATE TABLE game_attributes (game_id INTEGER, kind TEXT, value TEXT,
      origin TEXT DEFAULT '');
    CREATE TABLE metadata_links (game_id INTEGER, provider TEXT, provider_id TEXT,
      slug TEXT, url TEXT);
    CREATE TABLE game_tags (game_id INTEGER, tag TEXT, origin TEXT);
    CREATE INDEX ix_norm ON games(norm_key);
    CREATE INDEX ix_title ON games(canonical_title);
    CREATE INDEX ix_src_game ON sources(game_id);
    CREATE INDEX ix_src_plat ON sources(platform);
    CREATE INDEX ix_sattr_game ON source_attrs(game_id);
    CREATE INDEX ix_gattr_game ON game_attributes(game_id);
    CREATE INDEX ix_gattr_kv ON game_attributes(kind, value);
    CREATE INDEX ix_mlink_game ON metadata_links(game_id);
    CREATE INDEX ix_gtag_game ON game_tags(game_id);
    """)
    con.close()


_ensure_catalog()               # empty catalog so a fresh install doesn't 500
media_index.index_con().close()  # empty media-index so lib()'s ATTACH ro works


def lib():
    """game-library connection, ATTACHing media-index as `m`, user-tags as `t`,
    user-media as `u`, and ratings/scores as `sco` (all read-only)."""
    con = ro(LIBRARY_DB)
    con.execute("ATTACH DATABASE ? AS m", ("file:%s?mode=ro" % INDEX_DB,))
    con.execute("ATTACH DATABASE ? AS t", ("file:%s?mode=ro" % TAGS_DB,))
    con.execute("ATTACH DATABASE ? AS u", ("file:%s?mode=ro" % UMEDIA_DB,))
    con.execute("ATTACH DATABASE ? AS sco", ("file:%s?mode=ro" % SCORES_DB,))
    con.execute("ATTACH DATABASE ? AS ov", ("file:%s?mode=ro" % overrides.DB,))
    return con


# ------------------------------------------------------------------------- routes
@app.get("/api/stats")
def stats():
    con = lib()
    try:
        wcol = _has_col(con, "games", "wanted")     # wishlist-only games: exclude from owned stats
        gw = " WHERE g.wanted=0" if wcol else ""
        and_w = " AND g.wanted=0" if wcol else ""
        g = con.execute("SELECT COUNT(*) FROM games g" + gw).fetchone()[0]
        ident = con.execute("SELECT COUNT(*) FROM games g" +
                            (gw + " AND " if gw else " WHERE ") + IDENTIFIED_SQL).fetchone()[0]
        wanted_ct = con.execute("SELECT COUNT(*) FROM games WHERE wanted=1").fetchone()[0] if wcol else 0
        cross = con.execute("SELECT COUNT(*) FROM games WHERE n_kinds>1").fetchone()[0]
        unmatched = con.execute(
            "SELECT COUNT(*) FROM games g WHERE NOT EXISTS("
            "SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id)" + and_w).fetchone()[0]
        no_media = con.execute(
            "SELECT COUNT(*) FROM games g WHERE NOT EXISTS("
            "SELECT 1 FROM m.media md WHERE md.norm_key=g.norm_key)" + and_w).fetchone()[0]
        by_source = {}
        for s in COLUMN_SOURCES:
            by_source[s] = con.execute(
                "SELECT COUNT(*) FROM games WHERE has_%s=1" % s).fetchone()[0]
        # dynamic sources (ea/playnite/etc.) live only in the sources table
        for row in con.execute("SELECT source, COUNT(DISTINCT game_id) c "
                               "FROM sources GROUP BY source"):
            by_source.setdefault(row["source"], row["c"])
        coverage = {}
        total_with = con.execute(
            "SELECT COUNT(DISTINCT norm_key) FROM m.media WHERE chosen=1").fetchone()[0]
        for row in con.execute("SELECT kind, COUNT(DISTINCT norm_key) c "
                               "FROM m.media WHERE chosen=1 GROUP BY kind"):
            coverage[row["kind"]] = row["c"]
        return {
            "games": g,
            "identified": ident,               # real, known titles (in the library)
            "unidentified": g - ident,         # bare ROMs awaiting identification
            "wanted": wanted_ct,
            "cross_source": cross,
            "unmatched": unmatched,
            "no_media": no_media,
            "by_source": by_source,
            "media": {"games_with_art": total_with, "by_kind": coverage},
            "pending_meta": aimeta.pending_count(),   # accepted-not-applied findings
        }
    finally:
        con.close()


@app.get("/api/facets")
def facets():
    """Distinct sources + platforms + every categorical attribute value, for the
    UI filter dropdown (so ANY attribute is filterable)."""
    con = lib()
    try:
        sources = [r["source"] for r in con.execute(
            "SELECT DISTINCT source FROM sources ORDER BY source")]
        platforms = [r["platform"] for r in con.execute(
            "SELECT platform, COUNT(*) c FROM sources WHERE platform IS NOT NULL "
            "AND platform!='' AND platform NOT IN (%s) "
            "GROUP BY platform ORDER BY c DESC"
            % ",".join("?" * len(NON_SYSTEM_PLATFORMS)), NON_SYSTEM_PLATFORMS)]
        # every categorical attribute kind -> its values (busiest first). Free-text
        # kinds (description) aren't value-filterable, so they're skipped.
        attributes = {}
        kinds = [r["kind"] for r in con.execute(
            "SELECT DISTINCT kind FROM game_attributes "
            "WHERE kind NOT IN ('description') ORDER BY kind")]
        for k in kinds:
            vals = [r["value"] for r in con.execute(
                "SELECT value, COUNT(*) c FROM game_attributes WHERE kind=? "
                "AND value IS NOT NULL AND value!='' GROUP BY value "
                "ORDER BY c DESC, value LIMIT 400", (k,))]
            if vals:
                attributes[k] = vals
        return {"sources": sources, "platforms": platforms, "attributes": attributes}
    finally:
        con.close()


# Source/status flags for the include/exclude filter grid -> SQL boolean expr.
FLAG_SQL = {
    "steam": "g.has_steam=1",
    "gog": "g.has_gog=1",
    "epic": "g.has_epic=1",
    "itch": "g.has_itch=1",
    "emulation": "g.has_emulation=1",
    "archive": "g.has_archive=1",
    "playnite": "g.in_playnite=1",
    "launchbox": "g.in_launchbox=1",
    "matched": "EXISTS(SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id)",
    "has_cover": "(EXISTS(SELECT 1 FROM m.media md WHERE md.norm_key=g.norm_key "
                 "AND md.chosen=1 AND md.kind='cover') OR EXISTS(SELECT 1 FROM "
                 "u.user_media um WHERE um.norm_key=g.norm_key AND um.kind='cover'))",
    "has_media": "(EXISTS(SELECT 1 FROM m.media md WHERE md.norm_key=g.norm_key) "
                 "OR EXISTS(SELECT 1 FROM u.user_media um WHERE um.norm_key=g.norm_key))",
    "cross_source": "g.n_sources>1",
}


# Sort keys -> (SQL expression, default direction). Applied in priority order.
SORT_SQL = {
    "title": ("g.canonical_title COLLATE NOCASE", "ASC"),
    "platform": ("(SELECT MIN(s.platform) FROM sources s WHERE s.game_id=g.id)", "ASC"),
    "source": ("g.sources_summary", "ASC"),
    "n_sources": ("g.n_sources", "DESC"),
    "n_kinds": ("g.n_kinds", "DESC"),
    "matched": ("EXISTS(SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id)", "DESC"),
    "has_cover": ("EXISTS(SELECT 1 FROM m.media md WHERE md.norm_key=g.norm_key "
                  "AND md.chosen=1 AND md.kind='cover')", "DESC"),
    "cross_source": ("(g.n_sources>1)", "DESC"),
}


def _ludodex_weight():
    """Critic weight (0..1) for the unified Ludodex score; user weight is 1-this.
    Default 0.6 (critic-leaning). Tunable via config `ludodex_critic_weight`."""
    try:
        w = float(config.get("ludodex_critic_weight") or 0.6)
    except (TypeError, ValueError):
        w = 0.6
    return min(1.0, max(0.0, w))


def _order_by(sort, extra=None):
    """Build an ORDER BY clause from an ordered list of SORT_SQL keys (plus any
    `extra` key->(expr,dir)); always ends with a stable title tiebreak."""
    table = dict(SORT_SQL, **(extra or {}))
    parts = []
    for k in (sort or []):
        if k in table:
            expr, d = table[k]
            parts.append("%s %s" % (expr, d))
    parts.append("g.canonical_title COLLATE NOCASE ASC")
    return " ORDER BY " + ", ".join(parts)


def _has_col(con, table, col):
    try:
        return any(r[1] == col for r in con.execute("PRAGMA table_info(%s)" % table))
    except sqlite3.Error:
        return False


# A game is "identified" once it's a real, known title: a provider match (IGDB /
# ScreenScraper) OR a store/manual source. A bare ROM (emulation/archive only, no
# match) is just a file — "unidentified" — and is hidden from the library by
# default until it's identified (manually or by the wand).
IDENTIFIED_SQL = (
    "(EXISTS(SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id) "
    "OR EXISTS(SELECT 1 FROM sources s WHERE s.game_id=g.id AND s.source NOT IN "
    "('emulation','archive','physical','rom','digital')) "
    # a wishlist-wanted game is a known title from a real store (steam/gog), so it
    # counts as identified even before IGDB matches it — else the Wanted view hides
    # the whole wishlist behind "show unidentified".
    "OR EXISTS(SELECT 1 FROM wanted w WHERE w.game_id=g.id))")


# --- advanced "Query" search language -------------------------------------- #
_QL_ATTR = {  # field alias -> game_attributes.kind
    "genre": "genres", "genres": "genres", "theme": "themes", "themes": "themes",
    "mode": "game_modes", "modes": "game_modes", "perspective": "player_perspectives",
    "dev": "developers", "developer": "developers", "developers": "developers",
    "pub": "publishers", "publisher": "publishers", "publishers": "publishers",
    "series": "series", "os": "os", "device": "device", "region": "regions",
}


def _ql_num(field_sql, raw):
    """'>1990' / '<=75' / '1995' -> (sql_fragment, int_arg), or None."""
    m = re.match(r"^(>=|<=|>|<|=)?\s*(-?\d+)$", (raw or "").strip())
    if not m:
        return None
    return ("%s %s ?" % (field_sql, m.group(1) or "="), int(m.group(2)))


def _parse_query(qstr):
    """Advanced search query -> (where_sql[], args). Bare words match the title;
    field:value matches attributes/sources (platform, source, genre, tag, dev,
    publisher, os, …); year:/score: take numeric comparators (>,<,>=,<=); a
    leading '-' negates; "quoted phrases" are kept whole."""
    where, args = [], []
    for tok in re.findall(r'-?[\w]+:"[^"]*"|-?[\w]+:\S+|-?"[^"]*"|-?\S+', qstr or ""):
        neg = tok.startswith("-")
        tok = tok[1:] if neg else tok
        if ":" in tok and not tok.startswith('"'):
            field, _, val = tok.partition(":")
            field, val = field.lower(), val.strip('"')
        else:
            field, val = "title", tok.strip('"')
        if not val:
            continue
        clause, cargs = None, []
        if field in ("title", "name"):
            clause, cargs = "g.canonical_title LIKE ?", ["%%%s%%" % val]
        elif field in ("platform", "system"):
            clause, cargs = ("EXISTS(SELECT 1 FROM sources s WHERE s.game_id=g.id "
                             "AND s.platform LIKE ?)"), ["%%%s%%" % val]
        elif field in ("source", "store"):
            clause, cargs = ("EXISTS(SELECT 1 FROM sources s WHERE s.game_id=g.id "
                             "AND s.source LIKE ?)"), ["%%%s%%" % val]
        elif field == "tag":
            clause, cargs = ("EXISTS(SELECT 1 FROM game_tags gt WHERE gt.game_id=g.id "
                             "AND gt.tag LIKE ?)"), ["%%%s%%" % val]
        elif field == "year":
            num = _ql_num("CAST(ga.value AS INT)", val)
            if num:
                clause, cargs = ("EXISTS(SELECT 1 FROM game_attributes ga WHERE "
                                 "ga.game_id=g.id AND ga.kind='release_year' AND %s)"
                                 % num[0]), [num[1]]
        elif field == "score":
            num = _ql_num("(SELECT gs.universal FROM sco.game_scores gs "
                          "WHERE gs.norm_key=g.norm_key)", val)
            if num:
                clause, cargs = num[0], [num[1]]
        elif field in _QL_ATTR:
            clause, cargs = ("EXISTS(SELECT 1 FROM game_attributes ga WHERE "
                             "ga.game_id=g.id AND ga.kind=? AND ga.value LIKE ?)"), \
                            [_QL_ATTR[field], "%%%s%%" % val]
        else:                                   # unknown field: match the raw token in title
            clause, cargs = "g.canonical_title LIKE ?", ["%%%s:%s%%" % (field, val)]
        if clause:
            where.append("NOT (%s)" % clause if neg else clause)
            args.extend(cargs)
    return where, args


def _query_games(con, q=None, source=None, platform=None, has_kind=None,
                 include=None, exclude=None, sort=None, limit=60, offset=0,
                 status="owned", identified="only", query=None):
    """Core catalog query — shared by /api/games and AI /api/search.
    include/exclude are lists of FLAG_SQL keys (a flag can't be in both);
    sort is an ordered list of SORT_SQL keys (1st, 2nd, 3rd priority).
    status: 'owned' (default, wanted=0) | 'wanted' (wanted=1) | 'all'.
    identified: 'only' (default, hide bare unidentified ROMs) | 'all' | 'unidentified'."""
    where, args = [], []
    has_w = _has_col(con, "games", "wanted")
    has_ek = _has_col(con, "games", "entry_key")   # per-platform entries (DESIGN §11)
    if status == "wanted":
        where.append("g.wanted=1" if has_w else "0")
    elif status != "all" and has_w:            # 'owned' (default): hide wishlist-only
        where.append("g.wanted=0")
    if identified == "only":
        where.append(IDENTIFIED_SQL)
    elif identified == "unidentified":
        where.append("NOT " + IDENTIFIED_SQL)
    if query:                                  # advanced query-language mode
        qw, qa = _parse_query(query)
        where.extend(qw)
        args.extend(qa)
    elif q:
        where.append("g.canonical_title LIKE ?")
        args.append("%%%s%%" % q)
    def _fexpr(tok):
        """Filter token -> (sql, args). Bare tokens hit FLAG_SQL; 'source:<x>'
        and 'system:<x>' match the sources table dynamically."""
        if tok in FLAG_SQL:
            return FLAG_SQL[tok], []
        if tok.startswith("source:"):
            return ("EXISTS(SELECT 1 FROM sources s WHERE s.game_id=g.id "
                    "AND s.source=?)", [tok[7:]])
        if tok.startswith("system:"):
            return ("EXISTS(SELECT 1 FROM sources s WHERE s.game_id=g.id "
                    "AND s.platform=?)", [tok[7:]])
        if tok.startswith("attr:"):                  # attr:<kind>:<value>
            rest = tok[5:]
            if ":" in rest:
                kind, val = rest.split(":", 1)
                return ("EXISTS(SELECT 1 FROM game_attributes ga WHERE "
                        "ga.game_id=g.id AND ga.kind=? AND ga.value=?)", [kind, val])
        if tok.startswith("wanted:"):
            # device wishlist lives in connections.sqlite; resolve its norm_keys here
            keys = devices.wants_keys(tok[7:]) if tok[7:].isdigit() else []
            if not keys:
                return "0", []
            return "g.norm_key IN (%s)" % ",".join("?" * len(keys)), list(keys)
        return None, None
    for f in (include or []):
        e, a = _fexpr(f)
        if e:
            where.append(e); args += a
    for f in (exclude or []):
        e, a = _fexpr(f)
        if e:
            where.append("NOT (%s)" % e); args += a
    if source:
        where.append("EXISTS (SELECT 1 FROM sources s "
                     "WHERE s.game_id=g.id AND s.source=?)")
        args.append(source)
    if platform:
        where.append("EXISTS (SELECT 1 FROM sources s "
                     "WHERE s.game_id=g.id AND s.platform=?)")
        args.append(platform)
    if has_kind:
        where.append("g.norm_key IN (SELECT norm_key FROM m.media "
                     "WHERE chosen=1 AND kind=?)")
        args.append(has_kind)
    if config.get_bool("hide_non_games", True):
        _ex, _exargs = _non_game_hidden_sql()
        where.append("NOT (" + _ex + ")")
        args += _exargs
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    total = con.execute("SELECT COUNT(*) FROM games g" + clause, args).fetchone()[0]
    # how many unidentified games the SAME search would surface if the hide toggle
    # were off — drives the "N matches hidden — show them" hint during a search.
    hidden = 0
    if identified == "only" and (q or query):
        hw = [w for w in where if w != IDENTIFIED_SQL] + ["NOT " + IDENTIFIED_SQL]
        hidden = con.execute("SELECT COUNT(*) FROM games g WHERE " + " AND ".join(hw),
                             args).fetchone()[0]
    # unified Ludodex score is precomputed per game (scores_fetch.py -> sco.game_scores)
    score = "(SELECT gs.universal FROM sco.game_scores gs WHERE gs.norm_key=g.norm_key)"
    wsel = "g.wanted AS wanted, " if has_w else ""
    # per-platform entry id + platform (fall back to base norm_key on an un-rebuilt
    # catalog so the server survives a deploy before the first rebuild)
    eksel = ("g.entry_key AS entry_key, g.platform AS platform, " if has_ek
             else "g.norm_key AS entry_key, NULL AS platform, ")
    # cover_v: content hash of the cover THIS entry serves — user upload, then the
    # entry's own console art, then platform-neutral store art. NEVER another console's
    # art (COALESCE of WHERE-correlated subqueries; SQLite forbids an outer column ref
    # in a subquery ORDER BY, so system preference is ordered fallbacks, not a sort).
    _um = ("(SELECT substr(um.sha1,1,12) FROM u.user_media um WHERE um.norm_key=g.norm_key "
           "AND um.kind='cover' ORDER BY um.created DESC LIMIT 1)")
    _mc = ("(SELECT substr(md.sha1,1,12) FROM m.media md WHERE md.norm_key=g.norm_key "
           "AND md.chosen=1 AND md.kind='cover'%s LIMIT 1)")
    if has_ek:
        _own = " AND COALESCE(md.system,'')=COALESCE(g.platform,'')"
        # Neutral (platform-agnostic store/IGDB) art belongs to a specific resolved game.
        # It is served to THIS entry only when the media's identity matches the entry's —
        # media.game_key = g.game_key (DESIGN §11.9). That serves an identified game or a
        # stray retro-handheld port (both adopt igdb:<id>) while an era-collision entry
        # (its game_key is title:<nk>, the neutral art's is igdb:<id>) forfeits it — the
        # identity match replaces the old base_key era-marker test. (g.* outer refs are
        # legal in a subquery WHERE; only ORDER BY forbids them.)
        _hasgk = _has_col(con, "games", "game_key")
        _gk_gate = (" AND md.game_key=g.game_key" if _hasgk else "")
        _neutral = " AND COALESCE(md.system,'')=''" + _gk_gate
        # Neutral art is ALSO reachable by game IDENTITY across norm_keys: a game whose title
        # parsed into two norm_keys (International Karate "+"/"plus"/gb — same igdb id) shares
        # its one fetched cover. game_key self-restricts an unresolved title (title:<nk>), so
        # there is no cross-title bleed. Own-console art stays strictly per-norm_key. Added as
        # a COALESCE fallback so a game with its own neutral art is byte-for-byte unchanged.
        _mc_gk = ("(SELECT substr(md.sha1,1,12) FROM m.media md WHERE md.game_key=g.game_key "
                  "AND md.chosen=1 AND md.kind='cover' AND COALESCE(md.system,'')='' LIMIT 1)")
        _neu_gk_ex = (" OR EXISTS(SELECT 1 FROM m.media md WHERE md.game_key=g.game_key AND "
                      "md.chosen=1 AND md.kind='cover' AND COALESCE(md.system,'')='')"
                      if _hasgk else "")
        _cv = [_um, _mc % _own, _mc % _neutral] + ([_mc_gk] if _hasgk else [])
        cover_v = "COALESCE(" + ",".join(_cv) + ") AS cover_v, "
        # has_cover reflects SERVABLE art (own console or gated neutral), so a card with
        # only another console's art shows the placeholder, not a broken/foreign image.
        has_cov = ("((EXISTS(SELECT 1 FROM m.media md WHERE md.norm_key=g.norm_key AND "
                   "md.chosen=1 AND md.kind='cover'" + _own + ") OR EXISTS(SELECT 1 FROM "
                   "m.media md WHERE md.norm_key=g.norm_key AND md.chosen=1 AND "
                   "md.kind='cover'" + _neutral + ")" + _neu_gk_ex + ") OR EXISTS(SELECT 1 "
                   "FROM u.user_media um WHERE um.norm_key=g.norm_key AND um.kind='cover')) "
                   "AS has_cover, ")
    else:
        cover_v = "COALESCE(" + _um + "," + _mc % "" + ") AS cover_v, "
        has_cov = ("(EXISTS(SELECT 1 FROM m.media md WHERE md.norm_key=g.norm_key AND "
                   "md.chosen=1 AND md.kind='cover') OR EXISTS(SELECT 1 FROM u.user_media "
                   "um WHERE um.norm_key=g.norm_key AND um.kind='cover')) AS has_cover, ")
    base = (
        "SELECT g.norm_key, " + eksel + "g.canonical_title, g.n_sources, g.n_kinds, "
        "g.sources_summary, g.has_emulation AS is_emulation, " + wsel +
        "(SELECT group_concat(DISTINCT s.platform) FROM sources s "
        "   WHERE s.game_id=g.id AND s.platform IS NOT NULL AND s.platform!='') AS platforms, "
        "EXISTS(SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id) AS matched, "
        + IDENTIFIED_SQL + " AS identified, "
        + has_cov
        + cover_v
        + score + " AS ludodex_score, "
        "(SELECT group_concat(ga.kind||char(31)||ga.value, char(30)) "
        "   FROM game_attributes ga WHERE ga.game_id=g.id) AS attrs, "
        "%s"
        "(SELECT group_concat('ludodex:'||ut.tag, char(31)) FROM t.user_tags ut "
        "   WHERE ut.norm_key=g.norm_key) AS usr_tags "
        "FROM games g" + clause +
        _order_by(sort, {"ludodex_score": (score, "DESC")}) + " LIMIT ? OFFSET ?")
    # imported-origin tags live in the catalog's game_tags (absent in an older DB)
    imp = ("(SELECT group_concat(gt.origin||':'||gt.tag, char(31)) FROM game_tags gt "
           "   WHERE gt.game_id=g.id AND gt.origin<>'ludodex') AS imp_tags, ")
    try:
        rows = con.execute(base % imp, args + [limit, offset]).fetchall()
    except sqlite3.OperationalError:
        rows = con.execute(base % "", args + [limit, offset]).fetchall()

    def _tags(r):
        keys = r.keys()
        d = {}
        for col in ("imp_tags", "usr_tags"):
            blob = r[col] if col in keys else None
            for pair in (blob or "").split("\x1f"):
                if not pair:
                    continue
                origin, _, tag = pair.partition(":")
                if tag:
                    d.setdefault(tag, set()).add(origin)
        return [{"tag": t, "origins": sorted(o)}
                for t, o in sorted(d.items(), key=lambda kv: kv[0].lower())]

    def _attrs(r):
        """Per-game {kind: 'v1, v2'} for the optional attribute columns. Free-text
        'description' is dropped (too long for a table cell)."""
        blob = r["attrs"] if "attrs" in r.keys() else None
        d = {}
        for pair in (blob or "").split("\x1e"):
            if not pair:
                continue
            kind, _, val = pair.partition("\x1f")
            if kind and kind != "description" and val:
                d.setdefault(kind, []).append(val)
        return {k: ", ".join(v) for k, v in d.items()}

    items = [{
        "norm_key": r["norm_key"],
        "entry_key": r["entry_key"],
        "platform": r["platform"],
        "title": r["canonical_title"],
        "n_sources": r["n_sources"],
        "n_kinds": r["n_kinds"],
        "sources_summary": r["sources_summary"],
        "platforms": r["platforms"] or "",
        "emulation": bool(r["is_emulation"]),
        "matched": bool(r["matched"]),
        "identified": bool(r["identified"]),
        "has_cover": bool(r["has_cover"]),
        "cover_v": r["cover_v"] or None,
        "ludodex_score": round(r["ludodex_score"]) if r["ludodex_score"] is not None else None,
        "tags": _tags(r),
        "attrs": _attrs(r),
        "wanted": bool(r["wanted"]) if "wanted" in r.keys() else False,
    } for r in rows]
    # attach any saved cover framing so the grid renders it (only where set)
    covfr = framing.for_keys(DATA, [it["norm_key"] for it in items], "cover")
    for it in items:
        if it["norm_key"] in covfr:
            it["framing_cover"] = covfr[it["norm_key"]]
    return {"total": total, "hidden_unidentified": hidden,
            "limit": limit, "offset": offset, "items": items}


@app.get("/api/games")
def games(
    q: str = Query(None, description="substring match on title"),
    query: str = Query(None, description="advanced query-language search (field:value, -neg, year:>N)"),
    source: str = Query(None, description="filter to games available from this source"),
    platform: str = Query(None, description="filter by platform"),
    has_kind: str = Query(None, description="only games with a chosen asset of this kind"),
    include: str = Query(None, description="comma-list of source/status flags a game MUST have"),
    exclude: str = Query(None, description="comma-list of source/status flags a game must NOT have"),
    sort: str = Query(None, description="comma-list of sort keys in priority order (1st,2nd,3rd)"),
    status: str = Query("owned", description="ownership: owned (default) | wanted | all"),
    identified: str = Query("only", description="only (default, hide bare ROMs) | all | unidentified"),
    limit: int = Query(60, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    inc = [f for f in (include or "").split(",") if f]
    exc = [f for f in (exclude or "").split(",") if f]
    srt = [f for f in (sort or "").split(",") if f]
    con = lib()
    try:
        return _query_games(con, q, source, platform, has_kind, inc, exc, srt, limit, offset,
                            status=status if status in ("owned", "wanted", "all") else "owned",
                            identified=identified if identified in ("only", "all", "unidentified") else "only",
                            query=query)
    finally:
        con.close()


# --------------------------------------------------------------------- spotlight
# Platform code -> friendly label for spotlight titles (falls back to the raw code).
_PLAT_LABEL = {
    "snes": "Super Nintendo", "nes": "NES", "n64": "Nintendo 64", "gba": "Game Boy Advance",
    "gb": "Game Boy", "gbc": "Game Boy Color", "nds": "Nintendo DS", "3ds": "Nintendo 3DS",
    "gc": "GameCube", "wii": "Wii", "wiiu": "Wii U", "switch": "Switch",
    "genesis": "Sega Genesis", "megadrive": "Mega Drive", "dreamcast": "Dreamcast",
    "saturn": "Sega Saturn", "gamegear": "Game Gear", "sms": "Master System",
    "psx": "PlayStation", "ps2": "PlayStation 2", "ps3": "PlayStation 3", "psp": "PSP",
    "psvita": "PS Vita", "arcade": "Arcade", "mame": "Arcade (MAME)", "c64": "Commodore 64",
    "amiga": "Amiga", "dos": "DOS", "atari2600": "Atari 2600", "pcengine": "PC Engine",
    "neogeo": "Neo Geo", "3do": "3DO", "wonderswan": "WonderSwan", "lynx": "Atari Lynx",
}


def _spotlight_rows(con, where, args, order="gs.universal DESC", limit=10,
                    include_homebrew=False):
    # spotlight is a games showcase — never surface applications/tools/mods/etc.
    clauses = [where] if where else []
    args = list(args)
    if config.get_bool("hide_non_games", True):
        _ex, _exargs = _non_game_hidden_sql()
        clauses.append("NOT (" + _ex + ")")
        args += _exargs
    # Keep un-official releases (homebrew/hack/proto/demo/unlicensed — the editable
    # `release_type` attribute) out of the normal themes; the dedicated 'homebrew'
    # theme flips this on so they still have a home. A Translation is exempt — it IS
    # the real commercial game, just localized — so it stays in the normal themes.
    if not include_homebrew:
        clauses.append("NOT EXISTS(SELECT 1 FROM game_attributes ga WHERE "
                       "ga.game_id=g.id AND ga.kind='release_type' "
                       "AND ga.value<>'Translation')")
    clause = ("WHERE " + " AND ".join("(%s)" % c for c in clauses) + " ") if clauses else ""
    has_ek = _has_col(con, "games", "entry_key")
    eksel = ("g.entry_key AS entry_key, g.platform AS platform, " if has_ek
             else "g.norm_key AS entry_key, NULL AS platform, ")
    _mc = ("(SELECT substr(md.sha1,1,12) FROM m.media md WHERE md.norm_key=g.norm_key "
           "AND md.chosen=1 AND md.kind='cover'%s LIMIT 1)")
    _um = ("(SELECT substr(um.sha1,1,12) FROM u.user_media um WHERE um.norm_key=g.norm_key "
           "AND um.kind='cover' ORDER BY um.created DESC LIMIT 1)")
    # own console art or platform-neutral store art only — never another console's cover.
    # Neutral art is served only when its identity matches the entry (md.game_key =
    # g.game_key, DESIGN §11.9): an era-collision entry (game_key title:<nk>) forfeits the
    # resolved game's igdb:<id> neutral cover, a stray port (adopts igdb:<id>) keeps it.
    _own = " AND COALESCE(md.system,'')=COALESCE(g.platform,'')"
    _hasgk = _has_col(con, "games", "game_key")
    _neutral = " AND COALESCE(md.system,'')=''" + (" AND md.game_key=g.game_key" if _hasgk else "")
    # neutral art also reachable by game identity across norm_keys (see _query_games note).
    _mc_gk = ("(SELECT substr(md.sha1,1,12) FROM m.media md WHERE md.game_key=g.game_key "
              "AND md.chosen=1 AND md.kind='cover' AND COALESCE(md.system,'')='' LIMIT 1)")
    _neu_gk_ex = (" OR EXISTS(SELECT 1 FROM m.media md WHERE md.game_key=g.game_key AND "
                  "md.chosen=1 AND md.kind='cover' AND COALESCE(md.system,'')='')"
                  if _hasgk else "")
    if has_ek:
        _cv = [_um, _mc % _own, _mc % _neutral] + ([_mc_gk] if _hasgk else [])
        cover_v = "COALESCE(" + ",".join(_cv) + ") AS cover_v "
        has_cov = ("((EXISTS(SELECT 1 FROM m.media md WHERE md.norm_key=g.norm_key AND "
                   "md.chosen=1 AND md.kind='cover'" + _own + ") OR EXISTS(SELECT 1 FROM "
                   "m.media md WHERE md.norm_key=g.norm_key AND md.chosen=1 AND md.kind='cover'"
                   + _neutral + ")" + _neu_gk_ex + ") OR EXISTS(SELECT 1 FROM u.user_media um "
                   "WHERE um.norm_key=g.norm_key AND um.kind='cover')) AS has_cover, ")
    else:
        cover_v = "COALESCE(" + _um + "," + _mc % "" + ") AS cover_v "
        has_cov = ("(EXISTS(SELECT 1 FROM m.media md WHERE md.norm_key=g.norm_key AND "
                   "md.chosen=1 AND md.kind='cover') OR EXISTS(SELECT 1 FROM u.user_media "
                   "um WHERE um.norm_key=g.norm_key AND um.kind='cover')) AS has_cover, ")
    # one showcase row per game (a title shouldn't repeat once per platform); GROUP BY
    # the cross-ref base_key picks a representative entry, its platform driving the cover.
    grp = ("GROUP BY g.base_key " if _has_col(con, "games", "base_key")
           else "GROUP BY g.norm_key " if has_ek else "")
    sql = ("SELECT g.norm_key, " + eksel + "g.canonical_title AS title, gs.universal AS score, "
           "g.sources_summary AS sources, "
           "EXISTS(SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id) AS matched, "
           + has_cov
           + cover_v +
           "FROM games g LEFT JOIN sco.game_scores gs ON gs.norm_key=g.norm_key "
           + clause + grp
           + "ORDER BY " + order + ", g.canonical_title LIMIT ?")
    return [{"norm_key": r["norm_key"], "entry_key": r["entry_key"],
             "platform": r["platform"], "title": r["title"], "score": r["score"],
             "sources": r["sources"], "matched": bool(r["matched"]),
             "has_cover": bool(r["has_cover"]), "cover_v": r["cover_v"] or None}
            for r in con.execute(sql, args + [limit])]


def _spotlight_disabled():
    """Set of spotlight theme ids the user has switched off in settings."""
    raw = config.get("spotlight_disabled") or ""
    return {x.strip() for x in raw.split(",") if x.strip()}


_SPOTLIGHT_POOL_CACHE = {"key": None, "ids": None}


def _spotlight_all_ids(con):
    """Every concrete spotlight id worth showing, BEFORE the user's on/off filter.
    Platform/source/decade themes are keyed off GAME count (not score count) so the
    pool stays varied even when few games are scored — otherwise every theme
    collapses to 'overall' and repeats.

    Cached and invalidated by the library DB's mtime: the pool only changes when the
    catalog is rebuilt, so we don't re-run these GROUP-BY scans on every spotlight
    load / shuffle (they cost ~0.25s each otherwise)."""
    try:
        key = os.path.getmtime(LIBRARY_DB)
    except OSError:
        key = None
    if key is not None and _SPOTLIGHT_POOL_CACHE["key"] == key:
        return list(_SPOTLIGHT_POOL_CACHE["ids"])
    ids = _compute_spotlight_all_ids(con)
    _SPOTLIGHT_POOL_CACHE.update(key=key, ids=list(ids))
    return ids


def _compute_spotlight_all_ids(con):
    pool = ["overall", "emulation"]
    try:
        # score-only themes need a real sample of scored games, else they're thin
        # and just fall back to 'overall' — which is what makes it feel repetitive.
        if con.execute("SELECT COUNT(*) FROM sco.game_scores").fetchone()[0] >= 20:
            pool += ["underrated", "hidden_gems", "acclaimed", "beloved"]
        if con.execute("SELECT COUNT(DISTINCT game_id) FROM game_attributes "
                       "WHERE kind='release_type' AND value<>'Translation'"
                       ).fetchone()[0] >= 8:
            pool.append("homebrew")
        for (plat,) in con.execute(
            "SELECT s.platform FROM sources s JOIN games g ON g.id=s.game_id "
            "WHERE s.platform!='' AND s.platform NOT IN (%s) "
            "GROUP BY s.platform HAVING COUNT(DISTINCT g.norm_key)>=8"
            % ",".join("?" * len(NON_SYSTEM_PLATFORMS)), NON_SYSTEM_PLATFORMS):
            pool.append("platform:" + plat)
        for (src,) in con.execute(
            "SELECT DISTINCT s.source FROM sources s "
            "WHERE s.source IN ('steam','gog','epic','xbox','psn','ea','itch')"):
            pool.append("source:" + src)
        for (dec,) in con.execute(
            "SELECT (CAST(ga.value AS INT)/10)*10 d FROM game_attributes ga "
            "WHERE ga.kind='release_year' AND CAST(ga.value AS INT) BETWEEN 1970 AND 2035 "
            "GROUP BY d HAVING COUNT(DISTINCT ga.game_id)>=8"):
            pool.append("decade:%d" % dec)
    except sqlite3.OperationalError:
        pass
    return pool


def _spotlight_pool(con):
    """The rotation pool: all concrete ids minus the ones disabled in settings.
    Never returns empty — if the user disabled everything, 'overall' still shows."""
    off = _spotlight_disabled()
    pool = [k for k in _spotlight_all_ids(con) if k not in off]
    return pool or ["overall"]


def _warm_spotlight():
    """Prime the spotlight after a catalog rebuild (or on startup): the atomic swap
    gives game-library.sqlite a fresh inode (cold OS page cache) and invalidates the
    mtime-keyed theme-pool cache, so the FIRST dashboard request otherwise pays the
    cold GROUP-BY scans — while the post-sync media pipeline is still hammering the
    array. Recompute the pool + run the 'overall' query here (background) so the real
    request lands warm. Best-effort."""
    try:
        con = lib()
        try:
            _spotlight_pool(con)                       # repopulate _SPOTLIGHT_POOL_CACHE
            _spotlight_rows(con, "", [], "gs.universal DESC")  # warm catalog+media+score pages
        finally:
            con.close()
    except Exception as e:                             # never let warming break anything
        print("spotlight warm: %s" % str(e)[:120], file=sys.stderr)


def _warm_spotlight_bg():
    threading.Thread(target=_warm_spotlight, daemon=True).start()


@app.on_event("startup")
def _startup_warm_spotlight():
    _warm_spotlight_bg()              # warm on boot so the first dashboard load is snappy
    try:                             # reap scans orphaned 'running' by a prior process
        _reaped = aimeta.reap_running()   # (container restart/redeploy/crash mid-scan)
        if _reaped:
            print("startup: reaped %d orphaned scan run(s)" % _reaped, file=sys.stderr)
    except Exception as _e:
        print("startup: scan reap failed: %s" % _e, file=sys.stderr)


def _spotlight_catalog(con):
    """[{id, title, enabled}] for every theme, for the settings on/off list."""
    off = _spotlight_disabled()
    out = []
    for k in _spotlight_all_ids(con):
        title, subtitle, _w, _a, _o = _resolve_spotlight(k)
        out.append({"id": k, "title": title, "subtitle": subtitle,
                    "enabled": k not in off})
    return out


def _resolve_spotlight(kind):
    """kind -> (title, subtitle, where, args, order)."""
    if kind == "emulation":
        return ("Best on emulation", "Top games across your ROM library",
                "g.has_emulation=1", [], "gs.universal DESC")
    if kind == "homebrew":
        return ("Homebrew & Hacks", "Fan games, hacks, prototypes & demos you own",
                "EXISTS(SELECT 1 FROM game_attributes ga WHERE ga.game_id=g.id "
                "AND ga.kind='release_type' AND ga.value<>'Translation')", [],
                "gs.universal DESC")
    if kind == "underrated":
        return ("Underrated", "Players rate these higher than the critics did",
                "gs.critic IS NOT NULL AND gs.user IS NOT NULL AND gs.user-gs.critic>=8 "
                "AND gs.universal>=68", [], "(gs.user-gs.critic) DESC")
    if kind == "hidden_gems":
        return ("Hidden gems", "Great games you own in just one place",
                "gs.universal>=78 AND g.n_sources=1", [], "gs.universal DESC")
    if kind == "acclaimed":
        return ("Critically acclaimed", "Where the critics are all-in",
                "gs.critic>=85", [], "gs.critic DESC")
    if kind == "beloved":
        return ("Player favorites", "Loved by the people who actually play them",
                "gs.user>=88", [], "gs.user DESC")
    if kind.startswith("platform:"):
        p = kind.split(":", 1)[1]
        lbl = _PLAT_LABEL.get(p, p.upper() if len(p) <= 4 else p.title())
        return ("Best on %s" % lbl, "Top %s games you own" % lbl,
                "EXISTS(SELECT 1 FROM sources s WHERE s.game_id=g.id AND s.platform=?)",
                [p], "gs.universal DESC")
    if kind.startswith("source:"):
        s = kind.split(":", 1)[1]
        lbl = {"gog": "GOG", "epic": "Epic", "psn": "PlayStation",
               "xbox": "Xbox"}.get(s, s.title())
        return ("Best on %s" % lbl, "Top of your %s library" % lbl,
                "EXISTS(SELECT 1 FROM sources s WHERE s.game_id=g.id AND s.source=?)",
                [s], "gs.universal DESC")
    if kind.startswith("decade:"):
        d = int(kind.split(":", 1)[1])
        return ("Best of the %ds" % d, "Top games from %d–%d" % (d, d + 9),
                "EXISTS(SELECT 1 FROM game_attributes ga WHERE ga.game_id=g.id "
                "AND ga.kind='release_year' AND CAST(ga.value AS INT) BETWEEN ? AND ?)",
                [d, d + 9], "gs.universal DESC")
    return ("Top rated", "The highest-scoring games you own", "", [], "gs.universal DESC")


@app.get("/api/spotlight")
def spotlight(kind: str = Query("random"), exclude: str = Query(None)):
    """A themed top-N for the dashboard 'Spotlight'. `random` (default) rotates
    through overall / per-platform / per-store / per-decade / underrated / etc.
    `exclude` = the currently-shown theme id, so a rotation never repeats it."""
    con = lib()
    try:
        if kind == "random":
            pool = _spotlight_pool(con)
            if exclude and len(pool) > 1:      # don't show the same theme twice
                pool = [p for p in pool if p != exclude] or pool
            kind = random.choice(pool) if pool else "overall"
        title, subtitle, where, args, order = _resolve_spotlight(kind)
        items = _spotlight_rows(con, where, args, order,
                                include_homebrew=(kind == "homebrew"))
        if len(items) < 4 and kind != "overall":         # thin theme -> fall back
            kind = "overall"
            title, subtitle, where, args, order = _resolve_spotlight(kind)
            items = _spotlight_rows(con, where, args, order)
        return {"kind": kind, "title": title, "subtitle": subtitle, "items": items}
    finally:
        con.close()


@app.get("/api/spotlight/themes")
def spotlight_themes():
    """The full list of dashboard Spotlight themes with their on/off state, so the
    user can disable ones they never want to see (e.g. 'Best of Neo Geo')."""
    con = lib()
    try:
        return {"themes": _spotlight_catalog(con)}
    finally:
        con.close()


SPOTLIGHT_SECONDS_DEFAULT = 90
SPOTLIGHT_SECONDS_MIN = 3
SPOTLIGHT_SECONDS_MAX = 300


def _spotlight_seconds():
    try:
        v = int(config.get("spotlight_seconds") or SPOTLIGHT_SECONDS_DEFAULT)
    except (TypeError, ValueError):
        v = SPOTLIGHT_SECONDS_DEFAULT
    return max(SPOTLIGHT_SECONDS_MIN, min(SPOTLIGHT_SECONDS_MAX, v))


# --------------------------------------------------------------------------- #
#  Media storage: download chosen (or all) art into the local content-addressed
#  repo, per the media_mode preference. Complements materialize-on-serve (lazy).
# --------------------------------------------------------------------------- #
_MEDIA_JOB = {"job": None}
_MEDIA_LOCK = threading.Lock()


def _media_worker(mode):
    j = _MEDIA_JOB["job"]
    try:
        con = media_choose.con_index()
        j["step"] = "Choosing best assets…"
        media_choose.select(con)
        j["step"] = "Downloading media into the repo…"
        ok, dead = media_choose.materialize(con, all_refs=(mode == "all"))
        con.close()
        j.update({"ok": True, "downloaded": ok, "dead": dead, "step": "Done"})
    except Exception as e:
        j.update({"ok": False, "error": str(e)[:200], "step": "Failed"})
    finally:
        j["running"] = False
        j["finished"] = True


@app.post("/api/media/materialize")
def media_materialize(body: dict = Body(default={})):
    """Hydrate the local media repo now. mode defaults to the media_mode pref;
    'all' pulls every candidate, otherwise just the chosen asset per game/kind."""
    with _MEDIA_LOCK:
        cur = _MEDIA_JOB["job"]
        if cur and cur.get("running"):
            raise HTTPException(409, "a media download is already running")
        mode = (body or {}).get("mode") or config.get("media_mode") or "chosen"
        _MEDIA_JOB["job"] = {"running": True, "finished": False, "mode": mode,
                             "step": "Starting…", "ok": None, "downloaded": 0, "dead": 0}
    threading.Thread(target=_media_worker, args=(mode,), daemon=True).start()
    return {"media_job": _MEDIA_JOB["job"]}


@app.get("/api/media/materialize")
def media_materialize_status():
    return {"media_job": _MEDIA_JOB["job"]}


@app.get("/api/prefs")
def get_prefs():
    """Global app preferences (not per-service): hide non-game apps + how long each
    dashboard Spotlight stays before rotating."""
    return {
        "hide_non_games": config.get_bool("hide_non_games", True),
        "spotlight_seconds": _spotlight_seconds(),
        "spotlight_disabled": sorted(_spotlight_disabled()),
        "media_mode": config.get("media_mode") or "chosen",
        "media_language": config.get("media_language") or "",
        "media_languages": medialang.preferred(),
        "media_lang_mode": medialang.mode(),
        "fileops_apply_mode": config.get("fileops_apply_mode") or "preview",
        "manifests_enabled": config.get_bool("manifests_enabled", True),
        "xbox_platform": config.get("xbox_platform") or "xbox",
        "media_job": _MEDIA_JOB["job"],
    }


@app.post("/api/prefs")
def set_prefs(body: dict = Body(...)):
    body = body or {}
    if body.get("media_mode") in ("ondemand", "chosen", "all"):
        config.set_("media_mode", body["media_mode"])
    if body.get("xbox_platform") in ("xbox", "pc"):   # bucket for inbound Xbox games
        config.set_("xbox_platform", body["xbox_platform"])
    if "media_language" in body:                # "" = no preference (any language)
        config.set_("media_language", str(body["media_language"] or "")[:40])
    if "media_languages" in body:               # ordered 1st,2nd,3rd preference
        langs = body["media_languages"] or []
        if isinstance(langs, str):
            langs = [langs]
        clean = [x for x in (medialang._norm_lang(x) for x in langs[:3]) if x]
        config.set_("media_languages", ",".join(clean))
        config.set_("media_language", clean[0] if clean else "")   # AI picker follows 1st
    if body.get("media_lang_mode") in ("off", "hide", "ban"):
        config.set_("media_lang_mode", body["media_lang_mode"])
    if body.get("fileops_apply_mode") in ("preview", "immediate"):
        config.set_("fileops_apply_mode", body["fileops_apply_mode"])
    if "manifests_enabled" in body:
        on = bool(body["manifests_enabled"])
        config.set_("manifests_enabled", "1" if on else "0")
        if not on:                              # honor the opt-out: sweep existing manifests
            for did, p in devices.all_managed_paths():
                try:
                    fileops.manifest_delete(did, p)
                except Exception:               # noqa: BLE001 — best-effort cleanup
                    pass
    if "hide_non_games" in body:
        config.set_("hide_non_games", "1" if body["hide_non_games"] else "0")
    if "spotlight_seconds" in body:
        try:
            v = max(SPOTLIGHT_SECONDS_MIN,
                    min(SPOTLIGHT_SECONDS_MAX, int(body["spotlight_seconds"])))
            config.set_("spotlight_seconds", str(v))
        except (TypeError, ValueError):
            pass
    if "spotlight_disabled" in body:            # ids the user switched off
        ids = body["spotlight_disabled"] or []
        if isinstance(ids, str):
            ids = [ids]
        clean = sorted({str(x).strip() for x in ids if str(x).strip()})
        config.set_("spotlight_disabled", ",".join(clean))
    return get_prefs()


@app.post("/api/media/language-filter")
def media_language_filter(body: dict = Body(default={})):
    """Apply the language preference to the existing media index now (without a
    full sync). Optional body {mode: off|hide|ban} overrides the saved mode for
    this run. Re-picks chosen art afterward. Returns the per-run counts."""
    m = (body or {}).get("mode")
    if m not in (None, "off", "hide", "ban"):
        raise HTTPException(400, "mode must be off, hide or ban")
    try:
        res = medialang.apply_filter(m)
    except Exception as e:
        raise HTTPException(500, "language filter failed: %s" % e)
    _run_script("media_choose.py", timeout=900)   # re-pick now survivors changed
    return res


# --------------------------------------------------------------------------- #
#  Emulation storage locations. A location holds ROMs, Media, or both:
#    roms  -> an `archives` row (scanned by crawl/build_romdb)
#    media -> a `media_mounts` ES-DE row + a per-location media-kinds filter
#    both  -> the same path registered in BOTH (default)
#  Registers pre-mounted paths; status reflects how the path looks on disk now.
# --------------------------------------------------------------------------- #
def _emu_locations():
    """Merge ROM archives + media mounts into one list keyed by name."""
    by_name = {}
    for a in config.archives_list():
        by_name[a["name"]] = {"name": a["name"], "path": a["path"],
                              "role": "roms", "kinds": [], "enabled": bool(a["enabled"])}
    for m in config.media_mounts_list(provider="esde"):
        e = by_name.get(m["name"])
        if e:                                    # in both tables -> combined
            e["role"] = "both"
            e["kinds"] = m["kinds"]
            e["enabled"] = e["enabled"] and bool(m["enabled"])
        else:
            by_name[m["name"]] = {"name": m["name"], "path": m["path"],
                                  "role": "media", "kinds": m["kinds"],
                                  "enabled": bool(m["enabled"])}
    out = [dict(e, status=config.path_status(e["path"]))
           for e in by_name.values()]
    out.sort(key=lambda e: e["name"].lower())
    return {"locations": out}


@app.get("/api/archives")
def list_emu_locations():
    return _emu_locations()


@app.post("/api/archives")
def set_emu_location(body: dict = Body(...)):
    body = body or {}
    name = (body.get("name") or "").strip()
    path = (body.get("path") or "").strip()
    role = (body.get("role") or "both").strip().lower()
    if not name or not path:
        raise HTTPException(400, "both a name and a path are required")
    if role not in ("roms", "media", "both"):
        role = "both"
    enabled = 1 if body.get("enabled", True) else 0
    kinds = [k for k in (body.get("kinds") or []) if k in media.KINDS]
    # write to the right table(s); drop the other so a role change is clean
    if role in ("roms", "both"):
        config.archive_set(name, path, "rom", enabled)
    else:
        config.archive_rm(name)
    if role in ("media", "both"):
        config.media_mount_set(name, path, "esde", enabled, kinds)
    else:
        config.media_mount_rm(name)
    return _emu_locations()


@app.delete("/api/archives/{name}")
def remove_emu_location(name: str):
    config.archive_rm(name)
    config.media_mount_rm(name)
    return _emu_locations()


@app.post("/api/archives/{name}/enabled")
def set_emu_location_enabled(name: str, body: dict = Body(...)):
    on = bool((body or {}).get("enabled"))
    config.archive_set_enabled(name, on)
    config.media_mount_set_enabled(name, on)
    return _emu_locations()


# --------------------------------------------------------------------------- #
#  Add a game manually: identify across providers (IGDB) by name, or recognize
#  games from uploaded images (AI vision). Adds persist to manual-games.sqlite
#  (durable) and are inserted into the live catalog immediately.
# --------------------------------------------------------------------------- #
_IGDB_TOK = {"token": None, "exp": 0.0, "cid": None}


def _igdb_token():
    cid = config.get("igdb_client_id")
    secret = config.get("igdb_client_secret")
    if not (cid and secret):
        return None, None
    now = time.time()
    if _IGDB_TOK["token"] and _IGDB_TOK["cid"] == cid and _IGDB_TOK["exp"] > now + 60:
        return cid, _IGDB_TOK["token"]
    import igdb
    try:
        tok, ttl = igdb.get_token(cid, secret)
    except Exception:
        return None, None
    _IGDB_TOK.update(token=tok, exp=now + (ttl or 3600), cid=cid)
    return cid, tok


def _igdb_hits(raw):
    """Normalize raw IGDB game rows -> our candidate dicts (id, name, year, ...)."""
    out = []
    for h in raw or []:
        img = (h.get("cover") or {}).get("image_id")
        yr = None
        if h.get("first_release_date"):
            try:
                yr = time.gmtime(h["first_release_date"]).tm_year
            except (ValueError, OverflowError, OSError):
                yr = None
        out.append({
            "igdb_id": h.get("id"), "name": h.get("name"), "year": yr,
            "platforms": [p.get("abbreviation") for p in (h.get("platforms") or [])
                          if p.get("abbreviation")],
            "cover": ("https://images.igdb.com/igdb/image/upload/t_cover_small/"
                      "%s.jpg" % img) if img else None,
        })
    return out


_IGDB_FIELDS = ("fields id,name,slug,first_release_date,"
                "platforms.abbreviation,cover.image_id;")


def _igdb_search(name, limit=8):
    """IGDB free-text search -> candidate matches (id, name, year, platforms, cover)."""
    cid, tok = _igdb_token()
    if not tok:
        return []
    import igdb
    body = 'search "%s"; %s limit %d;' % (name.replace('"', ""), _IGDB_FIELDS, limit)
    try:
        return _igdb_hits(igdb.query("games", body, cid, tok))
    except Exception:
        return []


def _igdb_by_name(name, limit=15):
    """Case-insensitive EXACT-name lookup. IGDB's relevance `search` buries an exact
    short title under its own sequels ("Gradius" ranks below Gradius II/III/IV/V), so a
    title the AI is confident about can be missing from search results entirely. `~` is
    a case-insensitive exact match — it returns only games literally named `name` (a
    small, deterministic set: original + re-releases), which the caller filters/ranks —
    unlike a `*"..."*` contains query, whose arbitrary limited window can omit the very
    entry we need."""
    cid, tok = _igdb_token()
    if not tok:
        return []
    import igdb
    safe = name.replace('"', "").replace("\\", "").replace("*", "")
    # sort by release date asc so the ORIGINAL lands in the window (IGDB's default order
    # can push a modern re-release ahead of the 1986 original and cap it out at `limit`).
    body = ('%s where name ~ "%s"; sort first_release_date asc; limit %d;'
            % (_IGDB_FIELDS, safe, limit))
    try:
        return _igdb_hits(igdb.query("games", body, cid, tok))
    except Exception:
        return []


@app.get("/api/identify")
def identify_game(name: str = Query(...)):
    """Search providers for a game by name → candidate matches to confirm."""
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "a name is required")
    cands = _igdb_search(name)
    return {"query": name, "candidates": cands,
            "provider": "igdb" if cands or _igdb_token()[1] else None}


def _refresh_game_row(con, gid, new_source):
    """Recompute n_sources/n_kinds/summary (+ set has_<source>) after adding a row."""
    srcs = con.execute("SELECT source, platform FROM sources WHERE game_id=?",
                       (gid,)).fetchall()
    kinds = {}
    for s, p in srcs:
        kinds.setdefault(s, set()).add(p)
    parts = [grp + ":" + ",".join(sorted(kinds[grp]))
             for grp in ("emulation", "archive") if grp in kinds]
    parts += sorted(k for k in kinds if k not in ("emulation", "archive"))
    sets = "sources_summary=?, n_sources=?, n_kinds=?"
    args = ["; ".join(parts), len(srcs), len(kinds)]
    if new_source in COLUMN_SOURCES:
        sets += ", has_%s=1" % new_source
    con.execute("UPDATE games SET %s WHERE id=?" % sets, args + [gid])


def _insert_source_row(nk, title, source, platform, detail=""):
    """Add a source row to the live catalog (creating the game if new). Returns
    True if a brand-new game row was created."""
    sid = "manual:" + nk
    con = sqlite3.connect(LIBRARY_DB, timeout=15)
    try:
        con.execute("PRAGMA busy_timeout=15000")
        g = con.execute("SELECT id FROM games WHERE norm_key=?", (nk,)).fetchone()
        if g:
            gid = g[0]
            dup = con.execute("SELECT 1 FROM sources WHERE game_id=? AND source=? "
                              "AND platform=?", (gid, source, platform)).fetchone()
            if not dup:
                con.execute("INSERT INTO sources(game_id,source,platform,source_id,"
                            "title_raw,detail) VALUES(?,?,?,?,?,?)",
                            (gid, source, platform, sid, title, detail))
                _refresh_game_row(con, gid, source)
            con.commit()
            return False
        summary = (source + ":" + platform) if source in ("emulation", "archive") else source
        flags = {c: 0 for c in ("emulation", "steam", "gog", "epic", "itch", "archive")}
        if source in COLUMN_SOURCES:
            flags[source] = 1
        cur = con.execute(
            "INSERT INTO games(canonical_title,norm_key,n_sources,n_kinds,"
            "sources_summary,has_emulation,has_steam,has_gog,has_epic,has_itch,"
            "has_archive,in_playnite,in_launchbox) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (title, nk, 1, 1, summary, flags["emulation"], flags["steam"],
             flags["gog"], flags["epic"], flags["itch"], flags["archive"], 0, 0))
        con.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw,"
                    "detail) VALUES(?,?,?,?,?,?)",
                    (cur.lastrowid, source, platform, sid, title, detail))
        con.commit()
        return True
    finally:
        con.close()


@app.post("/api/games/add")
def add_game(body: dict = Body(...)):
    """Add a game by name + source + system. Persists durably (survives rebuilds)
    and inserts into the live catalog so it appears immediately."""
    body = body or {}
    title = (body.get("title") or "").strip()
    source = (body.get("source") or "manual").strip().lower()
    platform = (body.get("platform") or "").strip() or source
    detail = (body.get("detail") or "").strip()
    if not title:
        raise HTTPException(400, "a title is required")
    nk = titlenorm.norm(title)
    if not nk:
        raise HTTPException(400, "couldn't make a key from that title")
    mc = _manual_con()
    try:
        mc.execute("INSERT OR REPLACE INTO manual_games(norm_key,title,source,"
                   "platform,detail,added) VALUES(?,?,?,?,?,?)",
                   (nk, title, source, platform, detail, time.time()))
        mc.commit()
    finally:
        mc.close()
    new_game = _insert_source_row(nk, title, source, platform, detail)
    return {"ok": True, "norm_key": nk, "new_game": new_game}


def _decode_data_url(d, max_px=1536):
    """data:<mime>;base64,<data> -> (mime, bytes), downscaled if large. (None,None) on failure."""
    m = re.match(r"data:([^;]+);base64,(.+)$", d or "", re.S)
    if not m:
        return None, None
    import base64
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        return None, None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px))
            if im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            return "image/jpeg", buf.getvalue()
    except Exception:
        pass
    return m.group(1), raw


@app.post("/api/games/identify-image")
def identify_image(body: dict = Body(...)):
    """Recognize every game across uploaded image(s) via the AI vision model.
    `images` = list of data-URL strings. Returns candidate games to add."""
    if not ai.area_available("identify"):
        raise HTTPException(503, "AI image recognition isn't set up — set an image-"
                            "analysis default (a vision model) in Settings › AI.")
    images = []
    for d in ((body or {}).get("images") or [])[:8]:
        mime, data = _decode_data_url(d)
        if data:
            images.append((mime, data))
    if not images:
        raise HTTPException(400, "no images provided")
    try:
        games = ai.identify_games(images)
    except Exception as e:
        raise HTTPException(502, "image recognition failed: %s" % e)
    return {"games": games, "count": len(games)}


IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def _image_file_scaled(path, max_px=1536):
    """Load an image file, downscaled for vision. (mime, bytes) or None."""
    try:
        from PIL import Image
        im = Image.open(path)
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px))
        if im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return "image/jpeg", buf.getvalue()
    except Exception:
        return None


@app.post("/api/games/identify-folder")
def identify_folder(body: dict = Body(...)):
    """Recognize games across every image in a server-side folder (for when the
    user has a lot). Walks the folder, sends images to the vision model in small
    batches, aggregates + de-dups the recognized games."""
    if not ai.area_available("identify"):
        raise HTTPException(503, "AI image recognition isn't set up — set an image-"
                            "analysis default (a vision model) in Settings › AI.")
    body = body or {}
    path = (body.get("path") or "").strip()
    if not path or not os.path.isdir(path):
        raise HTTPException(400, "that folder isn't a directory on the server")
    limit = max(1, min(int(body.get("limit") or 60), 300))
    batch = max(1, min(int(body.get("batch") or 6), 12))
    files = []
    for root, _dirs, fns in os.walk(path):
        for fn in sorted(fns):
            if fn.lower().endswith(IMG_EXTS):
                files.append(os.path.join(root, fn))
    total = len(files)
    files = files[:limit]
    seen, errs = {}, 0
    for i in range(0, len(files), batch):
        imgs = [im for im in (_image_file_scaled(fp) for fp in files[i:i + batch]) if im]
        if not imgs:
            continue
        try:
            for g in ai.identify_games(imgs):
                k = (g.get("title") or "").strip().lower()
                if k and k not in seen:
                    seen[k] = g
        except Exception:
            errs += 1
    games = sorted(seen.values(), key=lambda g: (g.get("title") or "").lower())
    return {"games": games, "count": len(games), "scanned": len(files),
            "total_found": total, "batch_errors": errs}


# --------------------------------------------------------------------------- #
#  Connections › Devices: machines hosting library managers (RetroDECK/ES-DE,
#  RetroBat, Playnite, LaunchBox…), reached over SSH to pull ROMs + media.
# --------------------------------------------------------------------------- #
try:                       # fold legacy Emulation-storage locations into Connections
    devices.migrate_storage()
except Exception as _e:    # noqa: BLE001 — never block startup on the migration
    print("storage migration skipped: %s" % _e, file=sys.stderr)


@app.get("/api/devices")
def list_devices():
    return {"devices": devices.devices_list(), "lm_kinds": devices.LM_KINDS}


@app.post("/api/devices/migrate-storage")
def migrate_storage_ep():
    return devices.migrate_storage()


@app.post("/api/devices")
def set_device(body: dict = Body(...)):
    body = body or {}
    if not (body.get("name") or "").strip():
        raise HTTPException(400, "a device name is required")
    devices.device_set(body)
    return {"devices": devices.devices_list()}


@app.delete("/api/devices/{dev_id}")
def remove_device(dev_id: int):
    devices.device_rm(dev_id)
    return {"devices": devices.devices_list()}


@app.post("/api/devices/{dev_id}/test")
def test_device(dev_id: int):
    d = devices._device(dev_id)
    if not d:
        raise HTTPException(404, "no such device")
    return devices.test_connection(d)


@app.post("/api/devices/browse")
def browse_device(body: dict = Body(default={})):
    """List child directories of a path on a device — for ROM/media path
    autocomplete. device_id 0/absent = the local ludodex host/container."""
    raw = (body or {}).get("device_id")
    dev_id = int(raw) if str(raw).isdigit() else 0
    return devices.browse_dirs(dev_id, (body or {}).get("path") or "/")


@app.post("/api/devices/browse-entries")
def browse_entries_ep(body: dict = Body(default={})):
    """Immediate dirs (with child counts) + files (with sizes) of a path on a
    device — powers the read-only Files › Browse tree. Lazy, one level per expand."""
    raw = (body or {}).get("device_id")
    dev_id = int(raw) if str(raw).isdigit() else 0
    return devices.browse_entries(dev_id, (body or {}).get("path") or "/")


# --- Device wishlist: "I want these games on that device" (intent only) ------ #
@app.get("/api/wants")
def wants_summary():
    """Wanted-game counts per device id (for badges + the library filter list)."""
    return {"counts": {str(k): v for k, v in devices.wants_counts().items()}}


@app.get("/api/devices/{dev_id}/wants")
def device_wants_list(dev_id: int):
    """Games on a device's wishlist, as full catalog rows (title, cover, tags…)."""
    con = lib()
    try:
        res = _query_games(con, include=["wanted:%d" % dev_id], limit=1000)
    finally:
        con.close()
    return {"wants": res["items"], "total": res["total"]}


@app.post("/api/devices/{dev_id}/wants")
def device_wants_add(dev_id: int, body: dict = Body(...)):
    """Add games to a device's wishlist. Emulation games only for now — any
    non-emulation keys are skipped. Returns {added, skipped}."""
    if not devices._device(dev_id):
        raise HTTPException(404, "no such device")
    keys = [k for k in ((body or {}).get("norm_keys") or []) if k]
    if not keys:
        return {"added": 0, "skipped": 0}
    con = lib()
    try:
        ph = ",".join("?" * len(keys))
        emu = {r[0] for r in con.execute(
            "SELECT norm_key FROM games WHERE has_emulation=1 AND norm_key IN (%s)"
            % ph, keys)}
    finally:
        con.close()
    eligible = [k for k in keys if k in emu]
    added = devices.wants_add(dev_id, eligible) if eligible else 0
    return {"added": added, "skipped": len(keys) - len(eligible)}


@app.delete("/api/devices/{dev_id}/wants/{norm_key:path}")
def device_wants_remove(dev_id: int, norm_key: str):
    devices.wants_remove(dev_id, norm_key)
    return {"ok": True}


# --- Collections / compilations (DESIGN §13) -------------------------------- #
@app.get("/api/collections")
def collections_list():
    """Every recorded compilation + its member count."""
    return {"collections": compilations.all_collections(DATA)}


@app.get("/api/collections/{coll_key:path}")
def collection_get(coll_key: str):
    c = compilations.get_collection(DATA, _split_entry_key(coll_key)[0])
    if not c:
        raise HTTPException(404, "not a collection")
    return c


@app.post("/api/collections/{coll_key:path}")
def collection_set(coll_key: str, body: dict = Body(...)):
    """Mark an entry as a compilation and (re)set its members. Body:
    {name, members:[{title, platform?, year?}]}. Manual curation path."""
    base = _split_entry_key(coll_key)[0]
    name = ((body or {}).get("name") or "").strip()
    members = (body or {}).get("members") or []
    if not name:
        raise HTTPException(400, "name required")
    n = compilations.set_collection(DATA, base, name, members, origin="manual")
    return {"coll_key": base, "name": name, "members": n}


@app.delete("/api/collections/{coll_key:path}")
def collection_delete(coll_key: str):
    compilations.clear_collection(DATA, _split_entry_key(coll_key)[0])
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Device SYNC — two-way file sync (Files → Sync tab)
#    push:   master archive ─▶ device (queued "wants")
#    ingest: device ─▶ master archive (ROMs the archive doesn't have)
#  These endpoints PREVIEW the work (read-only); the run endpoints execute it.
# --------------------------------------------------------------------------- #
def _archive_manager():
    """The master ROM repo = a 'roms' manager on the local host. Returns (device, mgr)
    or (None, None). This is where pushes pull FROM and ingests copy INTO."""
    for d in devices.devices_list():
        if d.get("transport") != "local":
            continue
        for m in d.get("managers", []):
            if m.get("kind") in ("roms", "retrodeck", "esde", "retrobat") and m.get("rom_path"):
                return d, m
    return None, None


def _game_gamelist_meta(con, norm_key):
    """Catalog metadata for a game, shaped for an ES-DE gamelist entry."""
    g = con.execute("SELECT id, canonical_title FROM games WHERE norm_key=?",
                    (norm_key,)).fetchone()
    if not g:
        return None
    attrs = {}
    for r in con.execute("SELECT kind, value FROM game_attributes WHERE game_id=?",
                         (g["id"],)):
        attrs.setdefault(r["kind"], []).append(r["value"])
    ov = overrides.overrides_for(norm_key)
    for k, o in ov.items():
        attrs[k] = [o["value"]]
    first = lambda k: (attrs.get(k) or [None])[0]
    plats = attrs.get("platforms") or []
    return {
        "title": g["canonical_title"],
        "desc": first("description"),
        "developer": ", ".join(attrs.get("developers") or [])[:200] or None,
        "publisher": ", ".join(attrs.get("publishers") or [])[:200] or None,
        "genre": ", ".join(attrs.get("genres") or [])[:120] or None,
        "release_year": first("release_year"), "release_date": first("release_date"),
        "players": first("player_count") or first("players"),
        "platform": plats[0] if plats else None,
    }


def _game_systems(con, norm_key):
    """The emulation platform(s) a wanted game is on (source='emulation')."""
    return [r["platform"] for r in con.execute(
        "SELECT DISTINCT s.platform FROM sources s JOIN games g ON g.id=s.game_id "
        "WHERE g.norm_key=? AND s.source='emulation' AND s.platform!=''", (norm_key,))]


@app.get("/api/devices/{dev_id}/sync/push-plan")
def sync_push_plan(dev_id: int):
    """Preview what pushing this device's queued games would do: per game the chosen
    ROM file(s), format conversion, media, and gamelist entry — plus free space."""
    devs = {d["id"]: d for d in devices.devices_list()}
    dev = devs.get(dev_id)
    if not dev:
        raise HTTPException(404, "no such device")
    mgr = next((m for m in dev.get("managers", [])
                if m.get("kind") in ("retrodeck", "esde", "retrobat")), None)
    if not mgr:
        raise HTTPException(400, "this device has no RetroDECK/ES-DE library manager")
    _adev, amgr = _archive_manager()
    if not amgr:
        raise HTTPException(400, "no master ROM archive configured (a local 'roms' manager)")
    keys = devices.wants_keys(dev_id)
    con = lib()
    items, missing = [], []
    try:
        for nk in keys:
            meta = _game_gamelist_meta(con, nk)
            systems = _game_systems(con, nk)
            placed = False
            for platform in systems:
                es = devicesync.esde_system(platform)
                hits = devicesync.resolve_roms(amgr["id"], platform, _emu_game_name(con, nk, platform))
                if not hits:
                    continue
                discs = devicesync.pick_rom_files(hits, es)
                conv = [devicesync.convert_plan(es, d["entry"].rsplit(".", 1)[-1]) for d in discs]
                media = devicesync.chosen_media_files(INDEX_DB, REPO, nk)
                items.append({
                    "norm_key": nk, "title": meta["title"] if meta else nk,
                    "system": es, "n_discs": len(discs),
                    "rom_files": [os.path.basename(d["entry"]) for d in discs],
                    "conversions": [t for _, t in conv],
                    "multi_disc": len(discs) > 1,
                    "media": sorted(media.keys()),
                    "has_gamelist_meta": bool(meta and meta.get("desc")),
                })
                placed = True
                break
            if not placed:
                missing.append({"norm_key": nk, "title": meta["title"] if meta else nk,
                                "reason": "no ROM found in the archive for its system"})
    finally:
        con.close()
    free = devicesync.device_free_bytes(dev, mgr.get("rom_path"))
    return {"device": dev["name"], "target": mgr.get("rom_path"),
            "queued": len(keys), "ready": len(items), "missing": missing,
            "free_bytes": free, "items": items}


def _emu_game_name(con, norm_key, platform):
    """The emulation source's title_raw for a game on a platform (what the ROM index
    grouped it under), falling back to the canonical title."""
    r = con.execute(
        "SELECT s.title_raw FROM sources s JOIN games g ON g.id=s.game_id "
        "WHERE g.norm_key=? AND s.source='emulation' AND s.platform=? LIMIT 1",
        (norm_key, platform)).fetchone()
    if r and r["title_raw"]:
        return r["title_raw"]
    g = con.execute("SELECT canonical_title FROM games WHERE norm_key=?",
                    (norm_key,)).fetchone()
    return g["canonical_title"] if g else norm_key


@app.get("/api/devices/{dev_id}/sync/ingest-plan")
def sync_ingest_plan(dev_id: int):
    """Preview the reverse: ROMs on this device that the master archive doesn't have
    yet — candidates to pull back into the archive."""
    devs = {d["id"]: d for d in devices.devices_list()}
    dev = devs.get(dev_id)
    if not dev:
        raise HTTPException(404, "no such device")
    mgr = next((m for m in dev.get("managers", []) if m.get("rom_path")), None)
    if not mgr:
        raise HTTPException(400, "this device has no ROM library manager")
    _adev, amgr = _archive_manager()
    if not amgr:
        raise HTTPException(400, "no master ROM archive configured")
    if mgr["id"] == amgr["id"]:
        raise HTTPException(400, "this device IS the archive")
    if not os.path.exists(os.path.join(DATA, "roms-index-mgr%d.sqlite" % mgr["id"])):
        raise HTTPException(409, "sync this device first so its ROMs are indexed")
    games = devicesync.diff_ingest(mgr["id"], amgr["id"], limit=500)
    n_files = sum(g["n_files"] for g in games)
    by_system = {}
    for g in games:
        by_system[g["system"]] = by_system.get(g["system"], 0) + 1
    return {"device": dev["name"], "archive": amgr.get("rom_path"),
            "new_games": len(games), "new_files": n_files,
            "by_system": sorted(by_system.items(), key=lambda x: -x[1]),
            "games": games[:200]}


@app.post("/api/media/scan-local")
def media_scan_local(body: dict = Body(default={})):
    """Index EmulationStation/RetroArch art that lives INSIDE a device's ROM tree,
    in place — no move — so existing local covers show up. Local paths only (the
    server must be able to read the files). Runs in the background as a job."""
    dev_id = _dev_id(body)                       # 0 = local host/container
    dev = devices._device(dev_id) if dev_id else None
    if dev and (dev.get("transport") or "local") != "local":
        raise HTTPException(400, "index-in-place needs a locally-mounted path; for a "
                                 "remote device, pull its media via ROM sync first")
    root = ((body or {}).get("root") or "").strip()
    roots = [root] if root else devices.rom_paths(dev_id)
    roots = [r for r in roots if r]
    if not roots:
        raise HTTPException(400, "no ROM path to scan for art")

    def run(should_stop):
        for r in roots:
            subprocess.run([sys.executable, os.path.join(DIR, "media_index.py"),
                            "--gamelist", r], timeout=1800, cwd=DIR)
            if should_stop():
                return
        # pick the winning asset per (game, kind) for the kinds gamelist supplies,
        # so the newly-indexed covers become the chosen art the UI shows.
        subprocess.run([sys.executable, os.path.join(DIR, "media_choose.py"),
                        "--kinds", "cover,screenshot,logo"], timeout=1800, cwd=DIR)

    _start_job("artscan:%d" % dev_id, "artscan", "Indexing local art", run)
    return {"started": True, "roots": roots}


@app.post("/api/devices/{dev_id}/sync")
def sync_device_ep(dev_id: int):
    if not devices._device(dev_id):
        raise HTTPException(404, "no such device")
    try:
        return devices.sync_device(dev_id)
    except Exception as e:
        raise HTTPException(502, "device sync failed: %s" % e)


@app.post("/api/devices/managers")
def set_manager(body: dict = Body(...)):
    body = body or {}
    if not body.get("device_id") or not body.get("kind"):
        raise HTTPException(400, "device_id and kind are required")
    if "media_kinds" in body:      # keep only real media kinds; [] = all
        body["media_kinds"] = [k for k in (body.get("media_kinds") or [])
                               if k in media.KINDS]
    devices.manager_set(body)
    return {"devices": devices.devices_list()}


@app.delete("/api/devices/managers/{mid}")
def remove_manager(mid: int):
    devices.manager_rm(mid)
    return {"devices": devices.devices_list()}


# --------------------------------------------------------------------------- #
#  File-operations engine: profiles + runbooks over any device path.
#  Flow: detect → plan (preview) → runbook (persist) → execute (background,
#  pausable/resumable) → undo/troubleshoot. AI can infer a profile or turn a
#  natural-language request into a plan.
# --------------------------------------------------------------------------- #
def _dev_id(body):
    v = (body or {}).get("device_id")
    return int(v) if v not in (None, "", "local") else 0


def _fileops_ctx(device_id, root, scope, system):
    """Detect current layout + build the context strings the AI areas consume."""
    det = fileops.detect(device_id, root, scope, system)
    variables_text = "; ".join("{%s} = %s (e.g. %s)" % (v[0], v[2], v[3])
                               for v in fileops.VARIABLES)
    systems_text = ", ".join(det["systems"]) or "(none detected)"
    sample_text = "\n".join(det["sample"][:150])
    return det, variables_text, systems_text, sample_text


@app.get("/api/fileops/variables")
def fileops_variables():
    return {"variables": [{"token": v[0], "label": v[1], "description": v[2],
                           "example": v[3]} for v in fileops.VARIABLES]}


@app.get("/api/fileops/profiles")
def fileops_profiles():
    return {"profiles": fileops.profiles_list()}


@app.post("/api/fileops/profiles")
def fileops_profile_save(body: dict = Body(...)):
    try:
        pid = fileops.profile_set(body or {})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": pid, "profiles": fileops.profiles_list()}


@app.delete("/api/fileops/profiles/{pid:path}")
def fileops_profile_delete(pid: str):
    fileops.profile_rm(pid)
    return {"profiles": fileops.profiles_list()}


@app.post("/api/fileops/detect")
def fileops_detect_ep(body: dict = Body(...)):
    root = (body or {}).get("root")
    if not root:
        raise HTTPException(400, "a root path is required")
    try:
        return fileops.detect(_dev_id(body), root, body.get("scope", "multi_system"),
                              body.get("system"))
    except Exception as e:
        raise HTTPException(502, "scan failed: %s" % e)


@app.post("/api/fileops/plan")
def fileops_plan_ep(body: dict = Body(...)):
    root, profile = (body or {}).get("root"), (body or {}).get("profile")
    if not root or not profile:
        raise HTTPException(400, "root and profile are required")
    try:
        pl = fileops.plan(_dev_id(body), root, profile,
                          body.get("scope", "multi_system"), body.get("system"))
    except Exception as e:
        raise HTTPException(502, "planning failed: %s" % e)
    return {"summary": pl["summary"], "warnings": pl["warnings"],
            "sample": pl["sample"]}


@app.post("/api/fileops/plan-extract")
def fileops_plan_extract_ep(body: dict = Body(...)):
    """Preview the 'extract media' operation: media tangled in a ROM tree → a clean
    ES-DE downloaded_media/ tree (dest relative to root). No ROM files touched."""
    root = (body or {}).get("root")
    if not root:
        raise HTTPException(400, "a root path is required")
    try:
        pl = fileops.plan_extract(_dev_id(body), root,
                                  (body or {}).get("dest") or "downloaded_media",
                                  body.get("scope", "multi_system"), body.get("system"),
                                  layout=(body or {}).get("layout") or "esde",
                                  op=(body or {}).get("op") or "move")
    except Exception as e:
        raise HTTPException(502, "planning failed: %s" % e)
    return {"summary": pl["summary"], "warnings": pl["warnings"],
            "sample": pl["sample"]}


@app.get("/api/fileops/media-layouts")
def fileops_media_layouts():
    return {"layouts": fileops.media_layouts()}


@app.post("/api/fileops/model-source")
def fileops_model_source_ep(body: dict = Body(...)):
    """AI-describe the CURRENT layout (system/group folders + intermixed media) for
    the Before panel."""
    root = (body or {}).get("root")
    if not root:
        raise HTTPException(400, "a root path is required")
    did = _dev_id(body)
    det, _vars_t, sys_t, sample_t = _fileops_ctx(
        did, root, body.get("scope", "multi_system"), body.get("system"))
    try:
        model = ai.model_source_layout(sample_t, sys_t, det["current"])
    except Exception as e:
        raise HTTPException(502, "AI modeling failed: %s" % e)
    return {"model": model, "detected": det}


@app.post("/api/fileops/infer")
def fileops_infer_ep(body: dict = Body(...)):
    root = (body or {}).get("root")
    if not root:
        raise HTTPException(400, "a root path is required")
    did = _dev_id(body)
    det, vars_t, sys_t, sample_t = _fileops_ctx(
        did, root, body.get("scope", "multi_system"), body.get("system"))
    try:
        prof = ai.infer_file_profile(sample_t, sys_t, vars_t, det["current"])
    except Exception as e:
        raise HTTPException(502, "AI inference failed: %s" % e)
    return {"profile": prof, "detected": det}


@app.post("/api/fileops/command")
def fileops_command_ep(body: dict = Body(...)):
    root, text = (body or {}).get("root"), (body or {}).get("text")
    if not root or not text:
        raise HTTPException(400, "root and text are required")
    did = _dev_id(body)
    scope = body.get("scope", "multi_system")
    det, vars_t, sys_t, sample_t = _fileops_ctx(did, root, scope, body.get("system"))
    profiles_text = "\n".join(
        "- %s (id=%s): %s -> %s" % (p["name"], p["id"], p["description"], p["target"])
        for p in fileops.profiles_list())
    try:
        intent = ai.file_command(text, profiles_text, sys_t, vars_t, det["current"])
    except Exception as e:
        raise HTTPException(502, "AI command failed: %s" % e)
    scope = intent.get("scope") or scope
    system = intent.get("system") or body.get("system")
    if intent.get("profile_id"):
        profile = fileops.profile_get(intent["profile_id"])
        if not profile:
            raise HTTPException(502, "AI referenced an unknown profile")
    else:
        profile = {"name": "AI plan", "description": intent.get("explanation", ""),
                   "target": intent.get("target", ""),
                   "m3u": bool(intent.get("m3u")), "rename": bool(intent.get("rename")),
                   "prune_empty": intent.get("prune_empty", True) is not False,
                   "archive_policy": "keep"}
    try:
        pl = fileops.plan(did, root, profile, scope, system)
    except Exception as e:
        raise HTTPException(502, "planning the AI request failed: %s" % e)
    return {"explanation": intent.get("explanation", ""), "profile": profile,
            "scope": scope, "system": system, "summary": pl["summary"],
            "warnings": pl["warnings"], "sample": pl["sample"]}


@app.post("/api/fileops/runbook")
def fileops_make_runbook(body: dict = Body(...)):
    root = (body or {}).get("root")
    if not root:
        raise HTTPException(400, "a root path is required")
    did = _dev_id(body)
    scope, system = body.get("scope", "multi_system"), body.get("system")
    operation = (body or {}).get("operation") or "restructure"
    try:
        if operation == "extract":
            dest = (body or {}).get("dest") or "downloaded_media"
            layout = (body or {}).get("layout") or "esde"
            xop = (body or {}).get("op") or "move"
            pl = fileops.plan_extract(did, root, dest, scope, system,
                                      layout=layout, op=xop)
            label = "%s media → %s/" % ("Copy" if xop == "copy" else "Extract", dest)
        else:
            profile = (body or {}).get("profile")
            if not profile:
                raise HTTPException(400, "a profile is required")
            pl = fileops.plan(did, root, profile, scope, system)
            label = profile
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, "planning failed: %s" % e)
    if not pl["ops"]:
        raise HTTPException(400, "nothing to do — files already match this layout")
    rid = fileops.create_runbook(did, root, label, pl["ops"], scope, system,
                                 body.get("note", ""))
    # remember what this run makes the folder conform to, so a successful apply can
    # (re)write its .ludodex manifest (see _write_manifest_for_run).
    _MANIFEST_CTX[rid] = {
        "op": operation, "scope": scope, "system": system,
        "profile": None if operation == "extract" else profile,
        "dest": dest if operation == "extract" else None,
    }
    return {"run_id": rid, "runbook": fileops.runbook(rid), "warnings": pl["warnings"]}


@app.get("/api/fileops/runbook/{run_id}")
def fileops_get_runbook(run_id: int):
    try:
        rb = fileops.runbook(run_id)
    except RuntimeError:
        raise HTTPException(404, "no such runbook")
    rec = _JOBS.get("run:%d" % run_id)
    rb["running"] = bool(rec and rec["thread"].is_alive())
    rb["job_error"] = rec["error"] if rec else None
    return rb


@app.post("/api/fileops/runbook/{run_id}/execute")
def fileops_execute_runbook(run_id: int):
    try:
        fileops.runbook(run_id)
    except RuntimeError:
        raise HTTPException(404, "no such runbook")
    _start_runbook_job(run_id)
    return {"started": True, "run_id": run_id}


@app.post("/api/fileops/runbook/{run_id}/undo")
def fileops_undo_runbook(run_id: int):
    try:
        fileops.runbook(run_id)
    except RuntimeError:
        raise HTTPException(404, "no such runbook")
    _start_job("undo:%d" % run_id, "fileops-undo", "Undo runbook #%d" % run_id,
               lambda stop: fileops.undo(run_id), run_id=run_id, cancelable=False)
    return {"started": True, "run_id": run_id}


@app.get("/api/fileops/runbook/{run_id}/troubleshoot")
def fileops_troubleshoot(run_id: int):
    try:
        return fileops.troubleshoot(run_id)
    except RuntimeError:
        raise HTTPException(404, "no such runbook")


@app.get("/api/fileops/history")
def fileops_history():
    return {"runs": fileops.history()}


@app.post("/api/fileops/runbook-ops")
def fileops_runbook_ops(body: dict = Body(...)):
    """Build a reversible runbook from raw ops (the Commander's same-device drops).
    All paths must be relative to `root` — create_runbook's guard enforces that."""
    root = (body or {}).get("root")
    ops = [o for o in ((body or {}).get("ops") or []) if o.get("op")]
    if not root:
        raise HTTPException(400, "a root path is required")
    if not ops:
        raise HTTPException(400, "no operations given")
    did = _dev_id(body)
    label = (body or {}).get("label") or "File operations"
    try:
        rid = fileops.create_runbook(did, root, label, ops, "commander", None,
                                     body.get("note", ""))
    except RuntimeError as e:
        raise HTTPException(400, str(e))          # unsafe/absolute path in a drop
    return {"run_id": rid, "runbook": fileops.runbook(rid)}


# --------------------------------------------------------------------------- #
#  Commander direct filesystem ops: cross-device transfer (backgrounded rsync),
#  plus immediate mkdir / delete.
# --------------------------------------------------------------------------- #
_XFER = {}                          # jid -> {label,step,prog,error,when}


@app.post("/api/fs/transfer")
def fs_transfer_ep(body: dict = Body(...)):
    b = body or {}
    src_dev, dst_dev = int(b.get("src_device") or 0), int(b.get("dst_device") or 0)
    src_dir, dst_dir = b.get("src_dir") or "", b.get("dst_dir") or ""
    items = [i for i in (b.get("items") or []) if i and "/" not in i]
    mode = b.get("mode") or "copy"
    if mode not in ("copy", "move"):
        raise HTTPException(400, "mode must be copy or move")
    if src_dev == dst_dev:
        raise HTTPException(400, "same-device operations use the runbook path")
    if not (src_dir and dst_dir and items):
        raise HTTPException(400, "src_dir, dst_dir and items are required")
    jid = "xfer:%s" % os.urandom(4).hex()
    label = "%s %d → %s" % (mode.title(), len(items), posixbase(dst_dir))
    job = {"label": label, "error": None, "when": time.time(),
           "step": "%s %d item(s) → %s" % (mode, len(items), dst_dir),
           "prog": {"done": 0, "total": len(items)}}
    _XFER[jid] = job
    _start_job(jid, "transfer", label,
               lambda stop: devices.transfer_run(job, src_dev, src_dir, items,
                                                  dst_dev, dst_dir, mode, stop),
               cancelable=True)
    return {"started": True, "jid": jid}


@app.post("/api/fs/mkdir")
def fs_mkdir_ep(body: dict = Body(...)):
    path = (body or {}).get("path")
    if not path:
        raise HTTPException(400, "a path is required")
    try:
        devices.fs_mkdir(_dev_id(body), path)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


@app.post("/api/fs/delete")
def fs_delete_ep(body: dict = Body(...)):
    paths = [p for p in ((body or {}).get("paths") or []) if p]
    if not paths:
        raise HTTPException(400, "paths are required")
    try:
        devices.fs_delete(_dev_id(body), paths)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "removed": len(paths)}


@app.post("/api/fs/stat")
def fs_stat_ep(body: dict = Body(...)):
    path = (body or {}).get("path")
    if not path:
        raise HTTPException(400, "a path is required")
    try:
        return devices.fs_stat(_dev_id(body), path)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.post("/api/fileops/manifest")
def fileops_manifest_write(body: dict = Body(...)):
    """Scan a folder now and (re)write its .ludodex manifest — a background job
    (one full walk). Used for the manual 'Index this folder' action."""
    root = (body or {}).get("root")
    if not root:
        raise HTTPException(400, "a root path is required")
    did = _dev_id(body)
    op = (body or {}).get("operation") or "restructure"
    profile, dest = (body or {}).get("profile"), (body or {}).get("dest")
    scope, system = (body or {}).get("scope", "multi_system"), (body or {}).get("system")
    jid = "manifest:%s" % os.urandom(3).hex()
    _start_job(jid, "manifest", "Index folder → .ludodex.json",
               lambda stop: fileops.manifest_write(did, root, profile=profile,
                   scope=scope, system=system, op=op, dest=dest,
                   instance=_instance_name()), cancelable=False)
    return {"started": True, "jid": jid}


@app.post("/api/fileops/manifest/delete")
def fileops_manifest_delete(body: dict = Body(...)):
    root = (body or {}).get("root")
    if not root:
        raise HTTPException(400, "a root path is required")
    try:
        return fileops.manifest_delete(_dev_id(body), root)
    except Exception as e:
        raise HTTPException(502, str(e))


# --------------------------------------------------------------------------- #
#  Unified job monitor: long-running work (library sync + file-op runbooks),
#  with pause / restart / delete. Live worker threads live in _JOBS; runbook
#  status is authoritative from the fileops DB.
# --------------------------------------------------------------------------- #
_JOBS = {}                         # jid -> {kind,label,cancel,thread,error,run_id,started}
_JOBS_LOCK = threading.Lock()


def _start_job(jid, kind, label, fn, run_id=None, cancelable=False):
    """Run fn(should_stop) on a daemon thread, tracked as job `jid`."""
    with _JOBS_LOCK:
        cur = _JOBS.get(jid)
        if cur and cur["thread"] and cur["thread"].is_alive():
            raise HTTPException(409, "that job is already running")
        cancel = threading.Event()
        rec = {"kind": kind, "label": label, "cancel": cancel, "thread": None,
               "error": None, "run_id": run_id, "cancelable": cancelable,
               "started": time.time()}

        def worker():
            try:
                fn(cancel.is_set)
            except Exception as e:      # noqa: BLE001 — surface to the monitor
                rec["error"] = str(e)[:300]
        t = threading.Thread(target=worker, daemon=True)
        rec["thread"] = t
        _JOBS[jid] = rec
        t.start()
    return jid


_MANIFEST_CTX = {}                 # run_id -> {op, scope, system, profile, dest}


def _instance_name():
    return config.get("instance_name") or "ludodex"


def _write_manifest_for_run(run_id):
    """After a successful Operations runbook, refresh the folder's .ludodex manifest
    so future previews are instant. Best-effort + gated by the setting."""
    if not config.get_bool("manifests_enabled", True):
        return
    ctx = _MANIFEST_CTX.get(run_id)
    if not ctx:
        return
    did, root = fileops.run_target(run_id)
    if not root:
        return
    try:
        fileops.manifest_write(did, root, profile=ctx.get("profile"),
                               scope=ctx.get("scope", "multi_system"),
                               system=ctx.get("system"), op=ctx.get("op", "restructure"),
                               dest=ctx.get("dest"), run_id=run_id,
                               instance=_instance_name())
    except Exception as e:                      # noqa: BLE001 — manifest is best-effort
        print("manifest write failed for run %d: %s" % (run_id, e), file=sys.stderr)


def _start_runbook_job(run_id):
    def job(stop):
        res = fileops.execute_runbook(run_id, should_stop=stop)
        if res.get("status") in ("done", "partial"):
            _write_manifest_for_run(run_id)
    return _start_job("run:%d" % run_id, "fileops-run",
                      "Runbook #%d" % run_id, job, run_id=run_id, cancelable=True)


def _jobs_list():
    """Normalize the live sync job + recent runbooks into one job feed."""
    out = []
    sj = _SYNC.get("job")
    if sj:
        prog = sj.get("prog") or {
            "done": sum(1 for s in sj.get("services", {}).values() if s["state"] == "ok"),
            "total": len(sj.get("services", {})) or 1}
        _run = sj.get("running")
        _pau = sj.get("paused")
        out.append({
            "id": "sync", "kind": "sync", "label": "Library sync",
            "status": ("paused" if _pau else "running" if _run else
                       "error" if sj.get("error") else "done"),
            "detail": sj.get("step", ""), "error": sj.get("error"),
            "progress": {"done": prog["done"], "total": prog["total"] or 1, "failed": 0},
            "when": None,
            "cancelable": bool(_run and not _pau),    # ⏸ pause
            "restartable": bool(_pau),                 # ▶ resume
            "deletable": True})                        # × stop (running) / dismiss
    rj = _ROMSYNC.get("job")
    if rj:
        devs = rj.get("devices", {})
        rprog = rj.get("prog") or {
            "done": sum(1 for d in devs.values() if d["state"] == "ok"),
            "total": len(devs) or 1}
        out.append({
            "id": "romsync", "kind": "romsync", "label": "ROM sync",
            "status": ("running" if rj.get("running") else
                       "error" if rj.get("error") else "done"),
            "detail": rj.get("step", ""), "error": rj.get("error"),
            "progress": {"done": rprog["done"], "total": rprog["total"] or 1,
                         "failed": sum(1 for d in devs.values() if d["state"] == "failed")},
            "when": None, "cancelable": False, "restartable": False,
            "deletable": not rj.get("running")})
    for jid, xj in list(_XFER.items()):
        rec = _JOBS.get(jid)
        live = bool(rec and rec["thread"] and rec["thread"].is_alive())
        prog = xj.get("prog") or {"done": 0, "total": 1}
        out.append({
            "id": jid, "kind": "transfer", "label": xj.get("label", "Transfer"),
            "status": ("running" if live else "error" if xj.get("error") else "done"),
            "detail": xj.get("step", ""), "error": xj.get("error"),
            "progress": {"done": prog.get("done", 0),
                         "total": prog.get("total", 1) or 1, "failed": 0},
            "when": xj.get("when"), "cancelable": live, "restartable": False,
            "deletable": not live})
    for r in fileops.history(limit=40):
        jid = "run:%d" % r["id"]
        rec = _JOBS.get(jid)
        live = bool(rec and rec["thread"] and rec["thread"].is_alive())
        status = "running" if live else r["status"]
        out.append({
            "id": jid, "kind": "fileops", "run_id": r["id"],
            "label": "%s — %s" % (r["profile"], posixbase(r["root"])),
            "status": status, "detail": r["note"] or "",
            "error": (rec or {}).get("error"),
            "progress": {"done": r["done"], "total": r["n_ops"],
                         "failed": r["failed"]},
            "when": r["finished"] or r["started"] or r["created"],
            "cancelable": live, "restartable": r["status"] in
            ("paused", "partial", "planned") or r["pending"] > 0,
            "deletable": not live})
    _proposed = aimeta.proposed_counts()
    _pgames = aimeta.proposed_run_games()   # run_id -> [{norm_key,title}] (for naming/linking)
    _shown_runs = set()
    for s in aimeta.scans_list(limit=20):
        _shown_runs.add(s["id"])
        jid = "aimeta:%d" % s["id"]
        rec = _JOBS.get(jid)
        try:                                # single-game scan → link the game name
            _skeys = json.loads(s.get("keys_json") or "[]")
        except Exception:
            _skeys = []
        _tkey = _skeys[0] if len(_skeys) == 1 else None
        live = bool(rec and rec["thread"] and rec["thread"].is_alive())
        _prop = _proposed.get(s["id"], 0)
        _sk, _er = s.get("skipped") or 0, s.get("errored") or 0
        if _prop:                                   # findings waiting on the user win
            _detail = "%d to review" % _prop
        elif live:
            _detail = "scanning %d/%d…" % (s["done"], s["total"])
        else:                                       # finished: say WHY, not just a tally —
            # "0 found" has two very different meanings (already matched & complete vs.
            # couldn't identify); spell them out so a no-change scan isn't read as a failure.
            _co, _um = s.get("complete") or 0, s.get("unmatched") or 0
            if s["total"] == 1:                     # single-game wand → the specific verdict
                if s["findings"]:
                    _detail = "1 change found"
                elif _co:
                    _detail = "already matched & complete — nothing to change"
                elif _um:
                    _detail = "couldn't identify this game"
                elif _sk:
                    _detail = "skipped — no data to analyze"
                elif _er:
                    _detail = "scan errored"
                else:
                    _detail = "nothing to change"
            else:
                _parts = ["scanned %d" % s["done"], "%d found" % s["findings"]]
                if _co:
                    _parts.append("%d already complete" % _co)
                if _um:
                    _parts.append("%d not identified" % _um)
                if _sk:
                    _parts.append("%d skipped" % _sk)
                if _er:
                    _parts.append("%d error%s" % (_er, "" if _er == 1 else "s"))
                _detail = " · ".join(_parts)
        out.append({
            "id": jid, "kind": "aimeta", "run_id": s["id"],
            "label": "Metadata scan — %s" % s["target"],
            # only ACTUALLY-live (thread alive in this process) is 'running'. A DB row still
            # 'running' with no live thread is a mid-session orphan (its worker died) — show
            # it 'interrupted' rather than a phantom spinner; startup reap fixes it durably.
            "status": ("running" if live
                       else "interrupted" if s["status"] == "running" else s["status"]),
            "detail": _detail,
            "error": (rec or {}).get("error"),
            "findings": _prop,
            "target_key": _tkey,                       # single-game scan → clickable name
            "progress": {"done": s["done"], "total": s["total"], "failed": 0},
            "when": s["finished"] or s["created"],
            "cancelable": live, "restartable": not live and s["done"] < s["total"],
            "deletable": not live})
    # Orphaned reviews: proposed findings whose scan_run was deleted / aged past the
    # listed window still need reviewing, but nothing above points to them — so they'd
    # stay invisible until some new job jogged the feed. Surface each as a reviewable
    # entry so the monitor's poll scoops them out on its own (DESIGN: no pending review
    # is ever stranded). run_id survives on the findings, so Review still opens them.
    for _rid, _n in _proposed.items():
        if _rid in _shown_runs or not _n:
            continue
        _games = _pgames.get(_rid, [])
        if len(_games) == 1:                # name + link the single stranded game
            _olabel = "Metadata scan — %s" % (_games[0]["title"] or _games[0]["norm_key"])
            _otarget = _games[0]["norm_key"]
        else:
            _olabel, _otarget = "Metadata scan — pending review", None
        out.append({
            "id": "aimeta:%d" % _rid, "kind": "aimeta", "run_id": _rid,
            "label": _olabel, "target_key": _otarget,
            "status": "done", "detail": "%d to review" % _n, "error": None,
            "findings": _n, "progress": {"done": 0, "total": 0, "failed": 0},
            "when": None, "cancelable": False, "restartable": False, "deletable": True})
    # generic one-shot jobs (apply, undo…) not represented above, while live/errored
    shown = {j["id"] for j in out}
    for jid, rec in list(_JOBS.items()):
        if jid in shown:
            continue
        live = bool(rec["thread"] and rec["thread"].is_alive())
        if not live and not rec.get("error"):
            continue
        out.append({
            "id": jid, "kind": rec["kind"], "label": rec["label"],
            "status": "running" if live else "error",
            "detail": "", "error": rec.get("error"),
            "progress": {"done": 0, "total": 0, "failed": 0},
            "when": rec.get("started"), "cancelable": False,
            "restartable": False, "deletable": not live})
    return out


def posixbase(p):
    return (p or "").rstrip("/").rsplit("/", 1)[-1] or (p or "")


@app.get("/api/jobs")
def jobs_list():
    return {"jobs": _jobs_list()}


@app.post("/api/jobs/{jid:path}/pause")
def jobs_pause(jid: str):
    if jid == "sync":                       # freeze the running sync phase (SIGSTOP)
        if not _sync_pause():
            raise HTTPException(400, "no running sync to pause")
        return {"paused": True, "id": jid}
    rec = _JOBS.get(jid)
    if not rec or not rec.get("cancelable"):
        raise HTTPException(400, "this job can't be paused")
    rec["cancel"].set()
    return {"paused": True, "id": jid}


@app.post("/api/jobs/{jid:path}/restart")
def jobs_restart(jid: str):
    if jid == "sync":                       # ▶ on a paused sync = resume (SIGCONT)
        if not _sync_resume():
            raise HTTPException(400, "sync is not paused")
        return {"restarted": True, "id": jid}
    if jid.startswith("run:"):
        _start_runbook_job(int(jid.split(":", 1)[1]))
        return {"restarted": True, "id": jid}
    if jid.startswith("aimeta:"):
        old = aimeta.scan_get(int(jid.split(":", 1)[1]))
        if not old:
            raise HTTPException(404, "no such scan")
        # resume exactly where it stopped, using the stored key set + options
        keys = (old.get("keys") or [])[old.get("done") or 0:]
        if not keys:
            raise HTTPException(400, "nothing left to scan")
        opts = {"web": bool(old.get("web")),
                "match_provider": bool(old.get("match_provider")),
                "metadata_kinds": old.get("md_kinds"), "label": old.get("target")}
        rid = aimeta.scan_new(old["target"], keys, opts["web"],
                              opts["match_provider"], opts["metadata_kinds"])
        _start_aimeta_job(rid, keys, opts)
        return {"restarted": True, "id": "aimeta:%d" % rid}
    raise HTTPException(400, "start a library sync from the Library page")


def _delete_one_job(jid):
    """Dismiss/stop a single job by id. Returns True if handled, False if unknown."""
    if jid == "sync":
        sj = _SYNC.get("job")
        if sj and sj.get("running"):        # × on a live sync = stop it (kill phase)
            _sync_stop()
        else:
            _SYNC["job"] = None             # dismiss a finished/stopped job
        return True
    if jid == "romsync":
        _ROMSYNC["job"] = None
        return True
    if jid.startswith("xfer:"):
        rec = _JOBS.get(jid)
        if rec and rec["thread"] and rec["thread"].is_alive():
            rec["cancel"].set()
        _XFER.pop(jid, None)
        _JOBS.pop(jid, None)
        return True
    if jid.startswith("run:"):
        rid = int(jid.split(":", 1)[1])
        rec = _JOBS.get(jid)
        if rec and rec["thread"] and rec["thread"].is_alive():
            rec["cancel"].set()
        fileops.run_delete(rid)
        _JOBS.pop(jid, None)
        return True
    if jid.startswith("aimeta:"):
        rid = int(jid.split(":", 1)[1])
        rec = _JOBS.get(jid)
        if rec and rec["thread"] and rec["thread"].is_alive():
            rec["cancel"].set()
        aimeta.scan_delete(rid)                 # keeps the findings, drops the run
        _JOBS.pop(jid, None)
        return True
    if jid in _JOBS:                            # generic one-shot job (apply, undo…)
        rec = _JOBS.get(jid)
        if not (rec and rec["thread"] and rec["thread"].is_alive()):
            _JOBS.pop(jid, None)                # only dismiss finished/errored ones
        return True
    return False


@app.delete("/api/jobs/{jid:path}")
def jobs_delete(jid: str):
    if not _delete_one_job(jid):
        raise HTTPException(400, "unknown job")
    return {"deleted": True}


@app.post("/api/jobs/clear")
def jobs_clear():
    """Dismiss every FINISHED job at once (done / interrupted / failed) — leaves running,
    paused, and still-to-review jobs alone. Scan findings are kept (only the run rows go)."""
    n = 0
    for j in _jobs_list():
        if (j.get("deletable") and j["status"] not in ("running", "paused")
                and not (j.get("findings") or 0)):     # keep anything awaiting review
            try:
                if _delete_one_job(j["id"]):
                    n += 1
            except Exception:
                pass
    return {"cleared": n}


# --------------------------------------------------------------------------- #
#  AI metadata audit & supplement: scan games → the `metadata` AI area audits
#  the provider match, identifies unmatched games, and fills attribute gaps.
#  Findings are proposals the user accepts/rejects; accepted supplements show
#  in the detail view and bake into the catalog on the next rebuild.
# --------------------------------------------------------------------------- #
# fields carrying what igdb_enrich._pick_era_aware needs: first_release_date (era gate)
# + alternative_names (JP/romanized title matching). _IGDB_FIELDS omits alt-names.
_IGDB_FIELDS_ERA = ("fields id,name,slug,first_release_date,alternative_names.name,"
                    "platforms.abbreviation,cover.image_id;")


def _igdb_raw_hits(title, limit=15):
    """RAW (un-normalized) IGDB hits for `title` — search + exact-name(`~`), deduped by id.
    Carries first_release_date + alternative_names so the shared era-aware selector can
    apply its console/era gate and alt-name matching. Empty on no creds/error."""
    cid, tok = _igdb_token()
    if not tok:
        return []
    import igdb
    safe = title.replace('"', "").replace("\\", "").replace("*", "")
    out, seen = [], set()
    for body in ('search "%s"; %s limit 8;' % (safe, _IGDB_FIELDS_ERA),
                 '%s where name ~ "%s"; sort first_release_date asc; limit %d;'
                 % (_IGDB_FIELDS_ERA, safe, limit)):
        try:
            for h in igdb.query("games", body, cid, tok):
                if h.get("id") and h["id"] not in seen:
                    seen.add(h["id"])
                    out.append(h)
        except Exception:
            pass
    return out


def _provider_match(title, year=None, consoles=None):
    """Search IGDB for an AI-proposed title → the real provider hit (or None).

    When `consoles` is given (the game's EMULATION platforms), use the SAME era-aware
    selection as the catalog sync — igdb_enrich._pick_era_aware: exact normalized-title
    (incl. alternative_names), reject any year era-impossible for those consoles, prefer
    the earliest plausible (original over remake). This is what stops the wand binding a
    modern same-title game to a retro ROM (Valve's Portal 2007 → the 1986 Amiga ROM); the
    wand now identifies exactly as build_library would, instead of via a second, weaker
    matcher. Falls back to the legacy title+year path when no console context is available.

    Either way it binds ONLY on an exact normalized-title match — never the arbitrary top
    relevance hit (IGDB ranks 'Gradius' below its sequels; a garbage link is worse than
    none)."""
    if not title:
        return None
    if consoles:
        raw = _igdb_raw_hits(title)
        if not raw:
            return None
        iid, _slug = igdb_enrich._pick_era_aware(
            raw, titlenorm.norm(title), set(consoles), require_unique=False)
        if not iid:
            return None                 # era gate / exact-title rejected everything
        hit = next((h for h in raw if h.get("id") == iid), None)
        return _pack_igdb(_igdb_hits([hit])[0]) if hit else None
    tn = titlenorm.norm(title)
    try:
        search_hits = _igdb_search(title, limit=8)
    except Exception:
        search_hits = []
    sx = [h for h in search_hits if titlenorm.norm(h.get("name") or "") == tn]
    # fast path: the fuzzy search already gave an exact-title entry at the AI's year.
    yr_hit = next((h for h in sx if year and h.get("year") == year), None)
    if yr_hit:
        return _pack_igdb(yr_hit)
    # else consult the exact-name index too — IGDB's relevance `search` routinely omits
    # the buried original ("Gradius" → only its sequels) or ranks a modern re-release
    # first ("Contra" → the 2006 remake), so merge both candidate sets before ranking.
    cands = {h["igdb_id"]: h for h in sx}
    for h in _igdb_by_name(title):
        if titlenorm.norm(h.get("name") or "") == tn:
            cands.setdefault(h["igdb_id"], h)
    if not cands:
        return None                 # no trustworthy IGDB entry — better none than wrong
    # prefer the AI's year (distinguishes remakes/eras sharing a title); else the
    # earliest exact-title entry (the original, not a later remake/re-release).
    best = sorted(cands.values(),
                  key=lambda h: (0 if (year and h.get("year") == year) else 1,
                                 h.get("year") or 9999))[0]
    return _pack_igdb(best)


def _pack_igdb(h):
    return {"provider": "igdb", "igdb_id": h.get("igdb_id"), "name": h.get("name"),
            "year": h.get("year"), "cover": h.get("cover"),
            "platforms": h.get("platforms")}


def _emulation_consoles(nk):
    """The EMULATION console labels for a game, for the era gate — emulation sources only,
    matching igdb_enrich._consoles_by_norm. console_eras is keyed by these raw platform
    labels; a store 'pc'/'xbox' row spans all generations and must never year-restrict."""
    con = ro(LIBRARY_DB)
    try:
        return {r[0] for r in con.execute(
            "SELECT DISTINCT s.platform FROM games g JOIN sources s ON s.game_id=g.id "
            "WHERE g.norm_key=? AND s.source='emulation' AND s.platform IS NOT NULL "
            "AND s.platform!=''", (nk,))}
    except Exception:
        return set()
    finally:
        con.close()


def _igdb_year_from_meta(mc, iid):
    """Release year of a cached IGDB record (from first_release_date), or None."""
    r = mc.execute("SELECT payload_json FROM igdb_meta WHERE igdb_id=?", (iid,)).fetchone()
    if not r or not r[0]:
        return None
    try:
        d = json.loads(r[0]).get("first_release_date")
        return time.gmtime(int(d)).tm_year if d else None
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def _era_compatible_emulation_entries(nk, year):
    """The ENTRY platforms of `nk`'s emulation entries whose console can plausibly be
    `year` (not HIGH-side impossible) — the entries a retro identity applies to. The store
    entry (pc/xbox) is excluded: it keeps its own appid identity. `games.platform` is the
    per-entry (norm_system'd) platform, the same label console_eras.impossible expects."""
    con = ro(LIBRARY_DB)
    try:
        plats = {r[0] for r in con.execute(
            "SELECT DISTINCT g.platform FROM games g JOIN sources s ON s.game_id=g.id "
            "WHERE g.norm_key=? AND s.source IN ('emulation','archive') "
            "AND g.platform IS NOT NULL AND g.platform!=''", (nk,))}
    except Exception:
        return set()
    finally:
        con.close()
    if year is None:
        return plats
    return {p for p in plats if not console_eras.impossible(p, year)}


def _blocked_release_entries(nk):
    """Entry platforms of `nk` classified Homebrew/Hack/Unlicensed — a different work that
    shares the name (the Atari 2600 homebrew "Doom"). The wand must not bind these to the
    commercial game; build_library forfeits them and this keeps the wand from fighting it."""
    con = ro(LIBRARY_DB)
    try:
        return {r[0] for r in con.execute(
            "SELECT DISTINCT g.platform FROM games g JOIN game_attributes a ON a.game_id=g.id "
            "WHERE g.norm_key=? AND a.kind='release_type' AND a.value IN "
            "('Homebrew','Hack','Unlicensed') AND g.platform IS NOT NULL", (nk,))}
    except Exception:
        return set()
    finally:
        con.close()


def _store_locked_igdb(nk, proposed_id):
    """True when norm_key already resolves via a Steam appid (an authoritative
    external_games link) to a DIFFERENT IGDB game — so the wand must NOT overwrite it
    with an AI name-match. This is what keeps a modern store game (Valve's Portal 2007,
    Steam appid 400 → IGDB 71) from being clobbered by the AI's retro identity for
    emulation ROMs that share the title (the 1986 text adventure, IGDB 14546). Mirrors
    igdb_enrich precedence: a steam_appid resolution wins and the name pass is skipped.
    Only the appid-owning store entry keeps the resolution; the retro ROMs sharing the
    norm_key stay era-separated (their own console art), never adopting the store id."""
    if not proposed_id:
        return False
    cache = os.path.join(DATA, "metadata-cache.sqlite")
    if not os.path.exists(cache):
        return False
    try:
        c = ro(cache)
        try:
            r = c.execute("SELECT igdb_id, matched_by FROM igdb_resolution "
                          "WHERE norm_key=?", (nk,)).fetchone()
        finally:
            c.close()
    except Exception:
        return False
    return bool(r and r["matched_by"] == "steam_appid"
                and r["igdb_id"] and r["igdb_id"] != proposed_id)


def _ss_match(queries, systems, year=None):
    """Search ScreenScraper by name (jeuRecherche) → the best candidate across the game's
    systems. SS is media-rich and covers the console/arcade long-tail. `queries` = title
    strings to try; `systems` = the game's platform label(s) — SS is per-system, so we try
    each (then a cross-system pass) and stop at the first match.

    Matches by QUERY-token coverage: a short title like 'Flashback' correctly matches SS's
    'Flashback : The Quest for Identity' (the query is fully contained in the SS name) — the
    old metric measured NAME coverage and wrongly rejected every subtitled game. Ties break
    toward a tighter name + a matching year. Returns a match dict or None."""
    import screenscraper as ss
    creds = config.screenscraper_creds()
    if not creds:
        return None
    if isinstance(systems, str):
        systems = [systems]
    sids = []
    for s in (systems or []):
        sid = ss.systeme_id(s)
        if sid and sid not in sids:
            sids.append(sid)
    sids.append(None)                            # cross-system fallback, last
    # query variants: raw, (region)/[tag]/ext-stripped, and subtitle-stripped (before a
    # ':' / ' - '), since SS name-search is picky about full subtitles. Dedup, keep order.
    raw = [q for q in (queries if isinstance(queries, (list, tuple)) else [queries]) if q]
    qlist, seenq = [], set()
    for q in raw:
        for cand in (q,
                     re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "",
                            re.sub(r"\.\w{2,4}$", "", q)).strip(),
                     re.split(r"\s*[:\-–]\s", q)[0].strip()):
            if cand and cand.lower() not in seenq:
                seenq.add(cand.lower())
                qlist.append(cand)
    best, seen = None, set()
    for sid in sids:
        for q in qlist:
            try:
                cands = ss.jeu_recherche(creds, q, systemeid=sid, limit=8)
            except Exception as e:               # surface, don't swallow silently
                print("ss search %r sys=%s: %s" % (q, sid, str(e)[:120]), file=sys.stderr)
                continue
            qtok = set(titlenorm.norm(q).split())
            for j in cands:
                jid = j.get("id")
                if jid in seen:
                    continue
                seen.add(jid)
                ntok = set(titlenorm.norm(ss.jeu_name(j)).split())
                if not (qtok and ntok):
                    continue
                inter = len(qtok & ntok)
                qc = inter / len(qtok)           # query tokens covered by the SS name
                nc = inter / len(ntok)           # SS-name tokens covered by the query
                yr = ss.jeu_year(j)
                score = qc + nc + (0.4 if year and yr == str(year) else 0)
                if qc >= 0.8 and (best is None or score > best[0]):
                    best = (score, j, ss.jeu_name(j), yr)
            if best and best[0] >= 1.7:          # near-exact (qc≈1 + nc≈1) — stop
                break
        if best:                                 # matched on this system — done
            break
    if not best:
        return None
    _, j, nm, yr = best
    return {"provider": "screenscraper", "ss_id": j.get("id"), "name": nm,
            "year": int(yr) if yr and str(yr).isdigit() else None,
            "system": systems[0] if systems else None}


def _aimeta_scan(run_id, norm_keys, opts, should_stop):
    """Background scan body: analyze each game; when match_provider is on, also try
    to resolve AI identities to a real IGDB entry. Stores actionable findings."""
    web = bool(opts.get("web"))
    match_prov = bool(opts.get("match_provider"))
    want_media = opts.get("want_media", True)  # the wand's media scope (on by default)
    md_kinds = opts.get("metadata_kinds")     # None=all attrs, []=none (media-only)
    model = ai.model_for_area("metadata")
    done = found = skipped = errored = complete = unmatched = 0
    media_nks = set()                             # identified games -> fill their art after
    lib = aimeta._lib()
    try:
        for nk in norm_keys:
            if should_stop():
                break
            try:
                ctx = aimeta.game_context(nk, lib=lib)
                if not ctx:                        # game vanished / no context to analyze
                    skipped += 1
                else:
                    if ctx.get("match"):           # already resolved -> the wand fills its art
                        media_nks.add(nk)
                    if md_kinds is not None:       # restrict which attrs AI fills
                        ctx["missing"] = [k for k in ctx.get("missing", [])
                                          if k in md_kinds]
                    res = ai.analyze_game(ctx, web=web)
                    m = res.get("match") or {}
                    # Re-verify identity on EVERY run regardless of current status: the wand
                    # is "make this game correct," so an already-matched game is re-resolved
                    # too (the match may be wrong/stale, or a same-title split now applies).
                    # Binds only on an exact normalized-title match, so re-confirming a
                    # correct game just re-proposes the same id (store_finding = no-op); it
                    # never regresses a good match to an arbitrary top hit.
                    if (match_prov and m.get("suggested_title")):
                        title, yr = m.get("suggested_title"), m.get("suggested_year")
                        sys0 = (ctx.get("systems") or [None])[0]
                        pms = [p for p in (
                            _provider_match(title, yr, consoles=_emulation_consoles(nk)),
                            _ss_match([title, ctx.get("title")], ctx.get("systems"), yr)) if p]
                        # A store-locked title (Valve's Portal vs the 1986 ROMs) is no
                        # longer suppressed here — apply routes the match PER ENTRY to the
                        # era-compatible ROMs and leaves the store entry alone.
                        if pms:
                            res["provider_matches"] = pms
                            res["provider_match"] = next(  # keep single for compat
                                (p for p in pms if p["provider"] == "igdb"), pms[0])
                    if aimeta.store_finding(run_id, ctx, res, model):
                        found += 1
                    elif ctx.get("match"):       # already matched, nothing new to add
                        complete += 1
                    else:                        # no match AND the AI couldn't identify it
                        unmatched += 1
            except Exception as e:               # one game's failure never aborts
                errored += 1
                print("aimeta scan: %s -> %s" % (nk, str(e)[:200]), file=sys.stderr)
            done += 1
            aimeta.scan_progress(run_id, done, found, skipped, errored, complete, unmatched)
    finally:
        lib.close()
    # The wand is ONE operation: after identifying, fill/refresh art for the games it
    # scanned that are already resolved — from every provider, plus open-web discovery
    # (Wikimedia/Google/LLM) when the user turned on web search. A newly-proposed match
    # fills its art on apply instead. Never lets a media hiccup fail the scan.
    try:
        if want_media:
            _wand_fill_media(media_nks, web, should_stop)
    except Exception as e:
        print("aimeta scan media fill: %s" % str(e)[:200], file=sys.stderr)
    aimeta.scan_progress(run_id, done, found, skipped, errored, complete, unmatched)
    aimeta.scan_finish(run_id, "paused" if done < len(norm_keys) else "done")


def _start_aimeta_job(run_id, keys, opts):
    web = bool(opts.get("web"))
    mp = bool(opts.get("match_provider"))
    label = "Metadata scan (%s%s%s)" % (opts.get("label", "scan"),
                                        ", web" if web else "",
                                        ", match" if mp else "")
    _start_job("aimeta:%d" % run_id, "aimeta", label,
               lambda stop: _aimeta_scan(run_id, keys, opts, stop),
               run_id=run_id, cancelable=True)


@app.post("/api/aimeta/scan")
def aimeta_scan(body: dict = Body(default={})):
    """Start a background metadata scan / magic-wand pass. Body:
    {target|norm_keys, limit, web, match_provider}. `norm_keys` (an explicit set,
    e.g. the current library filter) takes precedence over `target`
    ('unmatched'|'matched'|'missing'|'all')."""
    body = body or {}
    try:
        ai._resolve(ai.provider_for_area("metadata"), ai.model_for_area("metadata"))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    provider = ai.provider_for_area("metadata")
    web = bool(body.get("web")) and ai.supports_web(provider)
    # metadata: True=all attrs, False=none (media-only), or a list of attr kinds
    md = body.get("metadata", True)
    if md is False:
        md_kinds = []
    elif isinstance(md, list):
        md_kinds = [k for k in md if k in aimeta.SUPPLEMENT_KINDS]
    else:
        md_kinds = None
    # media: True=all, False=none, or a list of media kinds (applied at Apply time)
    want_media = body.get("media", True) is not False
    # media needs a provider match to source it, so force matching when media is on
    match_provider = bool(body.get("match_provider")) or want_media
    explicit = body.get("norm_keys")
    if explicit:
        keys = [k for k in explicit if isinstance(k, str)][:5000]
        label = body.get("label") or "selection"
    else:
        target = body.get("target", "unmatched")
        if target not in ("unmatched", "matched", "missing", "all"):
            raise HTTPException(400, "bad target")
        keys = aimeta.targets(target, max(1, min(int(body.get("limit") or 100), 2000)))
        label = target
    if not keys:
        raise HTTPException(400, "no games to scan")
    run_id = aimeta.scan_new(label, keys, web, match_provider, md_kinds)
    _start_aimeta_job(run_id, keys, {"web": web, "match_provider": match_provider,
                                     "metadata_kinds": md_kinds, "want_media": want_media,
                                     "label": label})
    return {"run_id": run_id, "target": label, "count": len(keys), "web": web,
            "match_provider": match_provider}


def _fetch_ref_text(url, max_bytes=500_000, max_chars=6000):
    """Fetch one user-provided reference URL → readable text (best-effort). HTML is
    stripped to text; empty string on any failure. Used to ground a wand re-run in the
    exact sources the user found, instead of the model's own blind web search."""
    import re as _re
    import html as _html
    import urllib.request
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (ludodex reference fetcher)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            ctype = resp.headers.get("Content-Type", "")
            data = resp.read(max_bytes)
        text = data.decode("utf-8", "replace")
        if "html" in ctype.lower() or "<html" in text[:2000].lower():
            text = _re.sub(r"(?is)<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>",
                           " ", text)
            text = _re.sub(r"(?is)<[^>]+>", " ", text)
            text = _html.unescape(text)
        return _re.sub(r"\s+", " ", text).strip()[:max_chars]
    except Exception as e:
        print("ref fetch %s: %s" % (url, str(e)[:120]), file=sys.stderr)
        return ""


def _fetch_refs(refs):
    """Normalize the request's `refs` (list or newline/comma string) → [{url,text}] for the
    valid, fetchable http(s) links (cap 5)."""
    import re as _re
    if isinstance(refs, str):
        refs = _re.split(r"[\s,]+", refs)
    out = []
    for u in (refs or [])[:8]:
        u = (u or "").strip()
        if not u.startswith(("http://", "https://")):
            continue
        t = _fetch_ref_text(u)
        if t:
            out.append({"url": u, "text": t})
        if len(out) >= 5:
            break
    return out


def _resolve_igdb_ref(raw):
    """A user-provided IGDB reference — a game URL (…/games/<id-or-slug>), a bare slug, or
    a numeric id — resolved to the numeric IGDB id (or None). The manual-pin escape hatch."""
    import re as _re
    raw = (raw or "").strip()
    m = _re.search(r"igdb\.com/games/([^/?#]+)", raw, _re.I)
    token = (m.group(1) if m else raw).strip().strip("/")
    if token.isdigit():
        return int(token)
    cid, tok = _igdb_token()
    if not tok or not token:
        return None
    import igdb as _ig
    safe = token.replace('"', "").replace("\\", "")
    try:
        for g in _ig.query("games", 'fields id; where slug="%s"; limit 1;' % safe, cid, tok):
            return g.get("id")
    except Exception:
        pass
    return None


def _map_igdb_attrs(iid):
    """Mapped IGDB attribute dict (genres/themes/dev/…) for one igdb id from the metadata
    cache, or {}. Shared by the surgical pin/apply paths so a corrected identity's metadata
    lands without a rebuild — mirrors build_library's igdb_map."""
    if not iid:
        return {}
    try:
        from igdb import map_record as _igdb_map
        mc = ro(os.path.join(DATA, "metadata-cache.sqlite"))
        try:
            r = mc.execute("SELECT payload_json FROM igdb_meta WHERE igdb_id=?",
                           (iid,)).fetchone()
        finally:
            mc.close()
        if r and r["payload_json"]:
            return _igdb_map(json.loads(r["payload_json"])) or {}
    except Exception:
        pass
    return {}


def _map_ss_attrs(nk):
    """Mapped ScreenScraper attribute dict for a game from the SS cache, or {}."""
    try:
        from screenscraper import extract_metadata as _ss_map
        sc = ro(os.path.join(DATA, "screenscraper-cache.sqlite"))
        try:
            r = sc.execute("SELECT payload_json FROM ss_game WHERE norm_key=? AND "
                           "status='ok' AND payload_json IS NOT NULL LIMIT 1",
                           (nk,)).fetchone()
        finally:
            sc.close()
        if r and r["payload_json"]:
            return _ss_map(json.loads(r["payload_json"])) or {}
    except Exception:
        pass
    return {}


def _fill_provider_attrs(con, gid, *mapped_origins):
    """Fill-only write of provider attributes for one entry: each (mapped, origin) fills
    only kinds no source already supplied (first provider to supply a kind wins). Mirrors
    build_library's provider accumulation so the surgical paths are metadata-complete."""
    have = {r[0] for r in con.execute(
        "SELECT DISTINCT kind FROM game_attributes WHERE game_id=?", (gid,))}
    rows = []
    for mapped, origin in mapped_origins:
        for kind, val in (mapped or {}).items():
            if kind in have:
                continue
            wrote = False
            for v in (val if isinstance(val, list) else [val]):
                if v not in (None, ""):
                    rows.append((gid, kind, str(v), origin))
                    wrote = True
            if wrote:
                have.add(kind)
    if rows:
        con.executemany("INSERT INTO game_attributes(game_id,kind,value,origin) "
                        "VALUES(?,?,?,?)", rows)


def _pin_live(nk, iid, plat, name, detach=False):
    """Write a manual identity decision straight into the LIVE catalog for the affected
    entries. Pin: game_key = igdb:<iid>, the IGDB link, the rename, AND the provider-record
    attributes — so a pinned game is fully complete instantly (no rebuild). Detach: the
    entry forfeits the title's game → game_key = title:<nk> and its IGDB link is removed
    (a homebrew/different game sharing the name). A per-entry decision (`plat`) touches only
    that platform's entry; a title pin touches every non-era-separated entry."""
    con = sqlite3.connect(LIBRARY_DB)
    try:
        con.execute("PRAGMA busy_timeout=8000")
        if not _has_col(con, "games", "game_key"):
            return
        igdb_attrs = {} if detach else _map_igdb_attrs(iid)
        ss_attrs = {} if detach else _map_ss_attrs(nk)
        for gid, gplat, bkey in con.execute(
                "SELECT id, platform, base_key FROM games WHERE norm_key=?", (nk,)).fetchall():
            if plat:
                if gplat != plat:
                    continue
            elif "\x1f" in (bkey or ""):
                continue                       # title pin leaves era-separated entries alone
            if detach:                         # "not this game" — own identity, drop the link
                con.execute("UPDATE games SET game_key=? WHERE id=?", ("title:%s" % nk, gid))
                con.execute("DELETE FROM metadata_links WHERE game_id=? AND provider='igdb'",
                            (gid,))
                continue
            con.execute("UPDATE games SET game_key=? WHERE id=?", ("igdb:%d" % iid, gid))
            con.execute("DELETE FROM metadata_links WHERE game_id=? AND provider='igdb'",
                        (gid,))
            con.execute("INSERT INTO metadata_links(game_id,provider,provider_id,slug,url) "
                        "VALUES(?,?,?,?,?)", (gid, "igdb", str(iid), None,
                        "https://www.igdb.com/games/%d" % iid))
            if name:
                con.execute("UPDATE games SET canonical_title=? WHERE id=? AND NOT EXISTS("
                            "SELECT 1 FROM sources WHERE game_id=? AND source NOT IN "
                            "('emulation','archive'))", (name, gid, gid))
            _fill_provider_attrs(con, gid, (igdb_attrs, "igdb"), (ss_attrs, "screenscraper"))
        con.commit()
    finally:
        con.close()


@app.post("/api/aimeta/refine")
def aimeta_refine(body: dict = Body(default={})):
    """Re-run the pipeline for ONE game with user-supplied context and (optionally) a
    bigger model — the review page's "not right? add context & re-run". Synchronous:
    supersedes the game's proposed finding and returns the fresh one so the reviewer
    sees the new result immediately. Body: {norm_key, hint, model?, web?, run_id?}."""
    body = body or {}
    nk = (body.get("norm_key") or "").strip()
    if not nk:
        raise HTTPException(400, "norm_key required")
    provider = ai.provider_for_area("metadata")
    try:
        model = (body.get("model") or "").strip() or ai.model_for_area("metadata")
        ai._resolve(provider, model)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    web = bool(body.get("web", True)) and ai.supports_web(provider)
    ctx = aimeta.game_context(nk)
    if not ctx:
        raise HTTPException(404, "no such game / no context to analyze")
    ctx["user_hint"] = (body.get("hint") or "").strip()
    refs = _fetch_refs(body.get("refs"))       # user-supplied web links → fetched grounding
    ctx["user_refs"] = refs
    try:
        # force_web: an explicit web-checked re-run always grounds (bypass the escalation
        # heuristic — the user asked for the web pass on purpose). User-provided reference
        # material is always injected as ground truth regardless of the web toggle.
        res = ai.analyze_game(ctx, provider=provider, model=model, web=web,
                              force_web=web)
    except Exception as e:                      # surface the failure to the reviewer
        raise HTTPException(502, "AI re-run failed: %s" % str(e)[:200])
    m = res.get("match") or {}
    if (m.get("suggested_title")
            and (m.get("status") in ("unmatched", "wrong", "unsure")
                 or not ctx.get("match"))):     # unlinked+identified still needs the link
        title, yr = m.get("suggested_title"), m.get("suggested_year")
        sys0 = (ctx.get("systems") or [None])[0]
        pms = [p for p in (_provider_match(title, yr, consoles=_emulation_consoles(nk)),
                           _ss_match([title, ctx.get("title")], ctx.get("systems"), yr)) if p]
        if pms:            # store-locked titles apply per-entry at apply time (see _aimeta_apply)
            res["provider_matches"] = pms
            res["provider_match"] = next(
                (p for p in pms if p["provider"] == "igdb"), pms[0])
    run_id = int(body.get("run_id") or 0) or _refine_run_id(nk)
    kind = aimeta.store_finding(run_id, ctx, res, model + " · refined")
    fresh = next((f for f in aimeta.findings_list("proposed", run_id=run_id)
                  if f["norm_key"] == nk), None)
    return {"kind": kind, "finding": fresh, "used_web": bool(res.get("web")),
            "used_refs": [r["url"] for r in refs],
            "model": model, "context": _finding_context(ctx)}


def _refine_run_id(nk):
    """The scan run to hang a refined finding on: the game's existing proposed finding's
    run, or a fresh lightweight run so it still groups in the monitor."""
    con = aimeta._con()
    row = con.execute("SELECT run_id FROM findings WHERE norm_key=? AND status='proposed'"
                      " ORDER BY created DESC LIMIT 1", (nk,)).fetchone()
    con.close()
    if row and row[0]:
        return row[0]
    rid = aimeta.scan_new("refine — %s" % nk, [nk], False, True, None)
    aimeta.scan_finish(rid, "done")
    return rid


@app.post("/api/aimeta/pin")
def aimeta_pin(body: dict = Body(default={})):
    """Manually pin an entry's identity to a specific IGDB game — the human override for
    odd-ball cases the AI can't get. Body: {norm_key, igdb, platform?}. `igdb` = an IGDB
    game URL (…/games/<id-or-slug>), a bare slug, or a numeric id. `platform` present → a
    PER-ENTRY override (just that platform's entry — e.g. pin the Amiga ROM while the store
    entry keeps its own game); absent → title-level (the whole game). Writes the resolution
    directly (matched_by='manual', so a later New sync won't silently re-resolve it), then
    runs the surgical reconcile so identity + cover land immediately; the authoritative
    background rebuild is enqueued after."""
    body = body or {}
    nk = (body.get("norm_key") or "").strip()
    plat = (body.get("platform") or "").strip() or None
    detach = bool(body.get("detach"))     # "not this game" — separate the entry, no id needed
    if not nk:
        raise HTTPException(400, "norm_key required")
    if detach:
        if not plat:
            raise HTTPException(400, "separating an entry requires a platform")
        iid = None
    else:
        iid = _resolve_igdb_ref(body.get("igdb"))
        if not iid:
            raise HTTPException(400, "could not resolve an IGDB game from %r — paste an "
                                "IGDB game link, slug, or numeric id" % (body.get("igdb") or ""))
    if plat:                         # resolve a raw system label to this game's actual entry
        _lc = ro(LIBRARY_DB)         # platform (games.platform is norm_system'd)
        try:
            _plats = [r[0] for r in _lc.execute(
                "SELECT DISTINCT platform FROM games WHERE norm_key=? AND platform IS NOT NULL",
                (nk,))]
        finally:
            _lc.close()
        if plat not in _plats:
            _np = media.norm_system(plat)
            plat = next((p for p in _plats if p == _np or p == plat), plat)
    now = int(time.time())
    name = None
    cache = os.path.join(DATA, "metadata-cache.sqlite")
    mc = sqlite3.connect(cache)
    try:
        mc.execute("CREATE TABLE IF NOT EXISTS igdb_resolution(norm_key TEXT PRIMARY KEY, "
                   "igdb_id INTEGER, slug TEXT, matched_by TEXT, resolved_at INTEGER)")
        mc.execute("CREATE TABLE IF NOT EXISTS igdb_meta(igdb_id INTEGER PRIMARY KEY, "
                   "payload_json TEXT, fetched_at INTEGER)")
        entry_res.ensure(mc)
        if detach:
            entry_res.set_detach(mc, nk, plat)     # this entry is NOT the title's game
        else:
            row = mc.execute("SELECT payload_json FROM igdb_meta WHERE igdb_id=?",
                             (iid,)).fetchone()
            if not row:                        # fetch the trusted record (name + for media)
                cid, tok = _igdb_token()
                if tok:
                    import igdb as _ig
                    q = "fields %s; where id=(%d); limit 1;" % (_ig.GAME_FIELDS, iid)
                    for g in _ig.query("games", q, cid, tok):
                        mc.execute("INSERT OR REPLACE INTO igdb_meta VALUES(?,?,?)",
                                   (g["id"], json.dumps(g, ensure_ascii=False), now))
                        row = (json.dumps(g),)
            if row and row[0]:
                try:
                    name = (json.loads(row[0]).get("name") or "").strip() or None
                except ValueError:
                    pass
            if plat:
                entry_res.set_entry(mc, nk, plat, iid, "manual")
            else:
                mc.execute("INSERT OR REPLACE INTO igdb_resolution(norm_key,igdb_id,slug,"
                           "matched_by,resolved_at) VALUES(?,?,?,?,?)",
                           (nk, iid, None, "manual", now))
        mc.commit()
    finally:
        mc.close()
    # Surgical, SCOPED reconcile — identity link/title/game_key + provider attrs land
    # synchronously, then media for just this game hydrates in the background. A single
    # manual pin no longer rebuilds the whole catalog (associations are read-time on
    # game_key, already correct). For a global re-derivation use the Rebuild button.
    try:
        _pin_live(nk, iid, plat, name, detach=detach)
        _reconcile_media_now({nk}, now)
    except Exception as e:
        print("aimeta pin: reconcile failed: %s" % str(e)[:200], file=sys.stderr)
    _enqueue_media_reconcile({nk}, True)
    return {"ok": True, "norm_key": nk, "platform": plat, "detached": detach,
            "igdb_id": iid, "title": name,
            "url": ("https://www.igdb.com/games/%d" % iid) if iid else None}


@app.post("/api/aimeta/refresh-media")
def aimeta_refresh_media(body: dict = Body(default={})):
    """Hunt media for ONE already-identified game on demand — the button for a game the
    wand leaves alone because its identity is already correct but it simply has no art.
    Pulls IGDB + SteamGridDB (+ optional AI open-web discovery when web:true), then
    re-chooses. Body: {norm_key|entry_key, web?}. Synchronous; returns what it landed."""
    body = body or {}
    nk = (body.get("norm_key") or "").strip()
    if not nk and body.get("entry_key"):
        nk = _split_entry_key(body["entry_key"])[0]
    if not nk:
        raise HTTPException(400, "norm_key or entry_key required")
    try:
        res = _fetch_media_for(nk, want_web=bool(body.get("web")))
    except Exception as e:
        raise HTTPException(502, "media fetch failed: %s" % str(e)[:200])
    return {"ok": True, "norm_key": nk, **res}


@app.post("/api/aimeta/pick-art")
def aimeta_pick_art(body: dict = Body(default={})):
    """On-demand AI art pick for ONE game — the wand's 'pick nicest art' button. This is
    the ONLY place the paid vision pick_art runs unless ai_art_auto_pick is enabled; the
    routine apply/rebuild path never calls it by default. Vision-picks the best image per
    kind (kinds with ≥2 candidate providers) and marks the game adjudicated. Synchronous.
    Body: {norm_key|entry_key}."""
    body = body or {}
    nk = (body.get("norm_key") or "").strip()
    if not nk and body.get("entry_key"):
        nk = _split_entry_key(body["entry_key"])[0]
    if not nk:
        raise HTTPException(400, "norm_key or entry_key required")
    if not ai.area_available("art"):
        raise HTTPException(400, "AI art picking isn't configured (set an AI provider "
                                 "for the 'art' area in Settings › AI).")
    title = nk
    lc = ro(LIBRARY_DB)
    try:
        r = lc.execute("SELECT canonical_title FROM games WHERE norm_key=? LIMIT 1",
                       (nk,)).fetchone()
        if r and r["canonical_title"]:
            title = r["canonical_title"]
    finally:
        lc.close()
    try:
        _ai_adjudicate_game(nk, title)
        _mark_art_adjudicated(nk, int(time.time()))
    except Exception as e:
        raise HTTPException(502, "pick-art failed: %s" % str(e)[:200])
    return {"ok": True, "norm_key": nk}


@app.get("/api/aimeta/targets")
def aimeta_targets():
    """Per-target game counts + whether the metadata provider can search the web."""
    out = {t: aimeta.target_count(t) for t in ("unmatched", "matched", "missing", "all")}
    out["web_capable"] = ai.supports_web(ai.provider_for_area("metadata"))
    out["provider"] = ai.provider_for_area("metadata")     # for the refine model picker
    out["model"] = ai.model_for_area("metadata")           # current default model
    out["escalation_model"] = ai.escalation_model_for_area("metadata")  # bigger, if set
    out["attributes"] = aimeta.SUPPLEMENT_KINDS       # metadata kinds the wand can fill
    out["media_kinds"] = list(media.SCALAR_KINDS)      # media kinds it can (re)choose
    return out


def _current_year(ctx):
    """The release year we ALREADY know for this game (existing attribute first, else the
    IGDB match year) — so the review page can always state the year, even when the scan
    isn't changing it. None if genuinely unknown."""
    have = ctx.get("have") or {}
    ry = have.get("release_year")
    if ry:
        v = ry[0] if isinstance(ry, list) else ry
        mm = re.search(r"(?:19|20)\d{2}", str(v))
        if mm:
            return int(mm.group())
    m = ctx.get("match") or {}
    if m and m.get("year"):
        try:
            return int(m["year"])
        except (TypeError, ValueError):
            pass
    return None


def _finding_context(ctx):
    """The factual, non-AI things we KNOW about a game — shown on the review page so a
    reviewer can sanity-check the AI against the actual ROM: platform(s), file name(s),
    parent folder, region/edition tags, current provider match, known release year."""
    f = ctx.get("files") or {}
    m = ctx.get("match") or {}
    return {"title": ctx.get("title"), "systems": ctx.get("systems") or [],
            "sources": ctx.get("sources") or [], "year": _current_year(ctx),
            "files": f.get("files") or [], "paths": f.get("paths") or [],
            "folders": f.get("folders") or [],
            "tags": f.get("tags") or [], "siblings": f.get("siblings") or [],
            "current_match": (m.get("title") if m else None)}


@app.get("/api/aimeta/findings")
def aimeta_findings(status: str = Query(None), kind: str = Query(None),
                    run_id: int = Query(None)):
    findings = aimeta.findings_list(status, kind, run_id=run_id)
    # attach live factual context per game so the review page ALWAYS shows filename /
    # platform / folder / tags — even for findings created before this existed. Bounded
    # so a huge changeset can't stall; the ROM index is cached after first use.
    if len(findings) <= 60:
        seen = {}
        for f in findings:
            nk = f.get("norm_key")
            if nk and nk not in seen:
                try:
                    ctx = aimeta.game_context(nk)
                    seen[nk] = _finding_context(ctx) if ctx else None
                except Exception:
                    seen[nk] = None
            f["context"] = seen.get(nk)
    return {"findings": findings, "counts": aimeta.findings_counts()}


@app.get("/api/aimeta/scans")
def aimeta_scans():
    return {"scans": aimeta.scans_list()}


@app.post("/api/aimeta/finding/{fid}/{action}")
def aimeta_finding_action(fid: int, action: str):
    if action not in ("accept", "reject", "reset"):
        raise HTTPException(400, "action must be accept|reject|reset")
    aimeta.set_status(fid, {"accept": "accepted", "reject": "rejected",
                            "reset": "proposed"}[action])
    return {"findings": aimeta.findings_list(), "counts": aimeta.findings_counts()}


@app.post("/api/aimeta/accept-all")
def aimeta_accept_all(body: dict = Body(default={})):
    """Bulk-accept every proposed finding (optionally at/above a confidence)."""
    minc = float((body or {}).get("min_confidence") or 0)
    n = 0
    for f in aimeta.findings_list(status="proposed", limit=5000):
        if f["confidence"] >= minc:
            aimeta.set_status(f["id"], "accepted")
            n += 1
    return {"accepted": n, "counts": aimeta.findings_counts()}


def _igdb_attrs_for(nk):
    """Raw {kind: value} an accepted IGDB match supplies for a game."""
    import igdb
    cache = os.path.join(DATA, "metadata-cache.sqlite")
    if not os.path.exists(cache):
        return {}
    c = sqlite3.connect(cache)
    try:
        row = c.execute("SELECT m.payload_json FROM igdb_resolution r JOIN igdb_meta m "
                        "ON m.igdb_id=r.igdb_id WHERE r.norm_key=?", (nk,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        c.close()
    if not row:
        return {}
    try:
        return {k: v for k, v in igdb.map_record(json.loads(row[0])).items()
                if v not in (None, "", [])}
    except Exception:
        return {}


def _ss_attrs_for(nk):
    """Raw {kind: value} an accepted ScreenScraper match supplies for a game."""
    import screenscraper as ss
    cache = os.path.join(DATA, "screenscraper-cache.sqlite")
    if not os.path.exists(cache):
        return {}
    c = sqlite3.connect(cache)
    try:
        row = c.execute("SELECT payload_json FROM ss_game WHERE norm_key=? "
                        "AND status='ok'", (nk,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        c.close()
    if not row or not row[0]:
        return {}
    try:
        return {k: v for k, v in ss.extract_metadata(json.loads(row[0])).items()
                if v not in (None, "", [])}
    except Exception:
        return {}


def _attr_norm(v):
    if isinstance(v, list):
        return tuple(sorted(str(x).strip().lower() for x in v))
    return str(v).strip().lower()


def _art_adjudicated(nk):
    """Has the AI already vision-picked art for this game? Marker lives in the media
    index (durable across catalog rebuilds), so a rebuild never re-triggers the paid
    vision call for a game already adjudicated."""
    try:
        c = ro(INDEX_DB)
        try:
            return c.execute("SELECT 1 FROM art_adjudicated WHERE norm_key=?",
                             (nk,)).fetchone() is not None
        finally:
            c.close()
    except Exception:
        return False


def _mark_art_adjudicated(nk, now):
    """Record that the AI art-pick has run for this game (see _art_adjudicated). Marked
    on ATTEMPT, not just success, so a persistent provider error can't loop the wand into
    re-calling — and billing — on every rebuild. The on-demand button re-runs regardless."""
    try:
        c = sqlite3.connect(INDEX_DB)
        try:
            c.execute("CREATE TABLE IF NOT EXISTS art_adjudicated("
                      "norm_key TEXT PRIMARY KEY, at INTEGER)")
            c.execute("INSERT OR REPLACE INTO art_adjudicated(norm_key,at) VALUES(?,?)",
                      (nk, now))
            c.commit()
        finally:
            c.close()
    except Exception:
        pass


def _ai_adjudicate_game(nk, title):
    """With BOTH providers linked + art fetched, let AI pick the best image per kind
    and the better provider per conflicting attribute. Best-effort; never raises."""
    if ai.area_available("art"):                       # media — vision pick per kind
        try:
            rc = ro(INDEX_DB)
            try:
                kinds = [r["kind"] for r in rc.execute(
                    "SELECT kind FROM media WHERE norm_key=? GROUP BY kind "
                    "HAVING COUNT(DISTINCT provider) >= 2", (nk,))]
                for kind in kinds:
                    cands = []
                    for r in rc.execute("SELECT id, ref_type, ref, ext, sha1 FROM media "
                                        "WHERE norm_key=? AND kind=? ORDER BY id",
                                        (nk, kind)).fetchall():
                        t = _thumb_bytes(r)
                        if t:
                            cands.append((r["id"], t))
                        if len(cands) >= 6:
                            break
                    if len(cands) < 2:
                        continue
                    res = ai.pick_art(title, kind, [c[1] for c in cands],
                                      provider=ai.provider_for_area("art"),
                                      model=ai.model_for_area("art"),
                                      language=config.get("media_language") or None)
                    best = cands[res["index"]][0]
                    w = sqlite3.connect(INDEX_DB)
                    try:
                        w.execute("UPDATE media SET chosen=0 WHERE norm_key=? AND kind=?",
                                  (nk, kind))
                        w.execute("UPDATE media SET chosen=1 WHERE id=?", (best,))
                        w.commit()
                    finally:
                        w.close()
            finally:
                rc.close()
        except Exception as e:
            print("adjudicate media %s: %s" % (nk, str(e)[:160]), file=sys.stderr)
    try:                                                # attributes — pick per conflict
        ig, sc = _igdb_attrs_for(nk), _ss_attrs_for(nk)
        conflicts = {k: {"igdb": ig[k], "screenscraper": sc[k]}
                     for k in set(ig) & set(sc)
                     if _attr_norm(ig[k]) != _attr_norm(sc[k])}
        if conflicts and ai.area_available("metadata"):
            winners = ai.adjudicate_attributes(title, conflicts) or {}
            for k, prov in winners.items():
                if k in conflicts and prov in ("igdb", "screenscraper"):
                    val = conflicts[k][prov]
                    sval = ", ".join(str(x) for x in val) if isinstance(val, list) else str(val)
                    if sval:
                        overrides.set_override(nk, k, sval, origin=prov)
    except Exception as e:
        print("adjudicate attrs %s: %s" % (nk, str(e)[:160]), file=sys.stderr)


def _aimeta_apply(should_stop, only_ids=None):
    """Make accepted findings real: write AI provider-matches into igdb_resolution
    (+ fetch their IGDB records), then rebuild the catalog so accepted supplements
    and new provider links + their trusted attributes flow in. Returns the set of
    touched norm_keys so the caller can hand ART hydration off to a SEPARATE job —
    the metadata (rename/attrs/links) is what the reviewer waits on; media fetch +
    choose (up to ~30 min) runs in the background so the apply completes at the
    rebuild. `only_ids` scopes which findings get marked applied — captured at the
    start so a coalesced drain never marks findings accepted mid-run but not processed
    here."""
    import igdb
    cache = os.path.join(DATA, "metadata-cache.sqlite")
    now = int(time.time())
    if only_ids is None:                       # capture the set this pass applies
        only_ids = aimeta.accepted_ids()
    pms = aimeta.accepted_provider_matches()
    # every game this apply touches — new matches PLUS accepted supplements + SS
    # matches. Media top-up + adjudication run for ALL of them, so re-wanding an
    # already-identified game also fills its missing art (not just new matches).
    touched = {pm["norm_key"] for pm in pms}
    try:
        touched |= set(aimeta.accepted_supplements().keys())
        touched |= {m["norm_key"] for m in aimeta.accepted_ss_matches()}
    except Exception:
        pass
    # accepted compilations -> durable collections store. Credit is computed at read
    # time (game_detail), so this takes effect immediately — no rebuild dependency.
    try:
        for c in aimeta.accepted_collections():
            compilations.set_collection(DATA, c["coll_key"], c["name"],
                                        c["members"], origin="ai")
    except Exception as e:
        print("aimeta apply: collections write: %s" % str(e)[:150], file=sys.stderr)
    mc = sqlite3.connect(cache)
    mc.execute("CREATE TABLE IF NOT EXISTS igdb_resolution(norm_key TEXT PRIMARY "
               "KEY, igdb_id INTEGER, slug TEXT, matched_by TEXT, resolved_at INTEGER)")
    mc.execute("CREATE TABLE IF NOT EXISTS igdb_meta(igdb_id INTEGER PRIMARY KEY, "
               "payload_json TEXT, fetched_at INTEGER)")
    entry_res.ensure(mc)
    # Fetch the trusted IGDB records FIRST — the per-entry apply below needs each match's
    # release year for its era check, and title-level writes want the meta cached too.
    need = [pm["igdb_id"] for pm in pms if not mc.execute(
        "SELECT 1 FROM igdb_meta WHERE igdb_id=?", (pm["igdb_id"],)).fetchone()]
    cid, tok = _igdb_token()
    if tok and need:
        for i in range(0, len(need), 200):
            batch = need[i:i + 200]
            body = ("fields %s; where id = (%s); limit 500;"
                    % (igdb.GAME_FIELDS, ",".join(str(x) for x in batch)))
            try:
                for g in igdb.query("games", body, cid, tok):
                    mc.execute("INSERT OR REPLACE INTO igdb_meta(igdb_id,"
                               "payload_json,fetched_at) VALUES(?,?,?)",
                               (g["id"], json.dumps(g, ensure_ascii=False), now))
                mc.commit()
            except Exception:
                pass
    # Write resolutions. A store-locked title (a confident appid identity of a DIFFERENT
    # game — Valve's Portal vs the 1986 ROMs) does NOT clobber the shared slot: the match
    # is applied PER ENTRY to the era-compatible emulation entries (entry_resolution),
    # leaving the store entry's title-level identity intact. Everything else writes
    # title-level as before.
    detached = entry_res.load_detached(mc)     # user "not this game" — the AI must not undo it
    for pm in pms:
        nk, iid = pm["norm_key"], pm["igdb_id"]
        if _store_locked_igdb(nk, iid):
            yr = _igdb_year_from_meta(mc, iid)
            _skip = detached | {(nk, p) for p in _blocked_release_entries(nk)}
            plats = [p for p in _era_compatible_emulation_entries(nk, yr)
                     if (nk, p) not in _skip]
            for plat in plats:
                entry_res.set_entry(mc, nk, plat, iid, "ai_entry")
            if plats:
                print("aimeta apply: %s -> igdb:%s per-entry on %s (store id kept)"
                      % (nk, iid, sorted(plats)), file=sys.stderr)
        else:
            mc.execute("INSERT OR REPLACE INTO igdb_resolution(norm_key,igdb_id,slug,"
                       "matched_by,resolved_at) VALUES(?,?,?,?,?)",
                       (nk, iid, None, "ai_name", now))
    mc.commit()
    mc.close()
    _apply_ss_matches(now)
    # Steam community tags (SteamSpy) for touched Steam games — fetched BEFORE the
    # rebuild so build_library merges them into game_tags. This is what backfills
    # tags for Steam games imported before SteamSpy was configured: re-wanding a
    # game pulls its tags now, regardless of whether the initial import did.
    try:
        import steam_tags as _st
        if touched and _st.config.metadata_enabled("steamspy"):
            lc = ro(LIBRARY_DB)
            try:
                ph = ",".join("?" * len(touched))
                pairs = [(str(r["source_id"]), r["norm_key"]) for r in lc.execute(
                    "SELECT g.norm_key, s.source_id FROM games g JOIN sources s "
                    "ON s.game_id=g.id WHERE s.source='steam' AND g.norm_key IN (%s)"
                    % ph, list(touched)).fetchall()
                    if r["source_id"] and str(r["source_id"]).isdigit()]
            finally:
                lc.close()
            if pairs:
                nt = _st.fetch_pairs(pairs)
                print("aimeta apply: steam tags fetched for %d games" % nt,
                      file=sys.stderr)
    except Exception as e:
        print("apply steam-tags: %s" % str(e)[:150], file=sys.stderr)
    # INSTANT APPLY: reflect the accepted changes into the LIVE catalog right now
    # (rename + provider links + accepted AI attributes) so the reviewer sees them in
    # seconds — instead of blocking on a ~10-min full rebuild. The authoritative
    # rebuild is handed to the background reconcile job (which re-derives the same
    # facts canonically + adds provider-record attrs/game_key), mirroring the eager-
    # preview pattern of _apply_ownership_live. All accepted changes are durable
    # (igdb_resolution / ss_game caches + supplements read at status IN accepted,
    # applied), so the deferred rebuild reproduces them even after mark_applied.
    try:
        _apply_surgical_meta(touched)
    except Exception as e:                 # never let the preview block the apply
        print("aimeta apply: surgical preview failed (rebuild will apply): %s"
              % str(e)[:200], file=sys.stderr)
    # INSTANT media: fetch + choose art for JUST these games now, so a corrected/split
    # cover shows when this apply job finishes — not after the ~30-min whole-catalog art
    # tail in the background reconcile (which still runs, authoritatively, after).
    try:
        _reconcile_media_now(touched, now)
    except Exception as e:
        print("aimeta apply: instant media failed (reconcile will apply): %s"
              % str(e)[:200], file=sys.stderr)
    aimeta.mark_applied(only_ids)  # accepted -> applied (only this pass's findings)
    # recompute the combined Ludodex score from the IGDB ratings the wand just
    # cached (reads the cache, no network), so a newly-matched game's score lands
    # in the library immediately instead of waiting on a manual scores_fetch run.
    ok_s, err_s = _run_script("scores_fetch.py", args=["igdb"], timeout=180)
    if not ok_s:
        print("apply scores: %s" % (err_s or "")[:150], file=sys.stderr)
    return touched


def _apply_surgical_meta(touched):
    """Eager preview: write accepted matches + supplements straight into the LIVE
    game-library.sqlite for the touched games — the title rename (IGDB name, ROM/
    archive-only entries, mirroring build_library's guard), provider links, and
    accepted AI attributes (fill-only, never overriding an existing kind). The
    background reconcile rebuild supersedes this with the canonical derivation (which
    also adds provider-record attributes + game_key), so this only has to be visually
    right, not exhaustive. Best-effort per game; a failure just waits for the rebuild."""
    if not touched:
        return
    pms = {pm["norm_key"]: pm for pm in aimeta.accepted_provider_matches()}
    ssm = {}
    for m in aimeta.accepted_ss_matches():
        ssm.setdefault(m["norm_key"], []).append(m)
    sup = aimeta.accepted_supplements()
    # IGDB names for the matched games come from the igdb_meta cache just fetched.
    # Also load the CURRENT resolution maps for the touched games so the eager preview
    # can set games.game_key surgically (DESIGN §11.9) — the identity key the media
    # serve-gate keys on, normally only written by build_library in the background rebuild.
    igdb_names = {}
    title_ids = {}                       # nk -> igdb_id (title-level resolution)
    entry_ids = {}                       # (nk, platform) -> igdb_id (per-entry override)
    igdb_maps = {}                       # nk -> mapped IGDB attrs (genres/themes/dev/…)
    entry_maps = {}                      # (nk, platform) -> mapped IGDB attrs (per-entry)
    ss_maps = {}                         # nk -> mapped ScreenScraper attrs
    if touched:
        mc = ro(os.path.join(DATA, "metadata-cache.sqlite"))
        try:
            for pm in pms.values():
                r = mc.execute("SELECT payload_json FROM igdb_meta WHERE igdb_id=?",
                               (pm["igdb_id"],)).fetchone()
                if r and r["payload_json"]:
                    try:
                        igdb_names[pm["norm_key"]] = (json.loads(r["payload_json"])
                                                      .get("name") or "").strip()
                    except ValueError:
                        pass
            qs = ",".join("?" * len(touched))
            tt = tuple(touched)
            try:
                for _nk, _iid in mc.execute(
                        "SELECT norm_key, igdb_id FROM igdb_resolution "
                        "WHERE igdb_id>0 AND norm_key IN (%s)" % qs, tt):
                    title_ids[_nk] = _iid
            except sqlite3.OperationalError:
                pass
            try:
                for _nk, _p, _iid in mc.execute(
                        "SELECT norm_key, platform, igdb_id FROM entry_resolution "
                        "WHERE igdb_id>0 AND norm_key IN (%s)" % qs, tt):
                    entry_ids[(_nk, _p)] = _iid
            except sqlite3.OperationalError:
                pass                     # no per-entry overrides table yet
            # Provider-record ATTRIBUTES (genres/themes/dev/pub/…) mapped straight from the
            # cached IGDB records — the one thing the eager preview used to leave for the full
            # rebuild. Writing them here (fill-only, below) makes touched games metadata-
            # complete instantly, so the ~10-min whole-catalog rebuild is no longer needed
            # per apply. Mirrors build_library's igdb_map accumulation.
            try:
                from igdb import map_record as _igdb_map
                all_iids = (set(title_ids.values()) | set(entry_ids.values())
                            | {pm["igdb_id"] for pm in pms.values()})
                payloads = {}
                if all_iids:
                    qi = ",".join("?" * len(all_iids))
                    for _iid, _pl in mc.execute(
                            "SELECT igdb_id, payload_json FROM igdb_meta "
                            "WHERE igdb_id IN (%s)" % qi, tuple(all_iids)):
                        try:
                            payloads[_iid] = _igdb_map(json.loads(_pl))
                        except Exception:
                            pass
                for _nk, _i in title_ids.items():
                    if _i in payloads:
                        igdb_maps[_nk] = payloads[_i]
                for pm in pms.values():
                    if pm["igdb_id"] in payloads:
                        igdb_maps.setdefault(pm["norm_key"], payloads[pm["igdb_id"]])
                for _k, _i in entry_ids.items():
                    if _i in payloads:
                        entry_maps[_k] = payloads[_i]
            except Exception as e:
                print("surgical igdb attrs: %s" % str(e)[:150], file=sys.stderr)
        finally:
            mc.close()
    # ScreenScraper record attrs for the touched games (title-level), mapped like
    # build_library so a freshly SS-matched ROM shows genres/players/etc. immediately.
    if touched:
        try:
            from screenscraper import extract_metadata as _ss_map
            sc = ro(os.path.join(DATA, "screenscraper-cache.sqlite"))
            try:
                qs2 = ",".join("?" * len(touched))
                for _nk, _pl in sc.execute(
                        "SELECT norm_key, payload_json FROM ss_game WHERE status='ok' "
                        "AND norm_key IN (%s)" % qs2, tuple(touched)):
                    if _pl and _nk not in ss_maps:
                        try:
                            ss_maps[_nk] = _ss_map(json.loads(_pl))
                        except Exception:
                            pass
            finally:
                sc.close()
        except Exception:
            pass

    def _gk(nk, platform, bkey):
        """Mirror build_library._game_key: per-entry override wins, else an era-collision
        (\x1f in base_key) or unresolved entry falls to the title bucket, else the title's
        igdb id. Suffix-free title:<nk> matches media_fetch.game_key at serve time."""
        eid = entry_ids.get((nk, platform))
        if eid:
            return "igdb:%d" % eid
        if "\x1f" in (bkey or "") or nk not in title_ids:
            return "title:%s" % nk
        return "igdb:%d" % title_ids[nk]
    con = sqlite3.connect(LIBRARY_DB)
    try:
        con.execute("PRAGMA busy_timeout=8000")
        if not _has_col(con, "games", "game_key"):
            return                         # pre-migration schema; the rebuild applies
        for nk in touched:
            entries = con.execute(
                "SELECT id, platform, base_key FROM games WHERE norm_key=?",
                (nk,)).fetchall()
            if not entries:
                continue
            # Surgical game_key: set each entry's identity now (build_library does this
            # canonically in the background rebuild). This is what flips the media
            # serve-gate so a corrected/split cover appears without the ~10-min rebuild.
            for gid, plat, bkey in entries:
                con.execute("UPDATE games SET game_key=? WHERE id=?",
                            (_gk(nk, plat, bkey), gid))
            gids = [e[0] for e in entries]
            plat_of = {e[0]: e[1] for e in entries}
            pm = pms.get(nk)
            name = igdb_names.get(nk)
            # A store-locked match applies its IGDB identity only to ROM/archive entries;
            # the store entry keeps its appid identity (the rebuild does this canonically
            # via entry_resolution — mirror it here so the eager preview doesn't relink it).
            store_locked = bool(pm and _store_locked_igdb(nk, pm["igdb_id"]))
            for gid in gids:
                is_store_entry = bool(con.execute(
                    "SELECT 1 FROM sources WHERE game_id=? AND source NOT IN "
                    "('emulation','archive') LIMIT 1", (gid,)).fetchone())
                apply_igdb = bool(pm) and not (store_locked and is_store_entry)
                # rename to the matched title — only ROM/archive-only entries (store
                # titles are already clean; build_library guards the same way)
                if name and apply_igdb:
                    con.execute(
                        "UPDATE games SET canonical_title=? WHERE id=? AND NOT EXISTS("
                        "SELECT 1 FROM sources WHERE game_id=? AND source NOT IN "
                        "('emulation','archive'))", (name, gid, gid))
                # provider links (replace this provider's link for the entry)
                if apply_igdb:
                    con.execute("DELETE FROM metadata_links WHERE game_id=? AND "
                                "provider='igdb'", (gid,))
                    con.execute("INSERT INTO metadata_links(game_id,provider,"
                                "provider_id,slug,url) VALUES(?,?,?,?,?)",
                                (gid, "igdb", str(pm["igdb_id"]), None,
                                 "https://www.igdb.com/games/%s" % pm["igdb_id"]))
                for m in ssm.get(nk, []):
                    con.execute("DELETE FROM metadata_links WHERE game_id=? AND "
                                "provider='screenscraper'", (gid,))
                    con.execute("INSERT INTO metadata_links(game_id,provider,"
                                "provider_id,slug,url) VALUES(?,?,?,?,?)",
                                (gid, "screenscraper", str(m["ss_id"]), None,
                                 "https://www.screenscraper.fr/gameinfos.php?gameid=%s"
                                 % m["ss_id"]))
                # Metadata completeness (FILL-ONLY): provider records first (IGDB, then
                # ScreenScraper), then accepted AI supplements — each only filling kinds no
                # source already supplied. This is what makes the touched games genre/theme/
                # dev-complete NOW so no full rebuild is needed. IGDB attrs apply only where
                # the IGDB identity applies (not a store-locked store entry).
                have = {r[0] for r in con.execute(
                    "SELECT DISTINCT kind FROM game_attributes WHERE game_id=?", (gid,))}
                rows = []

                def _fill(mapped, origin):
                    for kind, val in (mapped or {}).items():
                        if kind in have:
                            continue
                        wrote = False
                        for v in (val if isinstance(val, list) else [val]):
                            if v not in (None, ""):
                                rows.append((gid, kind, str(v), origin))
                                wrote = True
                        if wrote:
                            have.add(kind)      # first provider to supply a kind wins
                if apply_igdb:
                    _fill(entry_maps.get((nk, plat_of.get(gid))) or igdb_maps.get(nk), "igdb")
                _fill(ss_maps.get(nk), "screenscraper")
                _fill(sup.get(nk), "ai")
                if rows:
                    con.executemany("INSERT INTO game_attributes(game_id,kind,value,"
                                    "origin) VALUES(?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()


def _reconcile_media_now(touched, now):
    """Surgical, synchronous media reconcile for the games an apply just touched: fetch
    their provider art (IGDB — incl. per-entry same-title override ids) and re-choose the
    best per kind, so the corrected identity's cover is available immediately. Whole-catalog
    fetch/choose (all providers, SteamGridDB gap-fill, materialize) still runs in the
    background reconcile; this just makes the touched games right in seconds, not ~30 min."""
    if not touched:
        return
    import media_fetch as _mf
    con = media_choose.con_index()         # Row factory — media_choose.select needs it
    try:
        con.execute("PRAGMA busy_timeout=30000")
        _mf.fetch_igdb(con, now, only=set(touched))   # incl. entry-override art
        _mf._backfill_game_key(con)                   # stamp any legacy/unstamped rows
        media_choose.select(con)                      # re-choose best per (nk,system,gkey,kind)
        con.commit()
    finally:
        con.close()


def _fetch_media_web(con, nk, title, now):
    """Add validated OPEN-WEB images for a game (provider='web', ranked below every real
    provider so it's a last resort): Wikimedia lead image (reliable, keyless) → Google image
    search with an AI vision PICK of the right result (needs a configured key) → LLM-proposed
    urls (low-yield). Every url is fetched and must be a live image of plausible size before
    it's trusted. Private-use catalog art (self-hosted, single user). Returns count added."""
    import media_fetch as _mf
    import media_web
    import urllib.request
    ctx = aimeta.game_context(nk) or {}
    systems, year = ctx.get("systems"), ctx.get("year")
    gkey = None
    lc = ro(LIBRARY_DB)
    try:
        r = lc.execute("SELECT game_key FROM games WHERE norm_key=? LIMIT 1", (nk,)).fetchone()
        gkey = r[0] if r else None
    finally:
        lc.close()

    def _fetch_img(url):                       # -> (mime, bytes) if a live image, else None
        if not (url or "").lower().startswith(("http://", "https://")):
            return None
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (ludodex media finder)"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                blob = resp.read(6_000_000)
            if "image" in ctype and len(blob) >= 2000:
                return ctype, blob
        except Exception:
            pass
        return None

    def _add(url, kind, ctype):
        ext = "png" if "png" in ctype else ("webp" if "webp" in ctype else "jpg")
        _mf.put(con, nk, kind, "web", url, now, ext=ext, gkey=gkey)

    added, seen = 0, set()
    # 1) Wikimedia lead image — reliable, keyless
    for c in media_web.wikimedia(title, year):
        if c["url"] in seen:
            continue
        img = _fetch_img(c["url"])
        if img:
            _add(c["url"], "cover", img[0]); seen.add(c["url"]); added += 1
    # 2) Google image search + AI vision pick (needs a configured key + cx)
    gcse_key, cx = config.get("google_cse_key"), config.get("google_cse_cx")
    if gcse_key and cx:
        sys0 = (systems or [""])[0] if systems else ""
        query = " ".join(x for x in (title, sys0, "cover art") if x)
        imgs, urls = [], []
        for c in media_web.google_images(query, gcse_key, cx, n=6):
            if c["url"] in seen:
                continue
            img = _fetch_img(c["url"])
            if img:
                imgs.append(img); urls.append(c["url"])
        if imgs:
            pick = 0
            if len(imgs) > 1:
                try:
                    pick = int(ai.pick_art(title, "cover", imgs).get("index", 0))
                except Exception:
                    pick = 0
            _add(urls[pick], "cover", imgs[pick][0]); seen.add(urls[pick]); added += 1
    # 3) LLM-proposed direct urls (low-yield last resort)
    for c in ai.find_media_urls(title, systems=systems, year=year):
        u = (c.get("url") or "").strip()
        if u in seen:
            continue
        img = _fetch_img(u)
        if img:
            _add(u, c["kind"], img[0]); seen.add(u); added += 1
    return added


def _pull_ss_media(con, nk, systems, queries, now):
    """Match + scrape ScreenScraper for a game and add its media. SS is media-rich for the
    retro/console long-tail (box art, screenshots, wheels, marquees) that IGDB/SGDB often
    lack — exactly what a TurboGrafx/arcade game needs. Caches the jeuInfos in ss_game (so
    build_library also unions SS metadata) + extracts the media into the index, tagged with
    the game's console so it serves as own-platform art. Returns media rows added."""
    creds = config.screenscraper_creds()
    if not creds or not config.get_bool("screenscraper_media", True):
        return 0
    m = _ss_match(queries, systems, None)
    if not m:
        return 0
    import screenscraper as ss
    import media_fetch as _mf
    try:
        jeu, _ = ss.jeu_infos(creds, gameid=m["ss_id"])
    except Exception as e:
        print("ss jeu_infos %s: %s" % (nk, str(e)[:120]), file=sys.stderr)
        return 0
    if not jeu:
        return 0
    system = m.get("system")
    try:                                       # cache for build_library's metadata union
        sc = sqlite3.connect(os.path.join(DATA, "screenscraper-cache.sqlite"))
        sc.execute("CREATE TABLE IF NOT EXISTS ss_game(norm_key TEXT, system TEXT, "
                   "ss_id INTEGER, status TEXT, payload_json TEXT, fetched_at INTEGER, "
                   "PRIMARY KEY(norm_key, system))")
        sc.execute("INSERT OR REPLACE INTO ss_game(norm_key,system,ss_id,status,"
                   "payload_json,fetched_at) VALUES(?,?,?,?,?,?)",
                   (nk, system, m["ss_id"], "ok", json.dumps(jeu, ensure_ascii=False), now))
        sc.commit()
        sc.close()
    except Exception as e:
        print("ss cache %s: %s" % (nk, str(e)[:100]), file=sys.stderr)
    n = 0
    for md in ss.extract_media(jeu):
        _mf.put(con, nk, md["kind"], "screenscraper", md["url"], now,
                ext=(md.get("format") or "jpg"), system=system,
                attrs={"type": md.get("type"), "region": md.get("region"),
                       "format": md.get("format")})
        n += 1
    return n


def _pull_media_sources(con, nk, want_web=False):
    """Fetch (do NOT choose) media for ONE game from every configured provider — IGDB
    (incl. per-entry override ids), SteamGridDB (a huge community art DB), and ScreenScraper
    (media-rich for the retro/console long-tail) — plus AI open-web discovery (Wikimedia/
    Google/LLM) when want_web. The caller runs media_choose.select once after a batch."""
    import media_fetch as _mf
    now = int(time.time())
    title, appid, plats = "", None, []
    lc = ro(LIBRARY_DB)
    try:
        r = lc.execute("SELECT canonical_title FROM games WHERE norm_key=? LIMIT 1",
                       (nk,)).fetchone()
        title = (r[0] if r else "") or ""
        ar = lc.execute("SELECT s.source_id FROM games g JOIN sources s ON s.game_id=g.id "
                        "WHERE g.norm_key=? AND s.source='steam' LIMIT 1", (nk,)).fetchone()
        appid = ar[0] if ar and str(ar[0] or "").isdigit() else None
        plats = [x[0] for x in lc.execute(
            "SELECT DISTINCT g.platform FROM games g JOIN sources s ON s.game_id=g.id "
            "WHERE g.norm_key=? AND s.source IN ('emulation','archive') "
            "AND g.platform IS NOT NULL AND g.platform!=''", (nk,))]
    finally:
        lc.close()
    _mf.fetch_igdb(con, now, only={nk})
    if plats:                                  # ScreenScraper media (retro/console-rich)
        try:
            _pull_ss_media(con, nk, plats, [title], now)
        except Exception as e:
            print("media ss %s: %s" % (nk, str(e)[:120]), file=sys.stderr)
    if _mf.config.steamgriddb_key():
        try:
            _mf.fetch_steamgriddb_targets(con, now, [(nk, title, appid)])
        except Exception as e:
            print("media sgdb %s: %s" % (nk, str(e)[:120]), file=sys.stderr)
    web_n = 0
    if want_web:
        try:
            web_n = _fetch_media_web(con, nk, title, now)
        except Exception as e:
            print("media web %s: %s" % (nk, str(e)[:120]), file=sys.stderr)
    return web_n


def _fetch_media_for(nk, want_web=False):
    """One-game media hunt + choose (the wand's media step, also the refresh endpoint)."""
    import media_fetch as _mf
    con = media_choose.con_index()
    web_n = 0
    try:
        con.execute("PRAGMA busy_timeout=30000")
        web_n = _pull_media_sources(con, nk, want_web=want_web)
        _mf._backfill_game_key(con)
        media_choose.select(con)
        con.commit()
        have = {r[0]: r[1] for r in con.execute(
            "SELECT kind, COUNT(*) FROM media WHERE norm_key=? AND chosen=1 GROUP BY kind",
            (nk,))}
    finally:
        con.close()
    return {"has_cover": bool(have.get("cover")), "chosen": have, "web_added": web_n}


def _wand_fill_media(nks, want_web, should_stop):
    """The wand's media step: fetch + choose art for the games it scanned, from every
    provider (+ open-web discovery when the user turned on web search). Runs for games that
    already have a resolution (a newly-proposed match fills its art on apply instead). One
    choose after the batch. Web discovery is capped so a huge 'scan all' doesn't do
    thousands of slow web calls — providers still run for every game."""
    nks = list(nks)
    if not nks:
        return
    import media_fetch as _mf
    con = media_choose.con_index()
    try:
        con.execute("PRAGMA busy_timeout=30000")
        # ENRICH vs FILL: a single-game (or handful) wand run pulls art from EVERY provider
        # for every scanned game — so a game with only a thin IGDB cover still gets its SGDB
        # heroes/logos and ScreenScraper box art. A big "scan all" only fills games MISSING a
        # cover (don't re-hit providers for thousands of games that already have art).
        ENRICH_CAP = 8
        if len(nks) <= ENRICH_CAP:
            need = nks
        else:
            need = [nk for nk in nks if not con.execute(
                "SELECT 1 FROM media WHERE norm_key=? AND chosen=1 AND kind='cover' LIMIT 1",
                (nk,)).fetchone()]
        if not need:
            return
        WEB_CAP = 40
        do_web = want_web and len(need) <= WEB_CAP
        if want_web and not do_web:
            print("aimeta wand: web media discovery skipped (%d games > cap %d); providers "
                  "only" % (len(need), WEB_CAP), file=sys.stderr)
        for nk in need:
            if should_stop():
                break
            try:
                _pull_media_sources(con, nk, want_web=do_web)
            except Exception as e:
                print("aimeta wand media %s: %s" % (nk, str(e)[:120]), file=sys.stderr)
        _mf._backfill_game_key(con)
        media_choose.select(con)
        con.commit()
    finally:
        con.close()


def _aimeta_apply_media(touched, media, should_stop):
    """Background art hydration for games an apply just touched: fetch provider media,
    gap-fill via SteamGridDB, prune blanks, choose the best per kind, then AI-adjudicate
    images/attribute conflicts. Split out of _aimeta_apply so the apply job completes at
    the catalog rebuild and this (the ~30-min tail) runs as its own coalesced job.
    `media` is True (all art), False (skip — never reaches here), or a list of kinds."""
    now = int(time.time())
    if media is not False:                 # media: False skips art entirely
        # pull provider media for newly-linked games (IGDB/Steam/ScreenScraper),
        # then pick the best per kind — restricted to `media` kinds if a list given
        _run_script("media_fetch.py", timeout=1800)
        # SteamGridDB gap-fill (hero/logo/cover/icon) for the games we just
        # identified — by title (or Steam appid). This is where SGDB's heroes/logos
        # come from for non-Steam games; its candidates then join the wand's art
        # adjudication below so the AI balances SGDB vs IGDB vs ScreenScraper.
        try:
            import media_fetch as _mf
            if touched and _mf.config.steamgriddb_key():
                lc = ro(LIBRARY_DB)
                try:
                    titles = {r["norm_key"]: r["canonical_title"] for r in lc.execute(
                        "SELECT norm_key, canonical_title FROM games")}
                    appids = {r["norm_key"]: r["source_id"] for r in lc.execute(
                        "SELECT g.norm_key, s.source_id FROM games g JOIN sources s "
                        "ON s.game_id=g.id WHERE s.source='steam'")}
                finally:
                    lc.close()
                tgts = [(nk, titles.get(nk, ""), appids.get(nk)) for nk in touched]
                mcon = sqlite3.connect(INDEX_DB)
                try:
                    _mf.fetch_steamgriddb_targets(mcon, now, tgts)
                finally:
                    mcon.close()
        except Exception as e:
            print("apply sgdb: %s" % str(e)[:150], file=sys.stderr)
        # drop blank/placeholder candidates BEFORE choosing so a real image wins
        try:
            n = _prune_blank_media(list(touched))
            if n:
                print("apply: pruned %d blank/degenerate images" % n, file=sys.stderr)
        except Exception as e:
            print("apply prune-blank: %s" % str(e)[:150], file=sys.stderr)
        args = ["--kinds", ",".join(media)] if isinstance(media, list) and media else []
        _run_script("media_choose.py", args=args, timeout=900)
    # AI adjudication (per game): now that providers are linked + art fetched, the model
    # can vision-pick the best image per kind and choose the better provider per
    # conflicting attribute. This is a PAID call per game, so it is OFF by default
    # (ai_art_auto_pick) — deterministic media_choose above already picks sensible art.
    # When enabled it runs ONCE per game (marker in the durable media index), so a catalog
    # rebuild never re-bills already-adjudicated games. The per-game 'AI: pick nicest art'
    # button (aimeta/pick-art) runs it on demand regardless of the flag.
    if touched and config.get_bool("ai_art_auto_pick", False):
        lcon = ro(LIBRARY_DB)
        try:
            titles = {r["norm_key"]: r["canonical_title"]
                      for r in lcon.execute("SELECT norm_key, canonical_title FROM games")}
        finally:
            lcon.close()
        for nk in touched:
            if should_stop():
                break
            if _art_adjudicated(nk):       # once per game — never re-bill on rebuild
                continue
            _ai_adjudicate_game(nk, titles.get(nk, nk))
            _mark_art_adjudicated(nk, now)


# --- background art job: coalesced queue drained on its own thread so metadata apply
# never waits on media. _ART_RUNNING (under _ART_LOCK) is the single-flight gate; a new
# apply that lands while art runs just adds its touched keys and the drain re-checks. ---
_ART_LOCK = threading.Lock()
_ART_QUEUE = set()
_ART_MEDIA = [True]                         # latest media spec (True | [kinds])
_ART_RUNNING = [False]


def _enqueue_reconcile(touched, media):
    """Queue touched games for the background reconcile (rebuild + scores + art); start
    the drain if idle. Always schedules when there's touched work — the rebuild must run
    to make the surgical preview canonical — even if `media` is False (art then skipped)."""
    if not touched:
        return
    start = False
    with _ART_LOCK:
        _ART_QUEUE.update(touched)
        _ART_MEDIA[0] = media
        if not _ART_RUNNING[0]:
            _ART_RUNNING[0] = True
            start = True
            cancel = threading.Event()
            rec = {"kind": "aimeta-art", "label": "Rebuild + art (reconcile)",
                   "cancel": cancel, "thread": None, "error": None, "run_id": None,
                   "cancelable": False, "started": time.time()}

            def worker():
                try:
                    _reconcile_drain(cancel.is_set)
                except Exception as e:      # noqa: BLE001 — surface to the monitor
                    rec["error"] = str(e)[:300]
            t = threading.Thread(target=worker, daemon=True)
            rec["thread"] = t
            with _JOBS_LOCK:
                _JOBS["aimeta-art"] = rec
    if start:
        rec["thread"].start()


def _reconcile_drain(should_stop):
    """Background reconcile: run the authoritative catalog rebuild (superseding the
    instant surgical preview with the canonical derivation), refresh scores, then
    hydrate art — in coalesced batches until the queue is empty. All queue decisions
    are under the lock so a concurrent _enqueue_reconcile never strands a game."""
    while not should_stop():
        with _ART_LOCK:
            if not _ART_QUEUE:
                _ART_RUNNING[0] = False
                return
            batch = set(_ART_QUEUE)
            _ART_QUEUE.clear()
            media = _ART_MEDIA[0]
        # canonical rebuild — provider matches/supplements are durable, so this
        # reproduces every accepted change and adds provider-record attrs + game_key.
        ok, err = _run_script("build_library.py", timeout=1800)
        if not ok:
            print("reconcile: build_library reported error (continuing): %s"
                  % (err or "")[:200], file=sys.stderr)
        _run_script("scores_fetch.py", args=["igdb"], timeout=180)
        _aimeta_apply_media(batch, media, should_stop)
    with _ART_LOCK:
        _ART_RUNNING[0] = False


def _apply_ss_matches(now):
    """Fetch accepted ScreenScraper matches by game id and cache the full record in
    ss_game, so build_library links them (metadata) and media_fetch pulls SS media."""
    import screenscraper as ss
    ssm = aimeta.accepted_ss_matches()
    if not ssm:
        return
    creds = config.screenscraper_creds()
    if not creds:
        return
    sc = sqlite3.connect(os.path.join(DATA, "screenscraper-cache.sqlite"))
    sc.execute("CREATE TABLE IF NOT EXISTS ss_game(norm_key TEXT, system TEXT, "
               "ss_id INTEGER, status TEXT, payload_json TEXT, fetched_at INTEGER, "
               "PRIMARY KEY(norm_key, system))")
    for m in ssm:
        if sc.execute("SELECT 1 FROM ss_game WHERE norm_key=? AND system=? AND "
                      "status='ok'", (m["norm_key"], m["system"])).fetchone():
            continue
        try:
            jeu, _ = ss.jeu_infos(creds, gameid=m["ss_id"])
        except Exception:
            jeu = None
        if jeu:
            sc.execute("INSERT OR REPLACE INTO ss_game(norm_key,system,ss_id,status,"
                       "payload_json,fetched_at) VALUES(?,?,?,?,?,?)",
                       (m["norm_key"], m["system"], m["ss_id"], "ok",
                        json.dumps(jeu, ensure_ascii=False), now))
            sc.commit()
    sc.close()


def _scoped_media_reconcile(touched, media, should_stop):
    """Background media reconcile for the games a wand apply touched — SCOPED to just
    those games, never the whole catalog. Pulls their provider art (IGDB + ScreenScraper +
    SteamGridDB gap-fill), re-chooses the best per kind, materializes the new bytes (content-
    addressed, so only new art downloads), refreshes scores, and — only if ai_art_auto_pick
    is on — AI-adjudicates once per game. This replaced the old ~18-min full media_fetch +
    ~10-min full build_library that ran on every apply."""
    if not touched:
        return
    now = int(time.time())
    import media_fetch as _mf
    if media is not False:                     # media:False => skip art entirely
        con = media_choose.con_index()
        try:
            con.execute("PRAGMA busy_timeout=30000")
            _mf.fetch_igdb(con, now, only=set(touched))            # scoped IGDB art
            try:
                _mf.fetch_screenscraper(con, now, only=set(touched))  # scoped SS art
            except Exception as e:
                print("scoped reconcile ss: %s" % str(e)[:150], file=sys.stderr)
            try:                                # SteamGridDB gap-fill (heroes/logos/covers)
                if _mf.config.steamgriddb_key():
                    ph = ",".join("?" * len(touched))
                    lc = ro(LIBRARY_DB)
                    try:
                        titles = {r["norm_key"]: r["canonical_title"] for r in lc.execute(
                            "SELECT norm_key, canonical_title FROM games "
                            "WHERE norm_key IN (%s)" % ph, tuple(touched))}
                        appids = {r["norm_key"]: r["source_id"] for r in lc.execute(
                            "SELECT g.norm_key, s.source_id FROM games g JOIN sources s "
                            "ON s.game_id=g.id WHERE s.source='steam' AND g.norm_key "
                            "IN (%s)" % ph, tuple(touched))}
                    finally:
                        lc.close()
                    tgts = [(nk, titles.get(nk, ""), appids.get(nk)) for nk in touched]
                    _mf.fetch_steamgriddb_targets(con, now, tgts)
            except Exception as e:
                print("scoped reconcile sgdb: %s" % str(e)[:150], file=sys.stderr)
            # no global prune_dead() here — it HTTP-probes every speculative URL in the
            # index (unscoped, slow). _prune_blank_media(touched) below covers these games;
            # the on-demand/scan rebuild does the whole-library dead-URL sweep.
            _mf._backfill_game_key(con)
            media_choose.select(con)                              # re-choose best per group
            con.commit()
        finally:
            con.close()
        try:
            _prune_blank_media(list(touched))
        except Exception:
            pass
        # NB: no materialize() here — like the old reconcile, chosen art serves from its
        # live reference until the separate materialize/publish step. Materializing here
        # would be a whole-library download (not scoped) and defeat the point.
    # scores read the cached IGDB ratings (no network) so a new match's score lands now
    _run_script("scores_fetch.py", args=["igdb"], timeout=180)
    # AI art adjudication — OFF by default (paid); once per game when on (see _art_adjudicated)
    if touched and config.get_bool("ai_art_auto_pick", False):
        lcon = ro(LIBRARY_DB)
        try:
            titles = {r["norm_key"]: r["canonical_title"]
                      for r in lcon.execute("SELECT norm_key, canonical_title FROM games")}
        finally:
            lcon.close()
        for nk in touched:
            if should_stop():
                break
            if _art_adjudicated(nk):
                continue
            _ai_adjudicate_game(nk, titles.get(nk, nk))
            _mark_art_adjudicated(nk, now)


# --- scoped media reconcile job (apply path): its own single-flight queue, distinct from
# the full rebuild+art drain (_ART_*) that the pin/scan paths still use. ---
_MEDIA_LOCK = threading.Lock()
_MEDIA_Q = set()
_MEDIA_SPEC = [True]
_MEDIA_RUNNING = [False]


def _scoped_media_drain(should_stop):
    while not should_stop():
        with _MEDIA_LOCK:
            if not _MEDIA_Q:
                _MEDIA_RUNNING[0] = False
                return
            batch = set(_MEDIA_Q)
            _MEDIA_Q.clear()
            media = _MEDIA_SPEC[0]
        _scoped_media_reconcile(batch, media, should_stop)
    with _MEDIA_LOCK:
        _MEDIA_RUNNING[0] = False


def _enqueue_media_reconcile(touched, media):
    """Queue touched games for the SCOPED media reconcile (no whole-catalog rebuild); start
    the drain if idle. Coalesces a burst of applies into one running job."""
    if not touched:
        return
    start = False
    with _MEDIA_LOCK:
        _MEDIA_Q.update(touched)
        _MEDIA_SPEC[0] = media
        if not _MEDIA_RUNNING[0]:
            _MEDIA_RUNNING[0] = True
            start = True
            cancel = threading.Event()
            rec = {"kind": "aimeta-media", "label": "Media reconcile (touched games)",
                   "cancel": cancel, "thread": None, "error": None, "run_id": None,
                   "cancelable": False, "started": time.time()}

            def worker():
                try:
                    _scoped_media_drain(cancel.is_set)
                except Exception as e:      # noqa: BLE001 — surface to the monitor
                    rec["error"] = str(e)[:300]
            t = threading.Thread(target=worker, daemon=True)
            rec["thread"] = t
            with _JOBS_LOCK:
                _JOBS["aimeta-media"] = rec
    if start:
        rec["thread"].start()


@app.post("/api/catalog/rebuild")
def catalog_rebuild():
    """Full authoritative catalog re-derivation (build_library) in the background. Wand
    applies no longer trigger this — they reconcile the touched games surgically — so run
    it on demand when you want a GLOBAL re-derivation (regional-duplicate merge, cross-era
    regrouping, provider-attribute union across sources). Also runs automatically on scans."""
    cur = _JOBS.get("catalog-rebuild")
    if cur and cur.get("thread") and cur["thread"].is_alive():
        return {"started": False, "running": True}

    def job(stop):
        ok, err = _run_script("build_library.py", timeout=1800)
        if ok:
            _run_script("scores_fetch.py", args=["igdb"], timeout=300)
        else:
            print("catalog rebuild: %s" % (err or "")[:200], file=sys.stderr)
    _start_job("catalog-rebuild", "catalog-rebuild", "Rebuild catalog (full)", job)
    return {"started": True}


def _apply_drain(should_stop, media):
    """Apply accepted findings, then loop while more are accepted — so accepting
    several games in quick succession coalesces into one running job instead of N.
    Each pass captures + marks only the findings it processed. The surgical apply above
    already made the catalog complete for the touched games (identity, game_key, rename,
    links, provider attributes), so this hands the touched games to a SCOPED media
    reconcile — NOT a whole-catalog rebuild. Applying a handful of games is now seconds,
    not the old ~10-20 min. (A full re-derivation is available on demand via
    /api/catalog/rebuild and still runs on library scans.)"""
    all_touched = set()
    while not should_stop():
        ids = aimeta.accepted_ids()
        if not ids:
            break
        all_touched |= _aimeta_apply(should_stop, only_ids=ids)
    _enqueue_media_reconcile(all_touched, media)


@app.post("/api/aimeta/accept")
def aimeta_accept(body: dict = Body(default={})):
    """Mark the selected changes ACCEPTED but do NOT apply — they queue in the
    pending-changes bar. Accept as many as you like across scans; a later single Apply
    then applies them all together in ONE catalog rebuild (no rebuild per accept)."""
    sels = (body or {}).get("selections")
    if not sels:
        raise HTTPException(400, "no selections")
    aimeta.apply_selection(sels)          # status -> accepted (+ per-attribute selection)
    return {"accepted": len(sels), "pending": aimeta.pending_count()}


@app.post("/api/aimeta/apply")
def aimeta_apply(body: dict = Body(default={})):
    """Apply the selected changes to the catalog (background: link provider
    matches, fetch their records, rebuild). Body may carry
    {selections:[{finding_id, attributes:[kinds]|null, match:bool}]} to apply an
    exact per-change selection; without it, every already-accepted finding applies.
    Coalesced: if an apply is already running, the just-accepted findings are picked
    up by its drain loop instead of starting a second (conflicting) rebuild."""
    sels = (body or {}).get("selections")
    if sels:
        aimeta.apply_selection(sels)
    media = (body or {}).get("media", True)   # True | False | [media kinds]
    cur = _JOBS.get("aimeta-apply")
    if cur and cur.get("thread") and cur["thread"].is_alive():
        return {"started": False, "coalesced": True,
                "selected": len(sels) if sels else None}
    _start_job("aimeta-apply", "aimeta-apply", "Apply AI metadata + rebuild",
               lambda stop: _apply_drain(stop, media))
    return {"started": True, "selected": len(sels) if sels else None}


_IGDB_PREVIEW_SIZE = {"cover": "t_cover_small", "background": "t_screenshot_med",
                      "screenshot": "t_screenshot_med"}


def _igdb_media_preview(igdb_ids):
    """One batched IGDB lookup → {igdb_id: [{kind, url}]} of the art a match would
    fetch on apply (cover, artwork→background, screenshots). Preview thumbnails; the
    real fetch (media_fetch) pulls the full-size versions. Empty on no creds/error."""
    ids = sorted({int(i) for i in igdb_ids if i})
    if not ids:
        return {}
    cid, tok = _igdb_token()
    if not tok:
        return {}
    import igdb as _igdb
    out = {}
    img = "https://images.igdb.com/igdb/image/upload/%s/%s.jpg"
    for i in range(0, len(ids), 200):
        batch = ids[i:i + 200]
        q = ("fields id,cover.image_id,artworks.image_id,screenshots.image_id; "
             "where id=(%s); limit 500;" % ",".join(str(x) for x in batch))
        try:
            for g in _igdb.query("games", q, cid, tok):
                art = []
                cov = (g.get("cover") or {}).get("image_id")
                if cov:
                    art.append({"kind": "cover",
                                "url": img % (_IGDB_PREVIEW_SIZE["cover"], cov)})
                for a in (g.get("artworks") or [])[:2]:
                    if a.get("image_id"):
                        art.append({"kind": "background",
                                    "url": img % (_IGDB_PREVIEW_SIZE["background"], a["image_id"])})
                for s in (g.get("screenshots") or [])[:4]:
                    if s.get("image_id"):
                        art.append({"kind": "screenshot",
                                    "url": img % (_IGDB_PREVIEW_SIZE["screenshot"], s["image_id"])})
                out[g["id"]] = art
        except Exception:
            pass
    return out


@app.post("/api/aimeta/media-diff")
def aimeta_media_diff(body: dict = Body(default={})):
    """Preview the media a finding would add/change on apply. Two parts per item:
    (1) the full ART SET the matched IGDB game supplies (cover + artwork + screenshots),
    each flagged new vs already-held — a light dry-run: ONE batched IGDB metadata call
    for every item, no image download, no mutation; and (2) the per-platform COVER
    before→after — own-console art is never displaced by neutral store/IGDB art
    (DESIGN §11.9), an entry on mismatched-identity neutral art reads as no-cover now →
    the match makes it adopt the new cover. SteamGridDB may add hero/logo art too on
    apply (flagged via `sgdb`); its dry-run isn't run here. Body:
    {items:[{norm_key, after_cover, igdb_id}]}."""
    items_in = [it for it in ((body or {}).get("items") or [])
                if isinstance(it, dict) and it.get("norm_key")][:200]
    if not items_in:
        return {"items": [], "sgdb": False}
    art_by_id = _igdb_media_preview([it.get("igdb_id") for it in items_in])
    try:
        import media_fetch as _mf
        sgdb = bool(_mf.config.steamgriddb_key())
    except Exception:
        sgdb = False
    con = lib()
    try:
        has_gk = _has_col(con, "games", "game_key")
        has_ek = _has_col(con, "games", "entry_key")
        eksel = ("entry_key, platform" if has_ek
                 else "norm_key AS entry_key, NULL AS platform")
        gksel = ", game_key" if has_gk else ""
        out = []
        for it in items_in:
            nk = it["norm_key"]
            after_url = it.get("after_cover") or None
            title = it.get("title") or nk
            # kinds the game already has chosen art for → mark added art new vs extra
            have_kinds = {r["kind"] for r in con.execute(
                "SELECT DISTINCT kind FROM m.media md WHERE md.norm_key=? AND md.chosen=1",
                (nk,))}
            added = [{"kind": a["kind"], "url": a["url"], "new": a["kind"] not in have_kinds}
                     for a in art_by_id.get(int(it["igdb_id"]), [])] if it.get("igdb_id") else []
            # store-locked: this match applies PER ENTRY to the ROMs only — the store
            # entry (pc/xbox) keeps its own identity + cover, so show it as unchanged.
            store_locked = bool(it.get("igdb_id")) and _store_locked_igdb(nk, it.get("igdb_id"))
            rows = con.execute(
                "SELECT id, %s, canonical_title%s FROM games WHERE norm_key=?"
                % (eksel, gksel), (nk,)).fetchall()
            plats = []
            for r in rows:
                platform = r["platform"] if "platform" in r.keys() else None
                gk = r["game_key"] if (has_gk and "game_key" in r.keys()) else None
                if store_locked and platform in ("pc", "xbox"):
                    plats.append({"entry_key": r["entry_key"], "platform": platform,
                                  "has_before": True, "own_art": False, "change": "none"})
                    continue
                own = con.execute(
                    "SELECT 1 FROM m.media md WHERE md.norm_key=? AND md.chosen=1 AND "
                    "md.kind='cover' AND COALESCE(md.system,'')=COALESCE(?,'') LIMIT 1",
                    (nk, platform)).fetchone()
                if has_gk and gk:
                    neu = con.execute(
                        "SELECT 1 FROM m.media md WHERE md.norm_key=? AND md.chosen=1 AND "
                        "md.kind='cover' AND COALESCE(md.system,'')='' AND md.game_key=? "
                        "LIMIT 1", (nk, gk)).fetchone()
                else:
                    neu = con.execute(
                        "SELECT 1 FROM m.media md WHERE md.norm_key=? AND md.chosen=1 AND "
                        "md.kind='cover' AND COALESCE(md.system,'')='' LIMIT 1",
                        (nk,)).fetchone()
                # own-console art is never displaced by neutral art → unchanged.
                if own:
                    change = "none"
                elif after_url:
                    change = "replace" if neu else "add"
                else:
                    change = "none"
                plats.append({
                    "entry_key": r["entry_key"], "platform": platform,
                    "has_before": bool(own or neu), "own_art": bool(own),
                    "change": change,
                })
            if any(p["change"] != "none" for p in plats) or any(a["new"] for a in added):
                out.append({"norm_key": nk, "title": title, "after_cover": after_url,
                            "platforms": plats, "added_art": added})
        return {"items": out, "sgdb": sgdb}
    finally:
        con.close()


# User-facing attribute kinds for the detail "view / edit all attributes" panel —
# the catalog vocabulary minus internal plumbing (install paths, activity stamps,
# app flags). Blank kinds are shown too, so the user can fill them in.
_EDITABLE_ATTR_KINDS = [
    "content_type",
    "release_type", "language", "release_year", "release_date", "platforms", "genres", "themes",
    "game_modes", "player_perspectives", "developers", "publishers", "series",
    "features", "categories", "age_ratings", "regions", "os", "device",
    "version", "completion_status", "user_score", "critic_score",
    "community_score", "playtime", "description",
]


def _entry_rom_paths(sources, limit=40):
    """Full on-disk path(s) for this entry's emulation/archive ROM files, straight from
    the ROM index (which already has fullpath/filename per file) — so the detail view
    can show WHERE the ROM lives, not just the title it matched. Returns
    [{path, filename, system}] deduped in path order. Empty for non-ROM games."""
    links = [(s.get("source_id") or "", s.get("title_raw") or "")
             for s in sources
             if s.get("source") in ("emulation", "archive") and s.get("title_raw")]
    if not links:
        return []
    out, seen = [], set()
    for dbp in aimeta._rom_indexes():
        aimeta._ensure_rom_index(dbp)
        try:
            rc = sqlite3.connect("file:%s?mode=ro" % dbp, uri=True, timeout=5)
            rc.row_factory = sqlite3.Row
        except sqlite3.OperationalError:
            continue
        try:
            for system, title in links:
                try:
                    rows = rc.execute(
                        "SELECT system, filename, fullpath, relpath FROM roms "
                        "WHERE game=? AND (system=? OR ?='') LIMIT ?",
                        (title, system, system or "", limit)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                for r in rows:
                    fp = r["fullpath"] or r["relpath"]
                    if fp and fp not in seen:
                        seen.add(fp)
                        out.append({"path": fp,
                                    "filename": r["filename"] or os.path.basename(fp),
                                    "system": r["system"]})
                        if len(out) >= limit:
                            break
        finally:
            rc.close()
        if len(out) >= limit:
            break
    return out


@app.get("/api/games/{norm_key}")
def game_detail(norm_key: str):
    con = lib()
    try:
        g, base, platform = _resolve_entry(con, norm_key)
        if not g:
            raise HTTPException(404, "no such game")
        gid = g["id"]
        _keys = g.keys()
        # sibling platform entries of the same game → "also owned on", grouped by the
        # cross-ref base_key (so an era-separated retro title isn't grouped with the
        # modern game that happens to share a norm_key).
        also = []
        if "entry_key" in _keys and platform is not None:
            _grp = g["base_key"] if "base_key" in _keys and g["base_key"] else base
            for row in con.execute(
                    "SELECT entry_key, platform, canonical_title FROM games "
                    "WHERE %s=? AND entry_key<>? ORDER BY platform"
                    % ("base_key" if "base_key" in _keys else "norm_key"),
                    (_grp, g["entry_key"])):
                also.append({"entry_key": row["entry_key"], "platform": row["platform"],
                             "title": row["canonical_title"]})
        _st = ", state" if _has_col(con, "sources", "state") else ""
        sources = [dict(r) for r in con.execute(
            "SELECT source, platform, source_id, title_raw, detail" + _st +
            " FROM sources WHERE game_id=?", (gid,))]
        osmap = _os_map()
        for s in sources:
            oss = osmap.get((s["source"], str(s["source_id"])))
            if oss:
                s["os"] = [o for o in ("windows", "mac", "linux") if oss.get(o)]
            elif s["source"] == "epic":
                s["os"] = ["windows"]  # Epic Games Store is Windows-only (no Linux client)
            else:
                s["os"] = None
        for s in sources:
            s.setdefault("collection", None)     # real rows aren't collection-credited
        # Collection credit (DESIGN §13): this game is owned via any COMPILATION the
        # user owns. Add a synthetic "in your library" row + an "also owned on" credit
        # for each owned collection whose member set includes this game.
        try:
            _bk_col = "g2.base_key" if "base_key" in _keys else "g2.norm_key"
            _seen_plat = {platform} | {a["platform"] for a in also}
            for c in compilations.credits_for(DATA, base):
                if c["coll_key"] == base:
                    continue                     # a collection never credits itself
                owned = con.execute(
                    "SELECT s.source, g2.platform, g2.entry_key FROM games g2 "
                    "JOIN sources s ON s.game_id=g2.id "
                    "WHERE (g2.norm_key=? OR " + _bk_col + "=?) "
                    "AND COALESCE(s.state,'have')='have' LIMIT 1",
                    (c["coll_key"], c["coll_key"])).fetchone()
                if not owned:
                    continue                     # user doesn't own this collection
                sources.append({
                    "source": owned["source"], "platform": owned["platform"],
                    "source_id": "", "title_raw": c["name"],
                    "detail": "part of “%s”" % c["name"], "state": "have", "os": None,
                    "collection": c["name"], "via_collection": owned["entry_key"]})
                if owned["platform"] and owned["platform"] not in _seen_plat:
                    _seen_plat.add(owned["platform"])
                    also.append({"entry_key": owned["entry_key"],
                                 "platform": owned["platform"], "title": c["name"],
                                 "via": c["name"]})
        except Exception as _ce:                 # credit is best-effort, never 500s detail
            print("collection credit: %s" % str(_ce)[:150], file=sys.stderr)
        attrs = {}                       # kind -> [values] (compat)
        prov = {}                        # kind -> [{value, origins, ai}] provenance
        for r in con.execute("SELECT kind, value, origin FROM game_attributes "
                             "WHERE game_id=?", (gid,)):
            attrs.setdefault(r["kind"], []).append(r["value"])
            origins = [o for o in (r["origin"] or "").split(",") if o]
            prov.setdefault(r["kind"], []).append(
                {"value": r["value"], "origins": origins, "ai": "ai" in origins})
        ov = overrides.overrides_for(base)   # user's chosen canonical per kind
        # Reflect the user's pinned / hand-typed canonical value in the DISPLAYED
        # attributes so an edit (or a value added to a blank) actually shows in the
        # detail view. The provenance block below still lists every source value.
        for _k, _o in ov.items():
            attrs[_k] = [_o["value"]]
        links = [dict(r) for r in con.execute(
            "SELECT provider, provider_id, slug, url FROM metadata_links "
            "WHERE game_id=?", (gid,))]
        # Provider links to surface as favicon shortcuts by the media tabs: every
        # metadata link that has a page URL (IGDB, ScreenScraper…) PLUS store-page
        # links derivable from an owned source (Steam appid → store page). Epic/GOG/
        # console deep-links aren't reliably derivable from a bare id, so they're
        # skipped rather than guessed into a dead link.
        provider_links, _pl_seen = [], set()
        for l in links:
            if l.get("url") and l["provider"] not in _pl_seen:
                provider_links.append({"provider": l["provider"], "url": l["url"]})
                _pl_seen.add(l["provider"])
        for s in sources:
            src, sid = s.get("source"), str(s.get("source_id") or "")
            if src in _pl_seen:
                continue
            url = ("https://store.steampowered.com/app/%s" % sid
                   if src == "steam" and sid.isdigit() else None)
            if url:
                provider_links.append({"provider": src, "url": url})
                _pl_seen.add(src)
        # media kinds available to THIS entry: its own console's chosen art, plus
        # platform-neutral store/IGDB art whose identity matches this entry
        # (media.game_key = the entry's game_key, DESIGN §11.9). An era-collision entry
        # (game_key title:<nk>) thus forfeits the resolved game's neutral art; a stray port
        # (adopts igdb:<id>) keeps it. Own-console art is always matched by norm_key+system.
        _gk = g["game_key"] if "game_key" in _keys and g["game_key"] else None
        if _gk:
            # own-console art per norm_key; neutral art by game IDENTITY across norm_keys
            # (a title split into two norm_keys shares its one fetched art set).
            _mk_sql = ("SELECT DISTINCT kind FROM m.media WHERE chosen=1 AND ("
                       "(norm_key=? AND COALESCE(system,'')=?) "
                       "OR (COALESCE(system,'')='' AND game_key=?)) ORDER BY kind")
            _mk_args = (base, platform or "", _gk)
        else:
            _mk_sql = ("SELECT DISTINCT kind FROM m.media WHERE norm_key=? AND chosen=1 "
                       "AND (COALESCE(system,'')=? OR COALESCE(system,'')='') ORDER BY kind")
            _mk_args = (base, platform or "")
        media_kinds = [r["kind"] for r in con.execute(_mk_sql, _mk_args)]
        return {
            "norm_key": base,
            "entry_key": g["entry_key"] if "entry_key" in _keys else base,
            "platform": platform,
            "also_owned_on": also,             # sibling platform entries (cross-ref)
            "title": g["canonical_title"],
            "sources": sources,
            "rom_files": _entry_rom_paths(sources),   # on-disk ROM path(s) for this entry
            "attributes": attrs,
            "attribute_provenance": prov,     # per-value origins (+ ai flag → ✨)
            "attribute_overrides": ov,        # user re-pointed canonical values
            "editable_kinds": _EDITABLE_ATTR_KINDS,   # full vocab for the "all attributes" editor
            "tags": _game_tags(con, gid, base),
            "scores": _score_breakdown(con, base),
            "metadata_links": links,
            "provider_links": provider_links,   # favicon shortcuts (metadata + steam store)
            "media_kinds": media_kinds,
            "ai_meta": aimeta.finding_for(base),   # AI audit/supplement, if any
            "ownership": ownership.list_for(DATA, base),  # manual physical/want facts
            "framing": framing.get_all(DATA, base),       # per-kind image position+zoom
            "hero_pref": framing.get_hero(DATA, base),     # hero override (marquee|<kind>|None)
            "collection": compilations.get_collection(DATA, base),  # if THIS entry is a compilation
        }
    finally:
        con.close()


def _apply_ownership_live(norm_key: str, title: str):
    """Reflect ownership.sqlite into the live library right away (no full rebuild):
    replace this game's manual `own:%` source rows and re-derive its wanted flag.
    On the next full rebuild build_library re-merges the same facts idempotently."""
    facts = ownership.list_for(DATA, norm_key)
    con = sqlite3.connect(LIBRARY_DB)
    try:
        if not _has_col(con, "sources", "state"):
            return                      # pre-migration schema; a rebuild will apply it
        row = con.execute("SELECT id FROM games WHERE norm_key=?", (norm_key,)).fetchone()
        if row is None:
            if facts and not any(f["state"] == "have" for f in facts) and title:
                con.execute(               # want-only for a not-yet-cataloged game
                    "INSERT INTO games(canonical_title,norm_key,n_sources,n_kinds,"
                    "sources_summary,has_emulation,has_steam,has_gog,has_epic,has_itch,"
                    "has_archive,in_playnite,in_launchbox,wanted) "
                    "VALUES(?,?,0,0,'',0,0,0,0,0,0,0,0,1)", (title, norm_key))
                row = con.execute("SELECT id FROM games WHERE norm_key=?",
                                  (norm_key,)).fetchone()
            else:
                return
        gid = row[0]
        con.execute("DELETE FROM sources WHERE game_id=? AND source_id LIKE 'own:%'", (gid,))
        for f in facts:
            src = ownership.FORM_TO_SOURCE.get(f["form"], f["form"])
            con.execute(
                "INSERT INTO sources(game_id,source,platform,source_id,title_raw,detail,"
                "state) VALUES(?,?,?,?,?,?,?)",
                (gid, src, f["platform"] or src, "own:%s:%s" % (src, f["platform"]),
                 title, f["note"], f["state"]))
        have = con.execute("SELECT 1 FROM sources WHERE game_id=? AND state='have' "
                           "LIMIT 1", (gid,)).fetchone()
        con.execute("UPDATE games SET wanted=?, n_sources=(SELECT COUNT(*) FROM sources "
                    "WHERE game_id=?) WHERE id=?", (0 if have else 1, gid, gid))
        con.commit()
    finally:
        con.close()


def _game_title(norm_key):
    con = lib()
    try:
        r = con.execute("SELECT canonical_title FROM games WHERE norm_key=?",
                        (norm_key,)).fetchone()
        return r["canonical_title"] if r else ""
    finally:
        con.close()


@app.post("/api/games/{norm_key}/framing")
def set_framing(norm_key: str, body: dict = Body(...)):
    """Position + zoom for one image kind inside its viewport (e.g. the hero
    'background' or a 'cover'). Applied at render time, keyed by norm_key."""
    norm_key = _split_entry_key(norm_key)[0]
    body = body or {}
    kind = (body.get("kind") or "").strip()
    if not kind:
        raise HTTPException(400, "kind is required")
    fr = framing.set_frame(DATA, norm_key, kind,
                           top=body.get("top", 0), right=body.get("right", 0),
                           bottom=body.get("bottom", 0), left=body.get("left", 0),
                           zoom=body.get("zoom", 1.0))
    return {"kind": kind, "framing": fr}


@app.delete("/api/games/{norm_key}/framing")
def clear_framing(norm_key: str, kind: str):
    framing.clear(DATA, _split_entry_key(norm_key)[0], kind)
    return {"ok": True}


@app.post("/api/games/{norm_key}/hero")
def set_hero_pref(norm_key: str, body: dict = Body(...)):
    """Override what drives the detail hero for one game: 'marquee' (force the
    scrolling media dance), a media kind to force as the static background, or
    'auto'/'' to clear (default hero→background→header→marquee logic). Keyed by
    norm_key, applied at render time."""
    source = ((body or {}).get("source") or "").strip()
    saved = framing.set_hero(DATA, _split_entry_key(norm_key)[0], source)
    return {"hero_pref": saved}


@app.get("/api/games/{norm_key}/ownership")
def get_ownership(norm_key: str):
    return {"ownership": ownership.list_for(DATA, norm_key)}


@app.post("/api/games/{norm_key}/ownership")
def set_ownership(norm_key: str, body: dict = Body(...)):
    """Record a per-format ownership fact — physical disc, or a per-platform want
    that coexists with what you already own (e.g. 'want the Switch ROM')."""
    body = body or {}
    title = (body.get("title") or _game_title(norm_key) or "").strip()
    if not title:
        raise HTTPException(400, "unknown game — pass a title to create it")
    try:
        ownership.set_fact(DATA, norm_key, title, body.get("form"),
                           body.get("platform", ""), body.get("state"),
                           body.get("note", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    _apply_ownership_live(norm_key, title)
    return {"ownership": ownership.list_for(DATA, norm_key)}


@app.delete("/api/games/{norm_key}/ownership")
def clear_ownership(norm_key: str, form: str, platform: str = "", state: str = ""):
    ownership.clear_fact(DATA, norm_key, form, platform, state)
    _apply_ownership_live(norm_key, _game_title(norm_key))
    return {"ownership": ownership.list_for(DATA, norm_key)}


@app.get("/api/games/{norm_key}/releases")
def game_releases(norm_key: str):
    """IGDB's cross-platform release list for a game (cache-first, self-healing) —
    powers the ownership overlay's 'this game also came out on…' section."""
    try:
        return igdb_enrich.releases_for(norm_key)
    except Exception as e:                       # network/creds issue — degrade, don't 500
        return {"resolved": False, "releases": [], "error": str(e)}


@app.get("/api/systems")
def known_systems():
    """The searchable catalog of known gaming systems (IGDB platforms), for the
    ownership overlay's 'add any system' search. Cached after the first call."""
    try:
        return {"systems": igdb_enrich.all_platforms()}
    except Exception as e:
        return {"systems": [], "error": str(e)}


@app.post("/api/games/{norm_key}/attribute")
def set_attribute_override(norm_key: str, body: dict = Body(...)):
    """Re-point one attribute to a chosen value + source (another provider's value
    or a hand-typed 'manual' one). Body: {kind, value, origin}."""
    body = body or {}
    kind = (body.get("kind") or "").strip()
    value = body.get("value")
    if not kind or value in (None, ""):
        raise HTTPException(400, "kind and value are required")
    try:
        overrides.set_override(norm_key, kind, value, body.get("origin") or "manual")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"override": overrides.overrides_for(norm_key).get(kind)}


@app.delete("/api/games/{norm_key}/attribute/{kind}")
def clear_attribute_override(norm_key: str, kind: str):
    """Remove an override — the attribute reverts to its provider-derived value(s)."""
    overrides.clear_override(norm_key, kind)
    return {"cleared": True}


# Friendly names + type for each rating source (drives the per-source display).
SCORE_SOURCES = {
    "igdb": "IGDB", "steam": "Steam players", "metacritic": "Metacritic",
    "screenscraper": "ScreenScraper", "gog": "GOG players",
}


def _score_breakdown(con, norm_key):
    """Unified Ludodex score + pooled critic/user + every source's original rating,
    from the multi-source ratings store (scores_fetch.py)."""
    gs = con.execute("SELECT universal, critic, user FROM sco.game_scores "
                     "WHERE norm_key=?", (norm_key,)).fetchone()
    srcs = [{"source": r["source"], "name": SCORE_SOURCES.get(r["source"], r["source"]),
             "kind": r["kind"], "score": round(r["score"]) if r["score"] is not None else None,
             "votes": r["votes"], "raw": r["raw"]}
            for r in con.execute(
                "SELECT source, kind, score, votes, raw FROM sco.ratings "
                "WHERE norm_key=? AND score IS NOT NULL ORDER BY kind DESC, source",
                (norm_key,))]
    return {"ludodex": gs["universal"] if gs else None,
            "critic": gs["critic"] if gs else None,
            "players": gs["user"] if gs else None,
            "critic_weight": _ludodex_weight(),
            "sources": srcs}


def _game_tags(con, gid, norm_key):
    """Merged tags for a game: imported-origin tags baked into the catalog
    (e.g. Playnite) + live user tags (origin 'ludodex'). -> [{tag, origins}]."""
    try:
        rows = con.execute(
            "SELECT tag, group_concat(DISTINCT origin) AS o FROM ("
            "  SELECT tag, origin FROM game_tags WHERE game_id=? AND origin<>'ludodex'"
            "  UNION SELECT tag, 'ludodex' FROM t.user_tags WHERE norm_key=?"
            ") GROUP BY tag ORDER BY tag COLLATE NOCASE", (gid, norm_key)).fetchall()
    except sqlite3.OperationalError:            # catalog predates game_tags
        rows = con.execute(
            "SELECT tag, 'ludodex' AS o FROM t.user_tags WHERE norm_key=? "
            "ORDER BY tag COLLATE NOCASE", (norm_key,)).fetchall()
    return [{"tag": r["tag"], "origins": (r["o"] or "").split(",")} for r in rows]


@app.get("/api/games/{norm_key}/tags")
def get_game_tags(norm_key: str):
    """All tags for a game (imported + user), each with its origin(s)."""
    con = lib()
    try:
        row = con.execute("SELECT id FROM games WHERE norm_key=?", (norm_key,)).fetchone()
        return {"norm_key": norm_key,
                "tags": _game_tags(con, row["id"] if row else -1, norm_key)}
    finally:
        con.close()


@app.post("/api/games/{norm_key}/tags")
def add_game_tag(norm_key: str, body: dict = Body(...)):
    """Add a user (ludodex-origin) tag. Durable — survives catalog rebuilds."""
    tag = (body or {}).get("tag", "").strip()
    if not tag:
        raise HTTPException(400, "tag is required")
    if len(tag) > 60:
        raise HTTPException(400, "tag too long (max 60 chars)")
    tc = _tags_con()
    tc.execute("INSERT OR IGNORE INTO user_tags(norm_key,tag,created) VALUES(?,?,?)",
               (norm_key, tag, time.time()))
    tc.commit()
    tc.close()
    return get_game_tags(norm_key)


@app.delete("/api/games/{norm_key}/tags/{tag}")
def remove_game_tag(norm_key: str, tag: str):
    """Remove a user tag (imported-origin tags can't be removed here)."""
    tc = _tags_con()
    tc.execute("DELETE FROM user_tags WHERE norm_key=? AND tag=?", (norm_key, tag))
    tc.commit()
    tc.close()
    return get_game_tags(norm_key)


@app.get("/api/games/{norm_key}/achievements")
def game_achievements(norm_key: str):
    """RetroAchievements for a game: full set + which the user earned.
    Populated by ra_fetch.py; empty/unmatched if never pulled."""
    if not os.path.exists(RA_DB):
        return {"matched": False, "num_ach": 0, "num_earned": 0, "achievements": []}
    con = ro(RA_DB)
    try:
        prog = con.execute("SELECT ra_id, num_ach, num_earned, pulled_at "
                           "FROM ra_progress WHERE norm_key=?", (norm_key,)).fetchone()
        rows = con.execute(
            "SELECT ra_ach_id, title, description, points, badge, earned, earned_date "
            "FROM ra_ach WHERE norm_key=? ORDER BY earned DESC, points, title",
            (norm_key,)).fetchall()
    finally:
        con.close()
    achs = [{
        "id": r["ra_ach_id"], "title": r["title"], "description": r["description"],
        "points": r["points"], "earned": bool(r["earned"]),
        "earned_date": r["earned_date"],
        "badge": ("https://media.retroachievements.org/Badge/%s%s.png"
                  % (r["badge"], "" if r["earned"] else "_lock")) if r["badge"] else None,
    } for r in rows]
    return {"matched": bool(prog), "ra_id": prog["ra_id"] if prog else None,
            "num_ach": prog["num_ach"] if prog else 0,
            "num_earned": prog["num_earned"] if prog else 0,
            "pulled_at": prog["pulled_at"] if prog else None,
            "achievements": achs}


# ------------------------------------------------------------------- media library

# Human-readable "why this asset exists" copy for every canonical kind (media.KINDS).
KIND_DESC = {
    "cover": "Front box art / portrait poster — the game's primary face, used on cards and grids.",
    "box_back": "Back of the box — screenshots, blurb and credits printed on the reverse.",
    "box_3d": "3D box render — the case shown at an angle, the way storefronts display it.",
    "box_spine": "Box spine — the narrow edge that shows when the case sits on a shelf.",
    "physical_media": "The physical medium itself — cartridge, disc or tape with its label.",
    "background": "Full-bleed backdrop art shown behind the page (Steam-style library background).",
    "hero": "Wide key-art banner, usually with the logo baked in (Steam library hero).",
    "header": "Small wide capsule/banner used in store rows and lists (~460×215).",
    "logo": "Transparent title logo — the game's name as stylised art, no background.",
    "icon": "Small square app icon.",
    "marquee": "Illuminated arcade marquee art that sits atop the cabinet.",
    "bezel": "Screen bezel / overlay that frames the play area on arcade & emulator screens.",
    "arcade_cabinet": "Photo or render of the full arcade cabinet.",
    "arcade_controls": "The control-panel (CPO) art / button-and-stick layout.",
    "pcb": "Photo of the game's circuit board.",
    "screenshot": "In-game screenshot.",
    "title_screen": "The game's title / attract screen.",
    "mix": "Composited “mix” image — logo, character and background combined (EmulationStation miximage).",
    "flyer": "Promotional flyer / advertisement poster.",
    "map": "Game world or level map.",
    "video": "Preview or trailer video.",
    "manual": "Scanned instruction manual (PDF).",
    "other": "Uncategorised asset that didn't match a known classification (never dropped; logged).",
}
SCALAR_SET = set(media.SCALAR_KINDS)
MULTI_CAP = 10  # non-scalar kinds (screenshots, video, …) can pin up to this many


def _pins():
    """Durable pin store (survives media-index rescans). One row per pinned asset,
    keyed by the stable (provider, kind, ref) identity + norm_key, with a rank."""
    con = sqlite3.connect(PINS_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS pins(
        norm_key TEXT, kind TEXT, provider TEXT, ref TEXT, rank INTEGER,
        PRIMARY KEY(norm_key, kind, provider, ref))""")
    con.row_factory = sqlite3.Row
    return con


def _os_map():
    """(source, source_id) -> {windows, mac, linux} bools, from the durable OS store.
    Populated by os_fetch.py; empty until then, so OS shows as “—”."""
    if not os.path.exists(OS_DB):
        return {}
    con = ro(OS_DB)
    try:
        return {(r["source"], r["source_id"]):
                {"windows": r["windows"], "mac": r["mac"], "linux": r["linux"]}
                for r in con.execute("SELECT source, source_id, windows, mac, linux "
                                     "FROM os_support")}
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()


def _pin_map(norm_key):
    """(kind, provider, ref) -> rank for a game's pinned assets."""
    if not os.path.exists(PINS_DB):
        return {}
    con = _pins()
    try:
        return {(r["kind"], r["provider"], r["ref"]): r["rank"]
                for r in con.execute("SELECT kind, provider, ref, rank FROM pins "
                                     "WHERE norm_key=?", (norm_key,))}
    finally:
        con.close()


@app.get("/api/media-kinds")
def media_kinds():
    """The full media classification vocabulary, in display order, with the copy
    shown in tooltips and whether each kind holds a single asset or many."""
    return {"kinds": [{"kind": k, "scalar": k in SCALAR_SET,
                       "cap": 1 if k in SCALAR_SET else MULTI_CAP,
                       "description": KIND_DESC.get(k, "")}
                      for k in media.KINDS]}


@app.get("/api/games/{norm_key}/media")
def game_media(norm_key: str):
    """Every media asset THIS entry has, grouped by kind, annotated with pin state.
    Filtered to the entry's own console + platform-neutral art (never another
    console's), so the detail hero/candidates match the platform. `pinned`/`rank`
    come from the durable (title-level) pin store."""
    base, platform = _split_entry_key(norm_key)
    _cols = ("id, kind, provider, ref, ref_type, ext, width, height, chosen, sha1")
    con = lib()
    try:
        if platform and _has_col(con, "games", "game_key"):
            # Mirror the SERVE gate (DESIGN §11.9) so the picker only offers art this
            # entry can actually display: own-console art (by system) always, but
            # platform-neutral store/IGDB art ONLY when its identity matches THIS
            # entry's game_key. An era-collision entry (game_key title:<nk>) thus isn't
            # shown — or allowed to "use" — the modern same-title game's covers that
            # serve would blank out (e.g. the Sega 32X "Doom" and the 2016 DOOM art).
            _gkr = con.execute("SELECT game_key FROM games WHERE norm_key=? AND "
                               "COALESCE(platform,'')=? LIMIT 1", (base, platform)).fetchone()
            _gk = (_gkr["game_key"] if _gkr else None) or "\x00"
            rows = con.execute(
                "SELECT " + _cols + " FROM m.media WHERE norm_key=? AND "
                "(COALESCE(system,'')=? OR (COALESCE(system,'')='' AND "
                "COALESCE(game_key,'')=?)) ORDER BY kind", (base, platform, _gk)).fetchall()
        elif platform:
            rows = con.execute(
                "SELECT " + _cols + " FROM m.media WHERE norm_key=? "
                "AND (COALESCE(system,'')=? OR COALESCE(system,'')='') ORDER BY kind",
                (base, platform)).fetchall()
        else:
            rows = con.execute(
                "SELECT " + _cols + " FROM m.media WHERE norm_key=? ORDER BY kind",
                (base,)).fetchall()
    finally:
        con.close()
    pins = _pin_map(base)
    noredist = mediaflags.no_redist_for(base)   # (kind,provider,ref) not-shareable
    assets = []
    for r in rows:
        # Don't surface assets that can't actually be served on THIS host, or they
        # render as blank placeholders: a `file` ref whose file lives only on the
        # producer (Deck) and isn't materialized here. URL refs stay (materialized
        # on demand; genuinely-dead ones are removed by `media_fetch.py prune`).
        if r["ref_type"] == "file" and not r["sha1"] and not os.path.exists(r["ref"]):
            continue
        rank = pins.get((r["kind"], r["provider"], r["ref"]))
        ext = (r["ext"] or "").lower()
        is_img = ext in ("jpg", "jpeg", "png", "webp", "gif", "bmp")
        has_preview = is_img or ext == "pdf"    # PDFs preview their first page
        assets.append({
            "id": r["id"], "kind": r["kind"], "provider": r["provider"],
            "ref_type": r["ref_type"], "ext": r["ext"],
            "width": r["width"], "height": r["height"],
            "is_image": is_img,
            "pinned": rank is not None, "rank": rank, "chosen": bool(r["chosen"]),
            "redistributable": (r["kind"], r["provider"], r["ref"]) not in noredist,
            "url": "/api/media-asset/%d" % r["id"],
            "thumb": "/api/media-asset/%d?size=thumb" % r["id"] if has_preview else None,
            "user": False,
        })
    # durable user uploads (added via the All Media upload buttons) — always "active"
    uc = _umedia_con()
    try:
        urows = uc.execute(
            "SELECT id, kind, sha1, ext, width, height, origin FROM user_media "
            "WHERE norm_key=? ORDER BY created DESC", (base,)).fetchall()
    finally:
        uc.close()
    for r in urows:
        ext = (r["ext"] or "").lower()
        is_img = ext in ("jpg", "jpeg", "png", "webp", "gif", "bmp")
        has_preview = is_img or ext == "pdf"
        assets.append({
            "id": r["id"], "kind": r["kind"], "provider": "user",
            "ref_type": "user", "ext": r["ext"],
            "width": r["width"], "height": r["height"],
            "is_image": is_img, "pinned": True, "rank": None, "chosen": False,
            "redistributable": True,
            "url": "/api/user-media-asset/%d" % r["id"],
            "thumb": "/api/user-media-asset/%d?size=thumb" % r["id"] if has_preview else None,
            "user": True,
        })
    return {"norm_key": norm_key, "scalar_kinds": list(media.SCALAR_KINDS),
            "multi_cap": MULTI_CAP, "assets": assets}


@app.post("/api/games/{norm_key}/pins")
def set_pins(norm_key: str, body: dict = Body(...)):
    """Set the pinned assets (and their order) for one kind of a game. Send the
    full ordered list of asset ids you want pinned — this replaces the prior set.
    Scalar kinds keep at most 1; other kinds keep up to MULTI_CAP, in order."""
    _ekey = norm_key                              # keep entry id for the filtered return
    norm_key = _split_entry_key(norm_key)[0]      # media/pins are keyed by base title
    kind = body.get("kind")
    ids = body.get("ids") or []
    if not kind:
        raise HTTPException(400, "kind required")
    cap = 1 if kind in SCALAR_SET else MULTI_CAP
    # Resolve each id to its stable identity, keeping only assets of this game+kind.
    con = lib()
    try:
        rows = {r["id"]: r for r in con.execute(
            "SELECT id, provider, ref FROM m.media WHERE norm_key=? AND kind=?",
            (norm_key, kind))}
    finally:
        con.close()
    ordered = [i for i in ids if i in rows][:cap]
    pc = _pins()
    try:
        pc.execute("DELETE FROM pins WHERE norm_key=? AND kind=?", (norm_key, kind))
        for rank, aid in enumerate(ordered, start=1):
            r = rows[aid]
            pc.execute("INSERT OR REPLACE INTO pins VALUES(?,?,?,?,?)",
                       (norm_key, kind, r["provider"], r["ref"], rank))
        pc.commit()
    finally:
        pc.close()
    # For a single-asset kind the #1 pin IS the used asset — reflect it in the media
    # index NOW so the served cover/art follows the user's choice immediately (a
    # later media_choose re-select honors the same pins, so it won't revert).
    if kind in SCALAR_SET and ordered:
        wc = sqlite3.connect(INDEX_DB, timeout=30)
        try:
            wc.execute("UPDATE media SET chosen=0 WHERE norm_key=? AND kind=?",
                       (norm_key, kind))
            wc.execute("UPDATE media SET chosen=1 WHERE id=?", (ordered[0],))
            wc.commit()
        finally:
            wc.close()
    return game_media(_ekey)


def _asset_identity(norm_key, aid):
    """(kind, provider, ref) for a provider media asset id, or None."""
    con = lib()
    try:
        r = con.execute("SELECT kind, provider, ref FROM m.media "
                        "WHERE id=? AND norm_key=?", (aid, norm_key)).fetchone()
    finally:
        con.close()
    return (r["kind"], r["provider"], r["ref"]) if r else None


@app.post("/api/games/{norm_key}/media/{aid}/ban")
def ban_media(norm_key: str, aid: int):
    """Ban a provider asset: delete it from the index AND remember never to
    re-download it (media_fetch skips banned refs). Unban later in Settings."""
    _ekey = norm_key
    norm_key = _split_entry_key(norm_key)[0]
    ident = _asset_identity(norm_key, aid)
    if not ident:
        raise HTTPException(404, "no such asset")
    kind, provider, ref = ident
    mediaflags.ban(norm_key, kind, provider, ref)
    wc = sqlite3.connect(INDEX_DB, timeout=30)
    try:
        wc.execute("DELETE FROM media WHERE norm_key=? AND kind=? AND provider=? "
                   "AND ref=?", (norm_key, kind, provider, ref))
        wc.commit()
    finally:
        wc.close()
    # drop any pin on it too, so it doesn't linger as a phantom pinned ref
    pc = _pins()
    try:
        pc.execute("DELETE FROM pins WHERE norm_key=? AND kind=? AND provider=? "
                   "AND ref=?", (norm_key, kind, provider, ref))
        pc.commit()
    finally:
        pc.close()
    return game_media(_ekey)


@app.post("/api/games/{norm_key}/media/{aid}/redist")
def set_media_redist(norm_key: str, aid: int, body: dict = Body(default={})):
    """Toggle whether a provider asset is redistributable (copied to other machines
    when games are sent to them). Default is redistributable; this stores the 'no'."""
    _ekey = norm_key
    norm_key = _split_entry_key(norm_key)[0]
    ident = _asset_identity(norm_key, aid)
    if not ident:
        raise HTTPException(404, "no such asset")
    kind, provider, ref = ident
    mediaflags.set_redist(norm_key, kind, provider, ref,
                          bool((body or {}).get("redistributable", True)))
    return game_media(_ekey)


@app.get("/api/media/banned")
def banned_media():
    """Banned assets, annotated with the game title, for the Settings unban list."""
    out = []
    con = lib()
    try:
        for b in mediaflags.list_banned():
            g = con.execute("SELECT canonical_title FROM games WHERE norm_key=?",
                            (b["norm_key"],)).fetchone()
            b["title"] = g["canonical_title"] if g else b["norm_key"]
            out.append(b)
    finally:
        con.close()
    return {"banned": out}


@app.post("/api/media/unban")
def unban_media(body: dict = Body(...)):
    """Lift a ban so the asset can be re-fetched from its provider again."""
    nk = (body or {}).get("norm_key")
    kind = (body or {}).get("kind")
    provider = (body or {}).get("provider")
    ref = (body or {}).get("ref")
    if not all((nk, kind, provider, ref)):
        raise HTTPException(400, "norm_key, kind, provider, ref required")
    mediaflags.unban(nk, kind, provider, ref)
    return {"ok": True}


# --------------------------------------------------------------- user media upload
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "bmp"}
ALLOWED_EXTS = IMAGE_EXTS | {"mp4", "webm", "pdf"}
MAX_UPLOAD = 80 * 1024 * 1024      # 80 MB
_MIME_EXT = {
    "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
    "image/webp": "webp", "image/gif": "gif", "image/bmp": "bmp",
    "video/mp4": "mp4", "video/webm": "webm", "application/pdf": "pdf",
}


def _norm_ext(ext):
    ext = (ext or "").lower().lstrip(".").split("?")[0].strip()
    return "jpg" if ext == "jpeg" else ext


def _ext_from(name, content_type=None):
    """Best-effort extension from a filename and/or content-type header."""
    if name and "." in name:
        e = _norm_ext(name.rsplit(".", 1)[1])
        if e in ALLOWED_EXTS:
            return e
    ct = (content_type or "").split(";")[0].strip().lower()
    return _MIME_EXT.get(ct)


def _umedia_path(norm_key, kind):
    """Local (path, ext) of the active user upload for a kind (most recent), or None."""
    uc = _umedia_con()
    try:
        r = uc.execute("SELECT sha1, ext FROM user_media WHERE norm_key=? AND kind=? "
                       "ORDER BY created DESC LIMIT 1", (norm_key, kind)).fetchone()
    finally:
        uc.close()
    if not r:
        return None
    p = os.path.join(REPO, "%s.%s" % (r["sha1"], r["ext"]))
    return (p, r["ext"]) if os.path.exists(p) else None


def _store_upload(norm_key, kind, data, ext, origin):
    """Write bytes into the content-addressed REPO and index them as a user upload."""
    _ekey = norm_key
    norm_key = _split_entry_key(norm_key)[0]
    if kind not in media.KINDS:
        raise HTTPException(400, "unknown media kind %r" % kind)
    ext = _norm_ext(ext)
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, "unsupported file type %r (allowed: %s)"
                            % (ext, ", ".join(sorted(ALLOWED_EXTS))))
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file too large (max %d MB)" % (MAX_UPLOAD // 1048576))
    import hashlib
    sha = hashlib.sha1(data).hexdigest()
    os.makedirs(REPO, exist_ok=True)
    path = os.path.join(REPO, "%s.%s" % (sha, ext))
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    w = h = None
    if ext in IMAGE_EXTS:
        try:
            from PIL import Image
            w, h = Image.open(io.BytesIO(data)).size
        except Exception:
            pass
    uc = _umedia_con()
    try:
        uc.execute("INSERT INTO user_media(norm_key,kind,sha1,ext,width,height,"
                   "origin,created) VALUES(?,?,?,?,?,?,?,?)",
                   (norm_key, kind, sha, ext, w, h, origin, time.time()))
        uc.commit()
    finally:
        uc.close()


@app.post("/api/games/{norm_key}/media/{kind}/upload")
async def upload_media(norm_key: str, kind: str, request: Request,
                       filename: str = Query("")):
    """Upload a media file from the device. The file is sent as the raw request
    body (no multipart); `filename` (or the Content-Type) sets the extension."""
    _ekey = norm_key                    # _store_upload splits to base internally
    data = await request.body()
    ext = _ext_from(filename, request.headers.get("content-type"))
    if not ext:
        raise HTTPException(400, "couldn't determine file type — include a filename")
    _store_upload(norm_key, kind, data, ext, "upload:" + (filename or ""))
    return game_media(_ekey)


@app.post("/api/games/{norm_key}/media/{kind}/url")
def add_media_from_url(norm_key: str, kind: str, body: dict = Body(...)):
    """Download media from a direct URL and store it as a user upload."""
    _ekey = norm_key
    norm_key = _split_entry_key(norm_key)[0]
    url = (body or {}).get("url", "").strip()
    if not url or not re.match(r"^https?://", url, re.I):
        raise HTTPException(400, "a valid http(s) URL is required")
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "ludodex/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ct = resp.headers.get("content-type")
            data = resp.read(MAX_UPLOAD + 1)
    except Exception as e:
        raise HTTPException(502, "couldn't fetch that URL: %s" % e)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "remote file too large (max %d MB)"
                            % (MAX_UPLOAD // 1048576))
    ext = _ext_from(url, ct)
    if not ext:
        raise HTTPException(400, "couldn't tell the media type from that URL")
    _store_upload(norm_key, kind, data, ext, url)
    return game_media(_ekey)


@app.delete("/api/games/{norm_key}/media/user/{asset_id}")
def delete_user_media(norm_key: str, asset_id: int):
    """Remove a user-uploaded asset (leaves shared repo bytes; they're content-addressed)."""
    _ekey = norm_key
    norm_key = _split_entry_key(norm_key)[0]
    uc = _umedia_con()
    try:
        uc.execute("DELETE FROM user_media WHERE id=? AND norm_key=?",
                   (asset_id, norm_key))
        uc.commit()
    finally:
        uc.close()
    return game_media(_ekey)


@app.get("/api/user-media-asset/{asset_id}")
def user_media_asset(asset_id: int, size: str = Query(None, pattern="^thumb$")):
    """Serve a user-uploaded asset by id."""
    uc = _umedia_con()
    try:
        r = uc.execute("SELECT sha1, ext FROM user_media WHERE id=?",
                       (asset_id,)).fetchone()
    finally:
        uc.close()
    if not r:
        raise HTTPException(404, "no such asset")
    p = os.path.join(REPO, "%s.%s" % (r["sha1"], r["ext"]))
    if not os.path.exists(p):
        raise HTTPException(404, "asset bytes missing")
    return _serve(p, r["ext"], size)


# ------------------------------------------------------------------- server ops

def _db_path(db_id):
    d = DB_BY_ID.get(db_id)
    if not d:
        raise HTTPException(404, "unknown database %r" % db_id)
    return os.path.join(DATA, d[2])


def _db_info(db_id, name, fname, role):
    path = os.path.join(DATA, fname)     # DBs live in the DATA volume, not the app dir
    exists = os.path.exists(path)
    return {"id": db_id, "name": name, "role": role, "path": fname,
            "exists": exists,
            "size": os.path.getsize(path) if exists else 0}


@app.get("/api/ops/status")
def ops_status():
    """Snapshot for the Server Operations panel: the running service + each database."""
    return {
        "services": [{
            "id": "server", "name": "Ludodex server (web + API)",
            "state": "running", "pid": os.getpid(),
            "uptime_seconds": int(time.time() - _STARTED),
            "host": "0.0.0.0", "port": 8001,
        }],
        "databases": [_db_info(*d) for d in DATABASES],
    }


@app.post("/api/ops/restart")
def ops_restart():
    """Restart the server in place (re-exec the exact launch command). The current
    HTTP response is sent first; the process then replaces itself, so the client
    should poll /api/health until it answers again."""
    def _reexec():
        time.sleep(0.8)
        try:
            with open("/proc/self/cmdline", "rb") as f:
                argv = [a for a in f.read().split(b"\0") if a]
            os.execv(argv[0], argv)          # same PID, inherits stdout/stderr fds
        except Exception:                    # pragma: no cover — last-ditch
            os._exit(3)                      # exit non-zero so a supervisor can restart
    threading.Thread(target=_reexec, daemon=True).start()
    return {"restarting": True, "pid": os.getpid()}


def _check_one(db_id, name, fname, role):
    info = _db_info(db_id, name, fname, role)
    if not info["exists"] or info["size"] == 0:
        info["status"] = "empty"
        info["detail"] = "no data" if info["exists"] else "missing"
        return info
    try:
        con = sqlite3.connect(_db_path(db_id), timeout=5)
        con.execute("PRAGMA busy_timeout=4000")
        qc = con.execute("PRAGMA quick_check").fetchone()[0]
        free = con.execute("PRAGMA freelist_count").fetchone()[0]
        page = con.execute("PRAGMA page_size").fetchone()[0]
        con.close()
        info["status"] = "ok" if qc == "ok" else "error"
        info["detail"] = "healthy" if qc == "ok" else qc
        info["reclaimable"] = free * page
    except sqlite3.Error as e:
        info["status"] = "error"
        info["detail"] = str(e)
    return info


@app.post("/api/ops/db-check")
def ops_db_check(body: dict = Body(default={})):
    """Run PRAGMA quick_check on one database (id) or all of them."""
    which = (body or {}).get("db", "all")
    dbs = DATABASES if which == "all" else [DB_BY_ID.get(which)]
    if dbs == [None]:
        raise HTTPException(404, "unknown database %r" % which)
    return {"results": [_check_one(*d) for d in dbs]}


@app.post("/api/ops/db-fix")
def ops_db_fix(body: dict = Body(...)):
    """Maintenance / repair on one database.
      optimize — PRAGMA optimize + REINDEX + VACUUM (safe; reclaims space).
      recover  — rebuild from a SQL dump into a fresh file (backs up the original
                 as <name>.bak first); for a database that fails its health check."""
    db_id = body.get("db")
    action = body.get("action")
    path = _db_path(db_id)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise HTTPException(400, "database is empty/missing")
    before = os.path.getsize(path)

    if action == "optimize":
        try:
            con = sqlite3.connect(path, timeout=10)
            con.execute("PRAGMA busy_timeout=8000")
            con.execute("PRAGMA optimize")
            con.execute("REINDEX")
            con.execute("VACUUM")
            con.commit(); con.close()
        except sqlite3.Error as e:
            raise HTTPException(409, "optimize failed: %s (is a scan running?)" % e)
        after = os.path.getsize(path)
        return {"db": db_id, "action": action, "ok": True,
                "before": before, "after": after, "reclaimed": before - after}

    if action == "recover":
        bak = path + ".bak"
        tmp = path + ".recovered"
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
            src = sqlite3.connect(path, timeout=10)
            dst = sqlite3.connect(tmp)
            with dst:
                dst.executescript("\n".join(src.iterdump()))
            src.close(); dst.close()
            # keep the original as .bak, swap the rebuilt file in
            if os.path.exists(bak):
                os.remove(bak)
            os.rename(path, bak)
            os.rename(tmp, path)
        except sqlite3.Error as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise HTTPException(409, "recover failed: %s" % e)
        return {"db": db_id, "action": action, "ok": True,
                "before": before, "after": os.path.getsize(path),
                "backup": os.path.basename(bak)}

    raise HTTPException(400, "unknown action %r (optimize | recover)" % action)


# --- whole-fleet maintenance: optimize / backup / restore across all data DBs. The panel
# surfaces these instead of a per-file inventory (users want maintenance, not a table list). ---
BACKUP_DIR = os.path.join(DATA, "backups")


def _all_db_files():
    """Every *.sqlite in the data volume (robust to the registry — backs up/optimizes
    what actually exists, including auth.sqlite and any future store)."""
    try:
        return sorted(f for f in os.listdir(DATA)
                      if f.endswith(".sqlite") and os.path.isfile(os.path.join(DATA, f)))
    except OSError:
        return []


@app.post("/api/ops/optimize")
def ops_optimize():
    """Optimize EVERY database (PRAGMA optimize + REINDEX + VACUUM) — reclaims space and
    rebuilds indexes across the whole data volume in one action."""
    reclaimed = ok = 0
    errors = []
    for fname in _all_db_files():
        path = os.path.join(DATA, fname)
        before = os.path.getsize(path)
        try:
            con = sqlite3.connect(path, timeout=10)
            con.execute("PRAGMA busy_timeout=8000")
            con.execute("PRAGMA optimize")
            con.execute("REINDEX")
            con.execute("VACUUM")
            con.commit()
            con.close()
            reclaimed += max(0, before - os.path.getsize(path))
            ok += 1
        except sqlite3.Error as e:
            errors.append("%s: %s" % (fname, str(e)[:80]))
    return {"ok": True, "optimized": ok, "reclaimed": reclaimed, "errors": errors}


@app.post("/api/ops/backup")
def ops_backup():
    """Snapshot every database into data/backups/<timestamp>/ using SQLite's online backup
    (consistent even while the server is running). Returns the new backup's id."""
    bid = time.strftime("%Y-%m-%d_%H%M%S", time.localtime())
    dest = os.path.join(BACKUP_DIR, bid)
    os.makedirs(dest, exist_ok=True)
    n = size = 0
    for fname in _all_db_files():
        src, dst = os.path.join(DATA, fname), os.path.join(dest, fname)
        try:
            s = sqlite3.connect(src, timeout=10)
            d = sqlite3.connect(dst)
            with d:
                s.backup(d)
            s.close()
            d.close()
        except sqlite3.Error:
            try:
                shutil.copy2(src, dst)          # fallback: plain copy
            except OSError:
                continue
        n += 1
        size += os.path.getsize(dst)
    return {"ok": True, "id": bid, "count": n, "size": size}


@app.get("/api/ops/backups")
def ops_backups():
    """List available backups (newest first): id, db count, total size."""
    out = []
    if os.path.isdir(BACKUP_DIR):
        for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
            p = os.path.join(BACKUP_DIR, name)
            if not os.path.isdir(p):
                continue
            files = [f for f in os.listdir(p) if f.endswith(".sqlite")]
            out.append({"id": name, "count": len(files),
                        "size": sum(os.path.getsize(os.path.join(p, f)) for f in files)})
    return {"backups": out}


@app.post("/api/ops/restore")
def ops_restore(body: dict = Body(...)):
    """Restore a backup over the live databases. Safety-snapshots the CURRENT state first,
    then copies the backup's files back in. A restart is required afterward so every open
    connection reopens the restored files — the client should call /api/ops/restart."""
    bid = (body or {}).get("id")
    src_dir = os.path.join(BACKUP_DIR, bid or "")
    if not bid or os.path.basename(bid) != bid or not os.path.isdir(src_dir):
        raise HTTPException(404, "unknown backup %r" % bid)
    safety = ops_backup()["id"]                 # never restore without a way back
    restored = 0
    for f in os.listdir(src_dir):
        if not f.endswith(".sqlite"):
            continue
        try:
            shutil.copy2(os.path.join(src_dir, f), os.path.join(DATA, f))
            restored += 1
        except OSError:
            pass
    return {"ok": True, "restored": restored, "safety_backup": safety,
            "restart_required": True}


# A games entry id is `base_key@platform` (per-platform library entry, DESIGN §11).
# Split on the LAST '@' — a base norm_key never contains '@', a platform never does.
# A bare key (no '@') is treated as a base norm_key with no platform preference, so
# old callers / exporters keep working.
def _split_entry_key(key):
    if "@" in key:
        b, p = key.rsplit("@", 1)
        return b, p
    return key, None


def _resolve_entry(con, key):
    """Resolve a URL key to (games row, base norm_key, platform). Accepts an entry_key
    `base@platform` or a bare base norm_key (legacy → the first/only entry for it).
    Tolerant of an un-rebuilt catalog with no entry_key column."""
    base, platform = _split_entry_key(key)
    if _has_col(con, "games", "entry_key"):
        row = con.execute("SELECT * FROM games WHERE entry_key=?", (key,)).fetchone()
        if row:
            return row, row["norm_key"], row["platform"]
        row = con.execute("SELECT * FROM games WHERE norm_key=? LIMIT 1",
                          (base,)).fetchone()
        return row, base, (row["platform"] if row else platform)
    row = con.execute("SELECT * FROM games WHERE norm_key=? LIMIT 1", (key,)).fetchone()
    return row, key, None


# ------------------------------------------------------------------- media resolver
def _render_pdf_thumb(src, dst, px=400):
    """Render page 1 of a PDF to a JPEG thumbnail at `dst` (PyMuPDF, no system deps)
    — so manuals preview their first page instead of showing a bare filename."""
    import fitz  # PyMuPDF
    from PIL import Image
    doc = fitz.open(src)
    try:
        pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        im.thumbnail((px, px))
        im.save(dst, "JPEG", quality=82)
    finally:
        doc.close()


def _serve(path, ext, size):
    """Return a FileResponse, optionally downscaled to a cached thumbnail. PDFs get
    a rendered first-page image so manuals preview instead of showing a bare name."""
    if size == "thumb":
        sha = os.path.splitext(os.path.basename(path))[0]
        is_pdf = ext.lower() == "pdf"
        tpath = os.path.join(THUMBS, "%s_thumb.%s" % (sha, "jpg" if is_pdf else ext))
        if not os.path.exists(tpath):
            try:
                if is_pdf:
                    _render_pdf_thumb(path, tpath)
                else:
                    from PIL import Image
                    im = Image.open(path)
                    im.thumbnail((400, 400))
                    buf = io.BytesIO()
                    fmt = "PNG" if ext.lower() == "png" else "JPEG"
                    if fmt == "JPEG" and im.mode in ("RGBA", "P"):
                        im = im.convert("RGB")
                    im.save(buf, fmt)
                    with open(tpath, "wb") as f:
                        f.write(buf.getvalue())
            except Exception:
                return FileResponse(path)        # fall back to full size
        path = tpath
    return FileResponse(path)


@app.get("/api/media/{norm_key}/{kind}")
def media_asset(norm_key: str, kind: str, size: str = Query(None, pattern="^thumb$")):
    """Resolve + stream the chosen asset for a library entry + kind.

    `norm_key` may be an entry id `base@platform` (per-platform library entry) or a
    bare base norm_key. Media is siloed by system: an entry serves its own console's
    chosen art, falling back to platform-neutral store art (system NULL) — so a
    TurboGrafx entry never shows a Game Boy cover (DESIGN §11.4).

    1. chosen row with sha1 + repo file present     -> stream it.
    2. chosen URL ref not yet materialized          -> fetch, cache, backfill, stream.
    3. chosen `file` ref (lives only on the producer)-> 404 (push it from the Deck).

    A user upload for this kind always wins (most recent), so uploads take effect
    immediately without a pipeline re-run.
    """
    base, platform = _split_entry_key(norm_key)
    up = _umedia_path(base, kind)
    if up:
        return _serve(up[0], up[1], size)
    rcon = ro(INDEX_DB)
    try:
        if platform:
            # Serve this entry's OWN console art (system=platform), or platform-neutral
            # store/IGDB art whose identity matches the entry (media.game_key = the entry's
            # game_key, DESIGN §11.9) — preferring own-console. An era-collision entry
            # (game_key title:<nk>) thus never borrows the resolved game's igdb:<id> neutral
            # cover (Portal/Amiga vs Valve's Portal), a stray port (adopts igdb:<id>) keeps
            # it, and an unidentified game shows its own title:<nk> neutral art. game_key
            # lives in the catalog db, so a tiny ro lookup carries it to the media query.
            gkey = None
            try:
                lcon = ro(LIBRARY_DB)
                try:
                    row = lcon.execute(
                        "SELECT game_key FROM games WHERE norm_key=? AND platform=? "
                        "LIMIT 1", (base, platform)).fetchone()
                    gkey = row[0] if row else None
                finally:
                    lcon.close()
            except sqlite3.OperationalError:
                gkey = None         # pre-rebuild catalog without game_key column
            if gkey:
                # own-console art is strictly this norm_key + system; neutral (store/IGDB)
                # art is matched by game IDENTITY (game_key) across norm_keys, so a game
                # whose title parsed into two norm_keys (Intl Karate "+"/"plus") still serves
                # its one fetched cover. Own-console preferred via the ORDER BY.
                r = rcon.execute(
                    "SELECT id, ref_type, ref, ext, sha1, provider FROM media "
                    "WHERE kind=? AND chosen=1 AND ("
                    "(norm_key=? AND COALESCE(system,'')=?) "
                    "OR (COALESCE(system,'')='' AND game_key=?)) "
                    "ORDER BY (norm_key=? AND COALESCE(system,'')=?) DESC LIMIT 1",
                    (kind, base, platform, gkey, base, platform)).fetchone()
            else:
                # pre-migration fallback: own console art, or any platform-neutral art.
                r = rcon.execute(
                    "SELECT id, ref_type, ref, ext, sha1, provider FROM media "
                    "WHERE norm_key=? AND kind=? AND chosen=1 "
                    "AND (COALESCE(system,'')=? OR COALESCE(system,'')='') "
                    "ORDER BY (COALESCE(system,'')=?) DESC LIMIT 1",
                    (base, kind, platform, platform)).fetchone()
        else:
            # bare norm_key (legacy callers / exporters): no platform context
            r = rcon.execute(
                "SELECT id, ref_type, ref, ext, sha1, provider FROM media "
                "WHERE norm_key=? AND kind=? AND chosen=1 LIMIT 1",
                (base, kind)).fetchone()
    finally:
        rcon.close()
    if not r:
        raise HTTPException(404, "no chosen %s for %s" % (kind, norm_key))

    ext = (r["ext"] or "jpg").split("?")[0]

    # 1. already materialized in the repo
    if r["sha1"]:
        p = os.path.join(REPO, "%s.%s" % (r["sha1"], ext))
        if os.path.exists(p):
            return _serve(p, ext, size)

    # local file present on THIS host (rare on the VM; common on the producer)
    if r["ref_type"] == "file" and os.path.exists(r["ref"]):
        return _serve(r["ref"], ext, size)

    # 2. remote URL -> materialize on serve (fetch, cache, backfill sha1)
    if r["ref_type"] == "url":
        sha = media_choose._materialize_row(REPO, r)
        if sha:
            wcon = sqlite3.connect(INDEX_DB)          # write-back the backfill
            try:
                wcon.execute("UPDATE media SET sha1=? WHERE id=?", (sha, r["id"]))
                wcon.commit()
            finally:
                wcon.close()
            p = os.path.join(REPO, "%s.%s" % (sha, ext))
            if os.path.exists(p):
                return _serve(p, ext, size)
        raise HTTPException(502, "failed to fetch remote asset")

    # 3. file ref that only exists on the producer
    raise HTTPException(
        404, "asset not materialized on this host (lives on the producer)")


# ------------------------------------------------------------------------- ai search
@app.post("/api/search")
def ai_search(body: dict):
    """Natural-language search: Claude turns the question into a structured query
    (server/ai.py), which we then run deterministically. 503 if no API key."""
    question = (body or {}).get("q", "").strip()
    if not question:
        raise HTTPException(400, "missing q")
    if not ai.area_available("search"):
        raise HTTPException(503, "AI search not configured (set a provider + API key)")
    provider = ai.provider_for_area("search")
    model = ai.model_for_area("search")
    con = lib()
    try:
        f = facets()
        try:
            query, explanation = ai.nl_to_query(
                question, f["sources"], f["platforms"],
                provider=provider, model=model)
        except Exception as e:
            raise HTTPException(502, "AI error: %s" % e)
        result = _query_games(con, **query, limit=120)
        return {"query": query, "explanation": explanation, "result": result}
    finally:
        con.close()


@app.get("/api/ai/config")
def ai_config():
    """Non-secret AI provider config for the settings UI (keys never returned)."""
    return ai.status()


@app.post("/api/ai/config")
def ai_config_set(body: dict):
    """Set the active provider, per-provider API keys, and/or model overrides.

    Body: {"provider": "openai", "keys": {"openai_api_key": "sk-..."},
           "models": {"openai_model": "gpt-5-mini"}}
    Keys/models are written to config.sqlite (gitignored). A key of "" clears it.
    """
    body = body or {}
    provider = body.get("provider")
    if provider is not None:
        if provider not in ai.PROVIDERS:
            raise HTTPException(400, "unknown provider %r" % provider)
        config.set_("ai_provider", provider)
    # global image-analysis (vision) default: {"vision": {"provider","model"}} —
    # provider/model are set independently so one can change without clearing the other.
    vis = body.get("vision")
    if isinstance(vis, dict):
        if "provider" in vis:
            vp = vis.get("provider") or ""
            if vp and vp not in ai.PROVIDERS:
                raise HTTPException(400, "unknown provider %r" % vp)
            config.set_("ai_vision_provider", vp)
        if "model" in vis:
            config.set_("ai_vision_model", vis.get("model") or "")
    valid_keys = {cfg for (_, cfg, _, _) in ai.PROVIDERS.values()}
    valid_models = {m for (_, _, _, m) in ai.PROVIDERS.values()}
    for k, v in (body.get("keys") or {}).items():
        if k not in valid_keys:
            raise HTTPException(400, "unknown key field %r" % k)
        config.set_(k, v or "")
    for k, v in (body.get("models") or {}).items():
        if k not in valid_models:
            raise HTTPException(400, "unknown model field %r" % k)
        config.set_(k, v or "")
    for area_id, val in (body.get("areas") or {}).items():
        if area_id not in ai.AREA_IDS:
            raise HTTPException(400, "unknown area %r" % area_id)
        if not isinstance(val, dict):
            val = {"provider": val or ""}
        # each of provider/model/prompt is set only when present (so editing the
        # prompt doesn't clobber the provider, and vice-versa). "" clears/resets.
        if "provider" in val:
            prov = val.get("provider") or ""
            if prov and prov not in ai.PROVIDERS:
                raise HTTPException(400, "unknown provider %r" % prov)
            config.set_("ai_area_" + area_id, prov)
        if "model" in val:
            config.set_("ai_area_" + area_id + "_model", val.get("model") or "")
        if "escalation_model" in val:
            config.set_("ai_area_" + area_id + "_escalation_model",
                        val.get("escalation_model") or "")
        if "prompt" in val:
            config.set_("ai_area_" + area_id + "_prompt", val.get("prompt") or "")
    return ai.status()


@app.get("/api/ai/usage")
def ai_usage():
    """Per provider/model token usage (lifetime + this month) + configured caps."""
    return ai.usage_summary()


@app.get("/api/ai/usage/series")
def ai_usage_series(provider: str = Query(...), model: str = Query(...)):
    """Daily token usage for a model over the last 31 days (today + 30 preceding)."""
    return {"provider": provider, "model": model,
            "days": ai.usage_series(provider, model)}


@app.get("/api/ai/limits")
def ai_limits():
    """The list of configured monthly caps (empty by default), each with this
    month's usage. Independent of what's been used — caps can name any model."""
    return {"caps": ai.limits_list()}


@app.post("/api/ai/limit")
def ai_limit(body: dict = Body(...)):
    """Set (or clear) the caps for a provider or a model. Body:
    {"scope":"provider"|"model", "key":"<id>", "caps":{total,usd,input,output}}.
    Any subset of caps; all falsy = the row is removed. `usd` is stored in USD
    (the UI converts from the display currency). `key` may be ANY provider/model."""
    body = body or {}
    scope = (body.get("scope") or "").strip()
    key = (body.get("key") or "").strip()
    if scope not in ("provider", "model", "global") or not key:
        raise HTTPException(400, "scope must be global|provider|model and key required")
    caps = body.get("caps")
    if not isinstance(caps, dict):               # back-compat: {monthly_tokens: N}
        caps = {"total": body.get("monthly_tokens")}
    try:
        ai.set_limit(scope, key, caps)
    except (TypeError, ValueError):
        raise HTTPException(400, "cap values must be numbers")
    return {"caps": ai.limits_list(), "usage": ai.usage_summary()}


@app.get("/api/ai/prices")
def ai_prices():
    """The editable per-model price table (USD per 1M tokens) + display currency +
    the OpenRouter source toggle + the daily auto-refresh schedule."""
    return {"prices": ai.prices_list(), "currency": ai.currency_get(),
            "openrouter": ai.prices_openrouter_enabled(),
            "schedule": ai.price_schedule_get(), "last_update": ai.price_update_last()}


@app.post("/api/ai/prices/source")
def ai_prices_source(body: dict = Body(...)):
    """Toggle the optional OpenRouter price fetch. Body: {openrouter: bool}."""
    on = bool((body or {}).get("openrouter"))
    return {"openrouter": ai.prices_openrouter_set(on)}


@app.post("/api/ai/prices/schedule")
def ai_prices_schedule(body: dict = Body(...)):
    """Set the daily price auto-refresh. Body: {daily: bool, time: 'HH:MM'} (install
    timezone). Runs only when the OpenRouter source is on and limits exist."""
    b = body or {}
    try:
        return {"schedule": ai.price_schedule_set(b.get("daily"), b.get("time"))}
    except ValueError as e:
        raise HTTPException(400, str(e))


def _price_scheduler():
    """Fire the daily price auto-refresh once per day at the configured local time.
    Polls every 5 min (uses `time.localtime`, so it honors the install's TZ) so
    setting changes take effect and a restart catches up the same day."""
    while True:
        try:
            sched = ai.price_schedule_get()
            if sched.get("daily"):
                lt = time.localtime()
                hh, mm = (int(x) for x in sched["time"].split(":"))
                today = time.strftime("%Y-%m-%d", lt)
                if ai.price_update_last() != today and (lt.tm_hour, lt.tm_min) >= (hh, mm):
                    try:
                        ai.run_daily_price_update()
                    finally:
                        config.set_("price_update_last", today)
        except Exception:                        # noqa: BLE001 — never kill the loop
            pass
        time.sleep(300)


threading.Thread(target=_price_scheduler, daemon=True).start()


@app.post("/api/ai/price")
def ai_price_set(body: dict = Body(...)):
    """Set a model's price (USD/1M). Body: {provider, model, in_usd, out_usd, cached_usd?}."""
    b = body or {}
    if not (b.get("provider") and b.get("model")):
        raise HTTPException(400, "provider and model required")
    try:
        ai.price_set(b["provider"], b["model"], b.get("in_usd"), b.get("out_usd"),
                     b.get("cached_usd"), source="manual")
    except (TypeError, ValueError):
        raise HTTPException(400, "prices must be numbers")
    return {"prices": ai.prices_list()}


@app.post("/api/ai/prices/refresh")
def ai_prices_refresh():
    """Pull current per-token pricing from OpenRouter (never overwrites manual rows).
    Opt-in: only works when the OpenRouter source toggle is on."""
    if not ai.prices_openrouter_enabled():
        raise HTTPException(400, "OpenRouter price fetching is off — enable it in "
                                 "Settings › AI › Budgets & limits first")
    try:
        res = ai.prices_refresh()
    except Exception as e:
        raise HTTPException(502, "price refresh failed: %s" % e)
    return {**res, "prices": ai.prices_list()}


@app.post("/api/ai/prices/resolve")
def ai_prices_resolve(body: dict = Body(default={})):
    """Auto-resolve model prices. Body: {use_ai: bool, note: str}. First pulls the
    OpenRouter feed (when enabled); with use_ai, asks the configured 'prices' AI
    area to price any model the feed still couldn't (renamed/deprecated/new),
    weaving the user's `note` into the area prompt. Returns counts + fresh prices."""
    body = body or {}
    use_ai = bool(body.get("use_ai"))
    note = str(body.get("note") or "")
    fetched, fetch_err = 0, None
    if ai.prices_openrouter_enabled():
        try:
            fetched = ai.prices_refresh().get("updated", 0)
        except Exception as e:                  # noqa: BLE001 — feed is best-effort
            fetch_err = str(e)[:140]
    ai_set, ai_err, targeted = 0, None, 0
    if use_ai:
        # Always resolve models with no price; plus any model/provider the user
        # named in the note (so a specific "X is wrong" gets re-priced even if it
        # already has a default).
        targets = list(ai.prices_missing())
        seen = set(targets)
        nlow = note.lower()
        if nlow.strip():
            for r in ai.prices_list():
                pm = (r["provider"], r["model"])
                if pm not in seen and ((r["model"] and r["model"].lower() in nlow)
                                       or (r["provider"] and r["provider"].lower() in nlow)):
                    targets.append(pm); seen.add(pm)
        targeted = len(targets)
        if targets:
            try:
                for r in ai.resolve_prices_ai(targets, note=note):
                    prov = r.get("provider") or ""
                    if not prov or not r.get("model"):
                        continue
                    ai.price_set(prov, r["model"], r["input"], r["output"],
                                 r.get("cached"), source="ai")
                    ai_set += 1
            except Exception as e:
                ai_err = str(e)[:200]
    return {"prices": ai.prices_list(), "fetched": fetched, "ai_resolved": ai_set,
            "targeted": targeted, "still_missing": len(ai.prices_missing()),
            "fetch_error": fetch_err, "ai_error": ai_err}


@app.post("/api/ai/currency")
def ai_currency(body: dict = Body(...)):
    """Set the display currency for budgets. Body: {code, fx} — fx = units per USD
    (ignored/forced to 1 for USD). Budgets stay stored in USD."""
    b = body or {}
    try:
        return {"currency": ai.currency_set(b.get("code"), b.get("fx"))}
    except (TypeError, ValueError):
        raise HTTPException(400, "fx must be a number")


@app.get("/api/ai/models/{provider}")
def ai_models(provider: str, refresh: bool = False, vision: bool = False):
    """All models the provider's API reports (cached; curated fallback).
    vision=true → only image-capable models (for the image-analysis default/areas)."""
    if provider not in ai.PROVIDERS:
        raise HTTPException(400, "unknown provider %r" % provider)
    return {"provider": provider,
            "models": ai.list_models(provider, refresh=refresh, vision_only=vision)}


# ----------------------------------------------------------- service credentials
# Credentials the pipeline reads (config.py resolves env > local config value only;
# this UI writes the config value). role: source=ownership, provider=enrichment.
def _limits(prefix, cooldown="", per_min="", per_day=""):
    """Uniform rate-limit fields for a service (config key <prefix>_limit_<f>).
    Blank default = unlimited / unset."""
    return [
        {"key": prefix + "_limit_cooldown_ms", "label": "Cooldown between requests",
         "unit": "ms", "default": cooldown},
        {"key": prefix + "_limit_per_min", "label": "Max requests / minute",
         "unit": "req", "default": per_min},
        {"key": prefix + "_limit_per_day", "label": "Max requests / day",
         "unit": "req", "default": per_day},
    ]


SERVICES = [
    {"id": "steam", "name": "Steam", "role": "both",
     "hint": "steamcommunity.com/dev/apikey",
     "creds": [
         {"key": "steam_api_key", "label": "Web API key", "secret": True},
         {"key": "steam_id", "label": "Steam ID (64-bit)", "secret": False}],
     "limits": _limits("steam", cooldown="200", per_day="100000")},
    {"id": "gog", "name": "GOG", "role": "source",
     "hint": "Connect your GOG account: click Get GOG code (opens GOG — sign in if "
             "asked). It lands on a blank/‘success’ page — the code is in the browser "
             "ADDRESS BAR, not on the page. Copy that whole address (or just the code "
             "after code=) and paste it below, then Connect. One-time login.",
     "creds": [],
     "connect": {"url": GOG_LOGIN_URL,
                 "action_label": "Get GOG code",
                 "field_label": "Paste the GOG address bar URL (or code)",
                 "note": "After signing in the page looks blank — the code is in the "
                         "browser’s address bar. Paste that whole URL here; we read it "
                         "either way. If it won't connect, paste JUST the code value "
                         "(the part after code= in the address bar).",
                 "post": "/api/services/gog/code"},
     "limits": _limits("gog", cooldown="300")},
    {"id": "itch", "name": "itch.io", "role": "source",
     "hint": "Lists the games you own on itch.io. Sign in at itch.io, generate a "
             "personal key at itch.io/user/settings/api-keys, and paste it below — "
             "that key alone is all ludodex needs (no separate login).",
     "creds": [{"key": "itch_api_key", "label": "API key", "secret": True}],
     "limits": _limits("itch", cooldown="300")},
    {"id": "epic", "name": "Epic Games", "role": "source",
     "hint": "Connect your Epic account: click Get Epic code (opens Epic — sign in "
             "if asked), copy the text it shows, paste it below, and Connect. "
             "This is a one-time login — Epic keeps you signed in after that.",
     "creds": [],
     "connect": {"url": EPIC_LOGIN_URL,
                 "action_label": "Get Epic code", "field_label": "Paste Epic code",
                 "note": "We read whatever you paste — the whole JSON, one line, or "
                         "the redirect URL. If it won't connect, paste JUST the "
                         "authorizationCode value (the long code between the quotes).",
                 "post": "/api/services/epic/code"},
     "limits": _limits("epic")},
    {"id": "ea", "name": "EA app / Origin", "role": "source",
     "hint": "Connect your EA account: click Get EA token (opens EA — sign in if "
             "asked), copy the text it shows, paste it below, and Connect. "
             "The connection lasts ~4 hours; reconnect the same way to refresh.",
     "creds": [],
     "connect": {"url": ("https://accounts.ea.com/connect/auth?client_id=ORIGIN_JS_SDK"
                         "&response_type=token&redirect_uri=nucleus:rest&prompt=none"),
                 "action_label": "Get EA token", "field_label": "Paste EA token",
                 "note": "We read whatever you paste — the whole JSON or one line. If "
                         "it won't connect, paste JUST the access_token value (the "
                         "long token between the quotes).",
                 "post": "/api/services/ea/token"},
     "limits": _limits("ea")},
    {"id": "psn", "name": "PlayStation Network", "role": "source",
     "hint": "Connect your PSN account: sign in at playstation.com, then click Get "
             "PSN token (opens the ssocookie page) and copy the npsso value it "
             "shows. Paste it below and Connect. One-time login — it refreshes for "
             "~2 months. The token is only on that page, not the main site.",
     "creds": [],
     "connect": {"url": "https://ca.account.sony.com/api/v1/ssocookie",
                 "action_label": "Get PSN token",
                 "field_label": "Paste PSN npsso (or the whole {\"npsso\":…})",
                 "note": "You must be signed in at playstation.com first, in the "
                         "same browser. The Get PSN token link then shows "
                         "{\"npsso\":\"…\"} — we read the whole thing, one line, or "
                         "the bare token. If it won't connect, paste JUST the npsso "
                         "value (the ~64 characters between the quotes).",
                 "post": "/api/services/psn/npsso"},
     "limits": _limits("psn", cooldown="500")},
    {"id": "xbox", "name": "Xbox / Microsoft Store", "role": "source",
     "hint": "Connect your Microsoft account with a short code — no address-bar "
             "copying. Click Connect Xbox, then at microsoft.com/link enter the code "
             "ludodex shows and approve. ludodex finishes on its own. One-time "
             "login — it refreshes automatically. Pulls games from your Xbox library.",
     "creds": [],
     "connect": {"mode": "device",
                 "url": "https://www.microsoft.com/link",
                 "action_label": "Connect Xbox",
                 "start": "/api/services/xbox/device/start",
                 "poll": "/api/services/xbox/device/poll",
                 "note": "Click Connect Xbox — ludodex shows a short code. Enter it at "
                         "microsoft.com/link (opens automatically), sign in, and "
                         "approve. Nothing to copy from the address bar; ludodex picks "
                         "it up the moment you approve. The code is good for 15 minutes.",
                 "post": "/api/services/xbox/code"},
     "limits": _limits("xbox", cooldown="500")},
    # Nintendo Account source REMOVED (2026-07-15): Nintendo exposes no owned-games
    # API a server can read (purchase history is console-locked), so the connector
    # never worked reliably. Switch ownership now comes via manual per-platform
    # ownership. NB the Nintendo *console* platform maps (switch/gameboy/snes/… in
    # console_eras/media/launchbox/igdb) stay — they classify emulation ROMs.
    {"id": "igdb", "name": "IGDB", "role": "provider",
     "hint": "IGDB authenticates via Twitch (≈4 req/sec). Create a free app to get a "
             "Client ID + Secret (OAuth Redirect URL can be http://localhost).",
     "doc": {"url": "https://dev.twitch.tv/console/apps",
             "label": "Open Twitch dev console →"},
     "creds": [
         {"key": "igdb_client_id", "label": "Twitch Client ID", "secret": False},
         {"key": "igdb_client_secret", "label": "Twitch Client Secret", "secret": True}],
     "limits": _limits("igdb", cooldown="250", per_min="240")},
    {"id": "screenscraper", "name": "ScreenScraper", "role": "provider",
     "hint": "screenscraper.fr — the app authenticates automatically. Optionally add "
             "YOUR screenscraper.fr account below to raise the daily request quota.",
     "creds": [
         {"key": "screenscraper_ssid", "label": "Your account user (optional)", "secret": False},
         {"key": "screenscraper_sspassword", "label": "Your account password (optional)", "secret": True}],
     "limits": _limits("screenscraper", cooldown="1000")},
    {"id": "steamgriddb", "name": "SteamGridDB", "role": "provider",
     "hint": "steamgriddb.com/profile/preferences/api",
     "creds": [{"key": "steamgriddb_api_key", "label": "API key", "secret": True}],
     "limits": _limits("steamgriddb", cooldown="200")},
    {"id": "google_images", "name": "Google image search", "role": "provider",
     "hint": "Optional last-resort cover finder for the wand's 'Find media + search web'. "
             "Create a Google API key (console.cloud.google.com — enable the Custom Search "
             "API) and a Programmable Search Engine that searches the whole web "
             "(programmablesearchengine.google.com) — paste its key + Search engine ID. "
             "Results are AI-picked and validated; art is for your own private catalog.",
     "creds": [{"key": "google_cse_key", "label": "API key", "secret": True},
               {"key": "google_cse_cx", "label": "Search engine ID (cx)", "secret": False}],
     "limits": _limits("google_images", cooldown="500")},
    {"id": "steamspy", "name": "SteamSpy", "role": "provider",
     "hint": "steamspy.com — Steam community tags for your owned Steam games. "
             "No account or API key needed (SteamSpy's API is public). Rate-limited "
             "to ~1 request/second, so a full pass runs in the background.",
     "creds": [],
     "limits": _limits("steamspy", cooldown="1100")},
    {"id": "retroachievements", "name": "RetroAchievements", "role": "provider",
     "hint": "retroachievements.org — Settings › Keys. Rate-limited; keep it gentle.",
     "creds": [
         {"key": "retroachievements_username", "label": "Username", "secret": False},
         {"key": "retroachievements_api_key", "label": "Web API key", "secret": True}],
     "limits": _limits("retroachievements", cooldown="400", per_min="60")},
]
ALL_SETTABLE_KEYS = {f["key"] for s in SERVICES
                     for f in (s["creds"] + s["limits"])}


def _svc_state(s):
    creds = []
    for f in s["creds"]:
        v = config.get(f["key"]) or ""
        creds.append({"key": f["key"], "label": f["label"], "secret": f["secret"],
                      "configured": bool(v),
                      "value": (ai._mask(v) if f["secret"] else v) if v else ""})
    limits = []
    for f in s["limits"]:
        limits.append({"key": f["key"], "label": f["label"], "unit": f["unit"],
                       "default": f["default"], "value": config.get(f["key"]) or ""})
    out = {"id": s["id"], "name": s["name"], "role": s["role"], "hint": s["hint"],
           "fields": creds, "limits": limits}
    if s.get("doc"):
        out["doc"] = s["doc"]                    # {url, label} — a "how to get creds" link
    if s["role"] in ("source", "both"):
        out["enabled"] = config.source_enabled(s["id"])
    if s.get("connect"):
        checker = {"ea": _ea_connected, "epic": _epic_connected,
                   "psn": _psn_connected, "xbox": _xbox_connected}.get(s["id"])
        out["connect"] = dict(s["connect"], connected=bool(checker and checker()))
    return out


def _ea_connected():
    """(bool) True if a valid cached EA browser token exists."""
    tokf = os.path.join(DATA, ".ea", "token.json")
    if not os.path.exists(tokf):
        return False
    try:
        import json as _json
        t = _json.load(open(tokf))
        return bool(t.get("access_token")) and t.get("expires_at", 0) > time.time()
    except Exception:
        return False


@app.get("/api/services")
def services_config():
    """Per-service credentials + rate-limit settings (secrets returned masked)."""
    return {"services": [_svc_state(s) for s in SERVICES]}


@app.post("/api/services")
def services_set(body: dict):
    """Set service credential / limit values in config.sqlite (""=clear)."""
    for k, v in ((body or {}).get("values") or {}).items():
        if k not in ALL_SETTABLE_KEYS:
            raise HTTPException(400, "unknown service setting %r" % k)
        config.set_(k, (v or "").strip())
    return services_config()


def _extract_token(raw, keys):
    """Pull an auth value out of whatever the user pasted. Tolerates three shapes:
    the whole JSON blob ({"access_token": "..."}), a `key=value` / `key: value`
    pair (as copied from devtools), or the bare value on its own. `keys` lists the
    field names to look for, in priority order."""
    import json as _json
    raw = (raw or "").strip()
    if not raw:
        return ""
    # 1. Full JSON object
    try:
        obj = _json.loads(raw)
        if isinstance(obj, dict):
            for k in keys:
                if obj.get(k):
                    return str(obj[k]).strip()
    except (ValueError, TypeError):
        pass
    # 2. `key: value`, `key=value`, or `key value` (quotes/commas tolerated)
    for k in keys:
        m = re.search(r'["\']?%s["\']?\s*[:=\s]\s*["\']?([^"\',\s}&]+)' % re.escape(k),
                      raw, re.I)
        if m:
            return m.group(1).strip()
    # 3. Bare value — drop any stray wrapping quotes
    return raw.strip().strip('"\'')


_SOURCE_IDS = {s["id"] for s in SERVICES if s["role"] in ("source", "both")}


@app.post("/api/services/{sid}/enabled")
def service_set_enabled(sid: str, body: dict = Body(...)):
    """Turn a source on/off for syncing (persists source_<id>_enabled)."""
    if sid not in _SOURCE_IDS:
        raise HTTPException(404, "no such source %r" % sid)
    config.set_("source_%s_enabled" % sid, "1" if (body or {}).get("enabled") else "0")
    return {"id": sid, "enabled": config.source_enabled(sid)}


@app.post("/api/services/ea/token")
def ea_connect(body: dict = Body(...)):
    """Accept whatever the user copies from EA's auth URL — the full JSON, an
    `access_token=…` pair, or the bare token — cache it (.ea/token.json, ~4h),
    and verify by fetching the EA display name."""
    raw = (body or {}).get("value", "")
    if "login_required" in raw or "102100" in raw:
        return {"ok": False, "account": None,
                "error": "You're not signed in to EA in that browser — EA returned a "
                         "'login_required' error instead of a token. Sign in at ea.com "
                         "first, then click Get EA token and paste the result."}
    tok = _extract_token(raw, ["access_token"])
    if not tok:
        raise HTTPException(400, "no access token found in what you pasted")
    import ea_owned
    ea_owned.save_token(tok)
    try:
        player, _ = ea_owned.whoami({}, tok)          # verify against EA
        return {"ok": True, "account": player.get("displayName")}
    except Exception as e:
        return {"ok": False, "account": None,
                "error": "That token didn't work — sign into EA in the browser, get a "
                         "fresh token, and paste it. (%s)" % str(e)[:100]}


def _epic_connected():
    """(bool) True if legendary has a cached Epic login (user.json w/ a name)."""
    uf = os.path.expanduser("~/.config/legendary/user.json")
    if not os.path.exists(uf):
        return False
    try:
        import json as _json
        return bool(_json.load(open(uf)).get("displayName"))
    except Exception:
        return False


@app.post("/api/services/epic/code")
def epic_connect(body: dict = Body(...)):
    """Accept whatever the user copies from Epic's redirect page — the full JSON,
    an `authorizationCode=…` pair, or the bare code — and hand it to legendary,
    which exchanges it for a refresh token that auto-renews from then on."""
    import subprocess
    code = _extract_token((body or {}).get("value", ""),
                          ["authorizationCode", "code"])
    if not code:
        raise HTTPException(400, "no authorization code found in what you pasted")
    try:
        r = subprocess.run([LEGENDARY, "auth", "--code", code],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "account": None, "error": "Couldn't reach Epic: %s" % e}
    if r.returncode != 0 or not _epic_connected():
        return {"ok": False, "account": None,
                "error": "That code didn't work — codes are single-use, so open "
                         "Get Epic code again for a fresh one and paste it."}
    try:
        import json as _json
        name = _json.load(open(os.path.expanduser(
            "~/.config/legendary/user.json"))).get("displayName")
    except Exception:
        name = None
    return {"ok": True, "account": name}


def _gog_connected():
    """(bool) True if a GOG OAuth login has been cached (.gog/tokens.json)."""
    return os.path.exists(os.path.join(DATA, ".gog", "tokens.json"))


@app.post("/api/services/gog/code")
def gog_connect(body: dict = Body(...)):
    """Accept whatever the user copies from GOG's login-success page — the full
    redirect URL, a `code=…` pair, or the bare code — and hand it to gog_owned.py,
    which exchanges it for OAuth tokens that auto-refresh from then on."""
    code = _extract_token((body or {}).get("value", ""), ["code"])
    if not code:
        raise HTTPException(400, "no login code found in what you pasted")
    try:
        r = subprocess.run([sys.executable, os.path.join(DIR, "gog_owned.py"),
                            "--code", code],
                           capture_output=True, text=True, timeout=60, cwd=DIR)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "account": None, "error": "Couldn't reach GOG: %s" % e}
    if r.returncode != 0 or not _gog_connected():
        return {"ok": False, "account": None,
                "error": "That code didn't work — codes are single-use, so open "
                         "Get GOG code again for a fresh one and paste it."}
    return {"ok": True, "account": None}


def _psn_connected():
    """(bool) True if a PSN login has been cached (.psn/tokens.json)."""
    return os.path.exists(os.path.join(DATA, ".psn", "tokens.json"))


@app.post("/api/services/psn/npsso")
def psn_connect(body: dict = Body(...)):
    """Accept the PSN npsso — the bare 64-char token, the {"npsso":"…"} JSON, or
    the whole ssocookie response — and hand it to psn_owned.py, which exchanges it
    for tokens that auto-refresh (~2 months) from then on."""
    npsso = _extract_token((body or {}).get("value", ""), ["npsso"])
    if not npsso:
        raise HTTPException(400, "no npsso token found in what you pasted")
    try:
        r = subprocess.run([sys.executable, os.path.join(DIR, "psn_owned.py"),
                            "--npsso", npsso],
                           capture_output=True, text=True, timeout=60, cwd=DIR)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "account": None, "error": "Couldn't reach PSN: %s" % e}
    if r.returncode != 0 or not _psn_connected():
        detail = (r.stderr or "").strip().splitlines()[-1:] or [""]
        return {"ok": False, "account": None,
                "error": "That npsso didn't work — it expires quickly, so grab a "
                         "fresh one from the ssocookie page and paste it. (%s)"
                         % detail[0]}
    return {"ok": True, "account": None}


def _xbox_connected():
    """(bool) True if an Xbox/Microsoft login has been cached (.xbox/tokens.json)."""
    return os.path.exists(os.path.join(DATA, ".xbox", "tokens.json"))


@app.post("/api/services/xbox/code")
def xbox_connect(body: dict = Body(...)):
    """Accept the Microsoft auth code — the whole oauth20_desktop.srf?code=… URL,
    a code=… pair, or the bare code — and hand it to xbox_owned.py, which runs the
    OAuth→XSTS chain and caches a refresh token that auto-renews."""
    code = _extract_token((body or {}).get("value", ""), ["code"])
    if not code:
        raise HTTPException(400, "no auth code found in what you pasted")
    try:
        r = subprocess.run([sys.executable, os.path.join(DIR, "xbox_owned.py"),
                            "--code", code],
                           capture_output=True, text=True, timeout=90, cwd=DIR)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "account": None, "error": "Couldn't reach Xbox: %s" % e}
    if r.returncode != 0 or not _xbox_connected():
        detail = (r.stderr or "").strip().splitlines()[-1:] or [""]
        return {"ok": False, "account": None,
                "error": "That code didn't work — codes are single-use, so open Get "
                         "Xbox code again for a fresh one and paste it. (%s)"
                         % detail[0]}
    return {"ok": True, "account": None}


# Device-code flow — the reliable Xbox connect: no address-bar code to race. We
# hold the (secret) device_code server-side; the UI only ever sees the short
# user_code and polls /poll until Microsoft reports the sign-in is complete.
_XBOX_DEVICE = {}   # single pending auth: {"code": <device_code>, "expires_at": ts}


@app.post("/api/services/xbox/device/start")
def xbox_device_start():
    """Begin the Xbox device-code flow. Returns the short code + microsoft.com/link
    URL for the UI to display; the device_code stays here and is consumed by /poll."""
    try:
        r = subprocess.run([sys.executable, os.path.join(DIR, "xbox_owned.py"),
                            "--device-start"],
                           capture_output=True, text=True, timeout=30, cwd=DIR)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": "Couldn't reach Microsoft: %s" % e}
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return {"ok": False,
                "error": (r.stderr or "").strip().splitlines()[-1:] or ["start failed"]}
    _XBOX_DEVICE["code"] = d.get("device_code", "")
    _XBOX_DEVICE["expires_at"] = time.time() + int(d.get("expires_in", 900))
    return {"ok": True, "user_code": d.get("user_code", ""),
            "verification_uri": d.get("verification_uri",
                                      "https://www.microsoft.com/link"),
            "interval": int(d.get("interval", 5)),
            "expires_in": int(d.get("expires_in", 900))}


@app.post("/api/services/xbox/device/poll")
def xbox_device_poll():
    """Poll once for the device-code result (the UI calls this on a timer)."""
    code = _XBOX_DEVICE.get("code")
    if not code or time.time() > _XBOX_DEVICE.get("expires_at", 0):
        return {"status": "expired", "account": None}
    try:
        r = subprocess.run([sys.executable, os.path.join(DIR, "xbox_owned.py"),
                            "--device-poll", code],
                           capture_output=True, text=True, timeout=30, cwd=DIR)
        d = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"status": "pending", "account": None}   # transient — keep polling
    if d.get("status") == "connected":
        _XBOX_DEVICE.clear()
    return {"status": d.get("status", "pending"), "account": d.get("account") or None}


# --------------------------------------------------------------------------- #
#  Ownership sync — pull owned games from each store, then rebuild the catalog
# --------------------------------------------------------------------------- #
# id -> (fetch script, output TSV, capture_stdout). Fetchers that write their own
# file (epic) set capture_stdout=False.
SYNC_SPECS = {
    "steam": ("steam_owned.py", "steam_games.tsv", True),
    "gog":   ("gog_owned.py",   "gog_games.tsv",   True),
    "itch":  ("itch_owned.py",  "itch_games.tsv",  True),
    "epic":  ("epic_owned.py",  "epic_games.tsv",  False),
    "ea":    ("ea_owned.py",    "ea_games.tsv",    True),
    "psn":   ("psn_owned.py",   "psn_games.tsv",   True),
    "xbox":  ("xbox_owned.py",  "xbox_games.tsv",  True),
}

# Stores that also expose a WISHLIST (Discover "Wanted"). Pulled alongside owned
# during a sync so new wanted items enter the catalog and get identified +
# enriched (build_library merges *_wishlist.tsv into wanted/games).
WISHLIST_SPECS = {
    "steam": ("steam_wishlist.py", "steam_wishlist.tsv"),
    "gog":   ("gog_wishlist.py",   "gog_wishlist.tsv"),
}

# Sources that are also art providers (role='both') → the media_fetch provider to
# run when "also sync media" is checked. Only Steam qualifies today.
MEDIA_SYNC_PROVIDER = {"steam": "steam"}
_SVC_NAME = {s["id"]: s["name"] for s in SERVICES}


def _sync_ready(sid):
    """(bool) True if this source can pull ownership right now (creds/login present)."""
    if sid == "steam":
        return bool(config.steam_key() and config.get("steam_id"))
    if sid == "itch":
        return bool(config.itch_key())
    if sid == "gog":
        return _gog_connected()
    if sid == "epic":
        return _epic_connected()
    if sid == "ea":
        return _ea_connected()
    if sid == "psn":
        return _psn_connected()
    if sid == "xbox":
        return _xbox_connected()
    return False


def _tsv_count(out):
    """Games recorded in a fetcher's output TSV (None if it doesn't exist yet)."""
    p = os.path.join(DATA, out)
    if not os.path.exists(p):                     # fall back to the legacy /app path
        p = os.path.join(DIR, out)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return sum(1 for ln in f if ln.strip())
    except OSError:
        return None


# Signatures in a fetcher's error meaning "the stored login died — the user just
# needs to reconnect" (vs. a transient/network/code failure). When matched, the
# Sync menu offers the store's connect flow inline instead of a raw stack trace.
_REAUTH_SIGNS = (
    "invalid_grant", "invalid refresh token", "session expired", "reconnect",
    "not connected", "npsso", "re-run --npsso", "sign in again",
    "token expired", "unauthorized", "http error 401", "http error 403",
)


def _needs_reauth(err):
    e = (err or "").lower()
    return any(sig in e for sig in _REAUTH_SIGNS)


def _sync_services():
    """Per-source sync metadata for the Sync menu."""
    out = []
    for s in SERVICES:
        sid = s["id"]
        if sid not in SYNC_SPECS:
            continue
        _, tsv, _ = SYNC_SPECS[sid]
        ready = _sync_ready(sid)
        conn = s.get("connect")
        out.append({
            "id": sid, "name": s["name"],
            "enabled": config.source_enabled(sid),
            "ready": ready,
            "needs_auth": bool(conn) and not ready,
            "connect": dict(conn, connected=ready) if conn else None,
            "count": _tsv_count(tsv),
            "can_media": sid in MEDIA_SYNC_PROVIDER,
        })
    return out


_SYNC = {"job": None, "proc": None}   # proc = the currently-running phase subprocess
_SYNC_LOCK = threading.Lock()
_ROMSYNC = {"job": None}          # ROM-location scans (Connections devices)
_ROMSYNC_LOCK = threading.Lock()


# ---- sync pause / resume / stop: signal the current phase's process GROUP ----
def _sync_signal(sig):
    """Send `sig` to the running sync subprocess's process group; True if sent."""
    p = _SYNC.get("proc")
    if not p or p.poll() is not None:
        return False
    try:
        os.killpg(os.getpgid(p.pid), sig)
        return True
    except (ProcessLookupError, OSError):
        return False


def _kill_proc(p, group):
    """Force-kill a subprocess (its whole group when `group`, i.e. a sync phase)."""
    try:
        if group:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        else:
            p.kill()
    except (ProcessLookupError, OSError):
        try:
            p.kill()
        except OSError:
            pass
    try:
        p.wait(timeout=5)
    except Exception:                       # noqa: BLE001
        pass


def _sync_over_deadline(job, start, timeout):
    """True if wall-time since `start` EXCLUDING paused stretches exceeds timeout —
    so pausing a phase never triggers a spurious timeout-kill."""
    paused = job.get("paused_total", 0.0) if job else 0.0
    if job and job.get("paused") and job.get("paused_since"):
        paused += time.time() - job["paused_since"]
    return (time.time() - start - paused) > timeout


def _sync_gate(job):
    """Block while paused between phases (no live process to freeze). Returns
    False if the job was cancelled while waiting."""
    while job and job.get("paused") and not job.get("cancel"):
        time.sleep(0.4)
    return not (job and job.get("cancel"))


def _sync_pause():
    j = _SYNC.get("job")
    if not j or not j.get("running") or j.get("paused"):
        return False
    j["paused"], j["paused_since"] = True, time.time()
    _sync_signal(signal.SIGSTOP)
    return True


def _sync_resume():
    j = _SYNC.get("job")
    if not j or not j.get("paused"):
        return False
    _sync_signal(signal.SIGCONT)
    j["paused_total"] = j.get("paused_total", 0.0) + (time.time() - (j.get("paused_since") or time.time()))
    j["paused"], j["paused_since"] = False, None
    return True


def _sync_stop():
    """Cancel the running sync: flag it, un-freeze if paused, kill the phase."""
    j = _SYNC.get("job")
    if not j:
        return False
    j["cancel"] = True
    if j.get("paused"):                     # resume first so the kill lands + exits
        _sync_signal(signal.SIGCONT)
        j["paused"], j["paused_since"] = False, None
    p = _SYNC.get("proc")
    if p and p.poll() is None:
        _kill_proc(p, True)
    return True


def _lib_keys():
    db = config.get("library_db")
    if not db or not os.path.exists(db):
        return set()
    try:
        con = sqlite3.connect(db)
        keys = {r[0] for r in con.execute("SELECT norm_key FROM games")}
        con.close()
        return keys
    except sqlite3.Error:
        return set()


def _n_identified_with_cover():
    """Count identified games that have a chosen cover — feeds the sync 'media'
    checkmark's detail (how many titles have art). None on error."""
    try:
        con = sqlite3.connect(LIBRARY_DB)
        con.execute("ATTACH DATABASE ? AS m", (INDEX_DB,))
        n = con.execute(
            "SELECT COUNT(*) FROM games g WHERE " + IDENTIFIED_SQL +
            " AND EXISTS(SELECT 1 FROM m.media md WHERE md.norm_key=g.norm_key "
            "AND md.chosen=1 AND md.kind='cover')").fetchone()[0]
        con.close()
        return n
    except sqlite3.Error:
        return None


def _run_script(script, out=None, capture=False, timeout=300, args=None, job=None):
    """Run a pipeline script; return (ok, error_tail). When `job` is given the
    child gets its own session (so it can be paused/stopped as a group), is
    registered on _SYNC for the pause/stop endpoints, and the timeout excludes
    paused time. stderr goes to a temp file (read for the failure tail) so a
    chatty child can't deadlock on a full pipe."""
    if job is not None and not _sync_gate(job):
        return False, "cancelled"
    argv = [sys.executable, script] + list(args or [])
    errf = tempfile.TemporaryFile()
    outf = None
    try:
        stdout_dest = subprocess.DEVNULL
        if capture and out:
            # ownership/wishlist TSVs go in the DURABLE data dir, not the ephemeral
            # image layer — so store games survive a redeploy / catalog rebuild.
            outf = open(os.path.join(DATA, out), "w", encoding="utf-8")
            stdout_dest = outf
        p = subprocess.Popen(argv, cwd=DIR, stdout=stdout_dest, stderr=errf,
                             start_new_session=(job is not None))
    except OSError as e:
        errf.close()
        if outf:
            outf.close()
        return False, str(e)
    if job is not None:
        _SYNC["proc"] = p
    start, cancelled, timed_out = time.time(), False, False
    while p.poll() is None:
        if job is not None and job.get("cancel"):
            _sync_signal(signal.SIGCONT); _kill_proc(p, True); cancelled = True; break
        if _sync_over_deadline(job, start, timeout):
            _kill_proc(p, job is not None); timed_out = True; break
        time.sleep(0.3)
    if job is not None:
        _SYNC["proc"] = None
    if outf:
        outf.close()
    if cancelled:
        errf.close(); return False, "cancelled"
    if timed_out:
        errf.close(); return False, "timed out"
    if p.returncode != 0:
        errf.seek(0); tail = errf.read().decode("utf-8", "replace").strip()[-300:]
        errf.close(); return False, (tail or "exit %d" % p.returncode)
    errf.close()
    if os.path.basename(str(script)) == "build_library.py":
        _warm_spotlight_bg()          # prime the dashboard spotlight after a rebuild
    return True, ""


def _run_streaming(script, args, on_prog, timeout=3600, job=None):
    """Run a pipeline script and stream its stdout, calling on_prog(i, n, key, kind)
    for each `PROG\\t...` line so a live job shows what's being pulled. When `job`
    is given, the child is pause/stop-controllable (see _run_script)."""
    if job is not None and not _sync_gate(job):
        return False, "cancelled"
    argv = [sys.executable, os.path.join(DIR, script)] + list(args)
    try:
        p = subprocess.Popen(argv, cwd=DIR, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1,
                             start_new_session=(job is not None))
    except OSError as e:
        return False, str(e)
    if job is not None:
        _SYNC["proc"] = p
    start, tail, timed_out = time.time(), "", False
    for line in p.stdout:                   # blocks (harmlessly) while SIGSTOP-paused
        if line.startswith("PROG\t"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 5:
                try:
                    on_prog(int(parts[1]), int(parts[2]), parts[3], parts[4])
                except Exception:           # noqa: BLE001
                    pass
        elif line.strip():
            tail = line.strip()
        if job is not None and job.get("cancel"):
            break
        if _sync_over_deadline(job, start, timeout):
            timed_out = True; break
    cancelled = bool(job is not None and job.get("cancel"))
    if cancelled or timed_out:
        _sync_signal(signal.SIGCONT); _kill_proc(p, job is not None)
    else:
        p.wait()
    if job is not None:
        _SYNC["proc"] = None
    if cancelled:
        return False, "cancelled"
    if timed_out:
        return False, "timed out"
    if p.returncode == 0 and os.path.basename(str(script)) == "build_library.py":
        _warm_spotlight_bg()          # prime the dashboard spotlight after a rebuild
    return (p.returncode == 0), ("" if p.returncode == 0 else (tail[-300:] or "exit %d" % p.returncode))


def _fmt_eta(sec):
    """' · ~2m left'-style suffix for a job step; '' when unknown/near-zero."""
    if not sec or sec < 1:
        return ""
    sec = int(sec)
    if sec < 60:
        return " · ~%ds left" % sec
    if sec < 3600:
        return " · ~%dm left" % (sec // 60)
    return " · ~%dh%02dm left" % (sec // 3600, (sec % 3600) // 60)


def _sync_worker(job, services, media_ids=(), full=False):
    prev = _lib_keys()
    any_ok = False
    # progress across ALL phases, not just the ownership pulls: each source, the
    # catalog rebuild, each media fetch, and the one materialize pass.
    planned_media = [sid for sid in media_ids if sid in MEDIA_SYNC_PROVIDER]
    mode = config.get("media_mode") or "chosen"
    # + 4 fixed pipeline steps: Steam tags, catalog rebuild, IGDB enrich (with
    # its merge rebuild), and the multi-source scores pass.
    total = len(services) + 5 + len(planned_media) + 1 + (1 if mode != "ondemand" else 0)
    job["prog"] = {"done": 0, "total": max(total, 1)}

    # Post-source pipeline phases, shown as their own checkmark rows in the sync
    # panel so a full run confirms the WHOLE pipeline (not just ownership pulls).
    phases = [
        {"id": "tags", "label": "Steam tags", "state": "pending", "detail": ""},
        {"id": "catalog", "label": "Catalog rebuilt", "state": "pending", "detail": ""},
        {"id": "meta", "label": "Descriptions & attributes", "state": "pending", "detail": ""},
        {"id": "scores", "label": "Scores & ratings", "state": "pending", "detail": ""},
        {"id": "os", "label": "OS / platform support", "state": "pending", "detail": ""},
        {"id": "art", "label": "Missing art", "state": "pending", "detail": ""},
        {"id": "language", "label": "Language filter", "state": "pending", "detail": ""},
        {"id": "media", "label": "Media downloaded" if mode != "ondemand" else "Media chosen",
         "state": "pending", "detail": ""},
    ]
    job["phases"] = phases

    def _phase(pid, state, detail=None):
        for p in phases:
            if p["id"] == pid:
                p["state"] = state
                if detail is not None:
                    p["detail"] = detail
                break

    def step():
        job["prog"]["done"] = min(job["prog"]["done"] + 1, job["prog"]["total"])

    def _mk_prog(label, pid, base):
        """Callback for a streamed phase: climb the bar within this step (base ->
        base+1) and show a live 'i/n · ~ETA' on the step + phase detail."""
        t0 = [None]
        def cb(i, n, key, kind):
            if t0[0] is None:
                t0[0] = time.time()
            if not n:
                return
            job["prog"]["done"] = min(base + i / n, job["prog"]["total"])
            eta = (n - i) * ((time.time() - t0[0]) / i) if i > 0 else 0
            det = "%d/%d%s" % (i, n, _fmt_eta(eta))
            _phase(pid, "running", det)
            job["step"] = "%s %s" % (label, det)
        return cb

    def _count_prog(label, pid):
        """Like _mk_prog but text-only: batch/CPU work (catalog rebuild) isn't a
        steady per-item clock, so show a live 'N/total games' count without a
        (misleading) ETA or a per-phase bar climb."""
        def cb(i, n, key, kind):
            det = ("%s/%s games" % (f"{i:,}", f"{n:,}")) if n else ""
            _phase(pid, "running", det)
            job["step"] = "%s %s" % (label, det)
        return cb

    def _stopped():
        """True + finalize the job as stopped if the user cancelled it."""
        if job.get("cancel"):
            for p in phases:
                if p["state"] in ("running", "pending"):
                    _phase(p["id"], "skipped")
            job["step"] = "Stopped"
            job["running"] = False
            job["finished"] = True
            job["stopped"] = True
            return True
        return False

    for sid in services:
        st = job["services"][sid]
        st["state"] = "running"
        job["step"] = "Syncing %s…" % _SVC_NAME.get(sid, sid)
        script, tsv, cap = SYNC_SPECS[sid]
        ok, err = _run_script(script, tsv, cap, timeout=240, job=job)
        if ok:
            st["state"], st["count"], any_ok = "ok", _tsv_count(tsv), True
        else:
            st["state"], st["error"] = "failed", err
            st["reauth"] = _needs_reauth(err)
        # Also pull this store's wishlist (Wanted) if it has one, so new wanted
        # items enter the catalog and get identified/enriched alongside owned.
        # Best-effort — a wishlist failure never fails the store's sync.
        if ok and sid in WISHLIST_SPECS:
            wscript, wtsv = WISHLIST_SPECS[sid]
            job["step"] = "Fetching %s wishlist…" % _SVC_NAME.get(sid, sid)
            _run_script(wscript, wtsv, True, timeout=240, job=job)
        step()
        if _stopped():
            return
    if any_ok:
        # Steam community tags (SteamSpy) at import time — read from the fresh
        # steam TSV so newly-imported games are covered, fetched BEFORE the
        # rebuild merges them into game_tags. Only when SteamSpy is enabled and
        # Steam actually synced this run.
        _tags_base = job["prog"]["done"]
        if (config.metadata_enabled("steamspy")
                and job["services"].get("steam", {}).get("state") == "ok"):
            job["step"] = "Fetching Steam tags…"
            _phase("tags", "running")
            _steam_tsv = os.path.join(DATA, "steam_games.tsv")
            if not os.path.exists(_steam_tsv):
                _steam_tsv = os.path.join(DIR, "steam_games.tsv")
            ok_t, _ = _run_streaming(
                "steam_tags.py", ["--tsv", _steam_tsv],
                _mk_prog("Fetching Steam tags…", "tags", _tags_base),
                timeout=3600, job=job)
            _phase("tags", "ok" if ok_t else "failed",
                   "" if ok_t else None)
        else:
            _phase("tags", "skipped")
        job["prog"]["done"] = min(_tags_base + 1, job["prog"]["total"])
        if _stopped():
            return
        job["step"] = "Rebuilding catalog…"
        _phase("catalog", "running")
        ok, err = _run_streaming("build_library.py", [],
                                 _count_prog("Rebuilding catalog…", "catalog"),
                                 timeout=900, job=job)
        if ok:
            job["added"] = len(_lib_keys() - prev)
            _phase("catalog", "ok", "+%d new" % job["added"] if job["added"] else "up to date")
        else:
            job["error"] = "catalog rebuild failed: " + err
            _phase("catalog", "failed")

        # ---- metadata enrichment + scores for the freshly-imported games ----
        # The catalog now knows every new game's identity (store id + title), so
        # enrich WITHOUT AI: resolve each identified game against IGDB (by Steam
        # appid, else name search) and pull descriptions/genres/attributes, then
        # rebuild so build_library merges the cache into game_attributes — same
        # order as update.sh (igdb_enrich && build_library). Non-fatal: a failure
        # here never aborts the media pass. Runs for ALL stores, not just Steam.
        _meta_base = job["prog"]["done"]
        if not job.get("error") and config.metadata_enabled("igdb"):
            mlabel = ("Re-checking metadata (IGDB)…" if full
                      else "Enriching new metadata (IGDB)…")
            job["step"] = mlabel
            _phase("meta", "running")
            # Full refresh re-resolves everything, but SCOPED to the store(s) just
            # synced (via --source) so a Steam full refresh re-checks ~Steam games,
            # not the whole ROM-dominated catalog. New-games stays whole-catalog
            # (cheap — only unresolved games hit the network).
            enrich_args = []
            if full:
                for sid in services:
                    if job["services"].get(sid, {}).get("state") == "ok":
                        enrich_args += ["--source", sid]
                enrich_args.append("--all")
            ok_e, err_e = _run_streaming(
                "igdb_enrich.py", enrich_args,
                _mk_prog(mlabel, "meta", _meta_base), timeout=3600, job=job)
            if ok_e and not job.get("cancel"):
                job["step"] = "Merging metadata…"
                ok_m, err_m = _run_script("build_library.py", timeout=900, job=job)
                _phase("meta", "ok" if ok_m else "failed",
                       None if ok_m else "merge failed: " + err_m)
            elif not job.get("cancel"):
                _phase("meta", "failed", err_e)
        else:
            _phase("meta", "skipped")
        job["prog"]["done"] = min(_meta_base + 1, job["prog"]["total"])
        if _stopped():
            return

        # Ratings from every source ludodex knows (IGDB critic+user, Steam
        # reviews, GOG user score, and the local ScreenScraper cache) rolled into
        # the unified Ludodex score. Self-limiting (7-day per-source freshness
        # skip); also non-fatal so the media pass always runs.
        if not job.get("error"):
            job["step"] = ("Re-checking scores & ratings…" if full
                           else "Fetching new scores & ratings…")
            _phase("scores", "running")
            if full:
                # scope the refresh to the synced store(s) scores_fetch can fetch
                # (steam/gog), plus igdb critic scores (cache-based, cheap)
                sargs = [s for s in services if s in ("steam", "gog")
                         and job["services"].get(s, {}).get("state") == "ok"]
                sargs += ["igdb", "--refresh"]
            else:
                sargs = ["all"]
            ok_sc, err_sc = _run_script("scores_fetch.py", args=sargs, timeout=1800, job=job)
            _phase("scores", "ok" if ok_sc else "failed", None if ok_sc else err_sc)
        else:
            _phase("scores", "skipped")
        step()
        if _stopped():
            return

        # OS / platform support (Windows/Mac/Linux) for PC store games — Steam &
        # GOG expose it via their store APIs (fills the "OS" column, which is empty
        # until this runs). Rate-limited (Steam ~1.5s/appid), so the FIRST run over
        # a big library is slow; incremental after that (only new appids), and
        # non-fatal + pausable. Scoped to the synced store(s) os_fetch supports.
        _os_base = job["prog"]["done"]
        os_stores = [s for s in services if s in ("steam", "gog")
                     and job["services"].get(s, {}).get("state") == "ok"]
        if os_stores:
            job["step"] = "Fetching OS / platform support…"
            _phase("os", "running")
            ok_os = True
            for s in os_stores:
                ok_s, _ = _run_streaming(
                    "os_fetch.py", [s],
                    _mk_prog("Fetching %s OS support…" % _SVC_NAME.get(s, s), "os", _os_base),
                    timeout=5400, job=job)
                ok_os = ok_os and ok_s
                if job.get("cancel"):
                    break
            _phase("os", "ok" if ok_os else "failed")
        else:
            _phase("os", "skipped")
        job["prog"]["done"] = min(_os_base + 1, job["prog"]["total"])
        if _stopped():
            return
    else:
        for p in phases:
            _phase(p["id"], "skipped")
    step()
    # optional media pass: fetch art for the requested sources (catalog must exist
    # first), then download it into the repo per the media_mode preference.
    media_targets = [sid for sid in planned_media
                     if job["services"].get(sid, {}).get("state") == "ok"]
    if any_ok and not job.get("error"):
        cover_before = _n_identified_with_cover()
        for sid in media_targets:
            job["step"] = "Fetching %s media…" % _SVC_NAME.get(sid, sid)
            _run_script("media_fetch.py",
                        args=["--provider", MEDIA_SYNC_PROVIDER[sid]], timeout=1800, job=job)
            step()
            if _stopped():
                return
        # Built-in art gap-fill: any identified game whose source shipped no
        # cover/backdrop/logo (non-Steam stores, unresolved matches) gets it from
        # SteamGridDB by name/appid. Runs on EVERY successful sync — including
        # stores with no media provider, whose media_targets list is empty — so
        # "if the import doesn't have art, fetch it" holds. Self-limiting: SGDB
        # skips games that already have the art.
        job["step"] = "Fetching missing art…"
        _phase("art", "running")
        _run_script("media_fetch.py", args=["--backfill-art"], timeout=3600, job=job)
        _phase("art", "ok")
        step()
        if _stopped():
            return
        # Language filter: hide or ban art whose confident single language isn't
        # among the user's preferred languages. Off by default (no-op); runs
        # BEFORE materialize so hidden/banned assets are never chosen/downloaded.
        lm = medialang.mode()
        if lm != "off":
            job["step"] = "Filtering media by language…"
            _phase("language", "running")
            try:
                r = medialang.apply_filter()
                _phase("language", "ok",
                       ("%d hidden" % r["hidden"]) if lm == "hide"
                       else ("%d banned" % r["banned"]))
            except Exception as e:
                _phase("language", "failed", str(e)[:120])
        else:
            _phase("language", "skipped")
        _phase("media", "running")
        if mode != "ondemand":
            job["step"] = "Downloading media…"
            base = job["prog"]["done"]   # phases finished before the download

            def _mat_prog(i, n, nk, kind):
                job["step"] = ("Downloading media %d/%d — %s (%s)"
                               % (i, n, nk, kind.replace("_", " ")))
                if n:
                    job["prog"]["done"] = base + i / n   # climb through this phase live
            _run_streaming("media_choose.py",
                           ["--materialize", "--progress"] + (["--all"] if mode == "all" else []),
                           _mat_prog, timeout=3600, job=job)
        else:
            _run_script("media_choose.py", timeout=900, job=job)
        # report how much art coverage the run produced
        cover_after = _n_identified_with_cover()
        if cover_after is not None:
            _phase("media", "ok", "%s with art" % f"{cover_after:,}")
            if cover_before is not None:
                _phase("art", "ok", "+%d filled" % max(cover_after - cover_before, 0))
        else:
            _phase("media", "ok")
    job["prog"]["done"] = job["prog"]["total"]   # snap to complete
    job["step"] = "Done"
    job["running"] = False
    job["finished"] = True


@app.get("/api/sync/status")
def sync_status():
    """Syncable sources (enabled/ready/needs-auth) + current-or-last job progress."""
    return {"services": _sync_services(), "job": _SYNC["job"]}


@app.post("/api/sync/run")
def sync_run(body: dict = Body(default={})):
    """Start a sync of the given source ids, or 'all' = every enabled+ready source.
    Sources that need a browser sign-in (epic/ea) are skipped until connected."""
    with _SYNC_LOCK:
        cur = _SYNC["job"]
        if cur and cur.get("running"):
            raise HTTPException(409, "a sync is already running")
        if _ROMSYNC["job"] and _ROMSYNC["job"].get("running"):
            raise HTTPException(409, "a ROM sync is running — wait for it to finish "
                                     "(both rebuild the catalog)")
        req = (body or {}).get("services") or ["all"]
        if req in ("all", ["all"]):
            targets = [s["id"] for s in _sync_services() if s["enabled"] and s["ready"]]
        else:
            targets = [sid for sid in req if sid in SYNC_SPECS
                       and config.source_enabled(sid) and _sync_ready(sid)]
        if not targets:
            raise HTTPException(400, "nothing ready to sync")
        media = [sid for sid in ((body or {}).get("media") or [])
                 if sid in MEDIA_SYNC_PROVIDER and sid in targets]
        # full=True re-checks EVERY game for upstream changes (re-resolve + refetch
        # IGDB metadata, re-fetch all scores), ignoring the freshness caches.
        # Default (new-games) only enriches/scores games not yet done.
        full = bool((body or {}).get("full"))
        job = {"running": True, "finished": False, "step": "Starting…",
               "error": None, "added": None, "full": full,
               "paused": False, "paused_since": None, "paused_total": 0.0,
               "cancel": False, "stopped": False,
               "services": {sid: {"state": "pending", "count": None, "error": None,
                                  "reauth": False}
                            for sid in targets}}
        _SYNC["job"] = job
        _SYNC["proc"] = None
    threading.Thread(target=_sync_worker, args=(job, targets, media, full),
                     daemon=True).start()
    return job


# --------------------------------------------------------------------------- #
#  ROM-repo sync — rescan Connections devices' ROM locations, rebuild catalog.
#  Backgrounded like the store sync (a device scan can outlast a proxy timeout).
# --------------------------------------------------------------------------- #
def _romsync_worker(job, targets):
    """Rescan each target device (find→index→rebuild happens in sync_device)."""
    done, any_ok = 0, False
    for did, name in targets:
        d = job["devices"][str(did)]
        d["state"] = "running"
        job["step"] = "Scanning %s…" % name
        try:
            rep = devices.sync_device(did)
            results = rep.get("results", [])
            roms = sum(r["roms"] for r in results if isinstance(r.get("roms"), int))
            failed = [r for r in results if not r.get("ok")]
            if failed:
                d.update(state="failed", roms=roms or None,
                         error="; ".join(f.get("error", "?") for f in failed)[:200])
            else:
                d.update(state="ok", roms=roms, error=None)
                any_ok = True
        except Exception as e:
            d.update(state="failed", roms=None, error=str(e)[:200])
        done += 1
        job["prog"] = {"done": done, "total": len(targets)}
    job["running"] = False
    job["finished"] = True
    any_failed = any(v["state"] == "failed" for v in job["devices"].values())
    job["step"] = ("Finished with errors" if any_failed and not any_ok
                   else "Done with some errors" if any_failed
                   else "Done")


@app.get("/api/roms/status")
def roms_status():
    """ROM locations (Connections devices with ROM managers) + current/last job."""
    return {"locations": devices.rom_locations(), "job": _ROMSYNC["job"]}


@app.post("/api/roms/run")
def roms_run(body: dict = Body(default={})):
    """Rescan the given ROM-location device ids, or 'all' = every enabled one."""
    with _ROMSYNC_LOCK:
        cur = _ROMSYNC["job"]
        if cur and cur.get("running"):
            raise HTTPException(409, "a ROM sync is already running")
        if _SYNC["job"] and _SYNC["job"].get("running"):
            raise HTTPException(409, "a library sync is running — wait for it to finish "
                                     "(both rebuild the catalog)")
        locs = {loc["id"]: loc for loc in devices.rom_locations()}
        req = (body or {}).get("devices") or ["all"]
        if req in ("all", ["all"]):
            targets = [(loc["id"], loc["name"]) for loc in locs.values() if loc["enabled"]]
        else:
            targets = [(int(i), locs[int(i)]["name"]) for i in req
                       if str(i).isdigit() and int(i) in locs and locs[int(i)]["enabled"]]
        if not targets:
            raise HTTPException(400, "no ROM locations to sync")
        job = {"running": True, "finished": False, "step": "Starting…", "error": None,
               "devices": {str(i): {"state": "pending", "roms": None, "error": None}
                           for i, _ in targets},
               "prog": {"done": 0, "total": len(targets)}}
        _ROMSYNC["job"] = job
    threading.Thread(target=_romsync_worker, args=(job, targets), daemon=True).start()
    return job


@app.get("/api/health")
def health():
    return {"ok": True, "library": os.path.exists(LIBRARY_DB),
            "media_index": os.path.exists(INDEX_DB), "repo": REPO,
            "ai": ai.status()}


# ----------------------------------------------------------- AI art pick + dedupe
def _asset_local_path(r):
    """Local path for a media row's bytes — materialize URL refs on demand. None if unreachable."""
    ext = (r["ext"] or "jpg").split("?")[0]
    if r["sha1"]:
        p = os.path.join(REPO, "%s.%s" % (r["sha1"], ext))
        if os.path.exists(p):
            return p
    if r["ref_type"] == "file" and os.path.exists(r["ref"]):
        return r["ref"]
    if r["ref_type"] == "url":
        sha = media_choose._materialize_row(REPO, r)
        if sha:
            wcon = sqlite3.connect(INDEX_DB)
            try:
                wcon.execute("UPDATE media SET sha1=? WHERE id=?", (sha, r["id"]))
                wcon.commit()
            finally:
                wcon.close()
            p = os.path.join(REPO, "%s.%s" % (sha, ext))
            if os.path.exists(p):
                return p
    return None


def _is_degenerate_image(data):
    """True if image bytes are effectively BLANK — a solid/near-uniform color, far
    too small, or (with alpha) almost fully transparent. Catches the placeholder
    art providers sometimes return (esp. ScreenScraper) that passes a bare HTTP
    check but is useless. Undecodable → not judged (keep it)."""
    try:
        from PIL import Image, ImageStat
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        if w < 24 or h < 24:
            return True
        im = im.convert("RGBA").resize((32, 32))
        if sum(ImageStat.Stat(im.convert("RGB")).var) / 3.0 < 20:   # no color variance
            return True
        if sum(1 for p in im.getchannel("A").getdata() if p > 16) < 20:  # ~transparent
            return True
        return False
    except Exception:
        return False


def _prune_blank_media(norm_keys, kinds=("cover", "hero", "background", "header",
                                         "logo", "icon", "box_back", "box_3d")):
    """Download + inspect each candidate image for the given games and DELETE the
    blank/degenerate ones so media_choose then picks a real image instead. Returns
    the number dropped. (Downloads are auth-aware and cached, so re-use is cheap.)"""
    if not norm_keys:
        return 0
    ph = ",".join("?" * len(kinds))
    rc = ro(INDEX_DB)                       # gather candidates, then close (no lock
    rc.row_factory = sqlite3.Row           # held while _thumb_bytes writes sha1)
    rows = []
    try:
        for nk in norm_keys:
            rows += rc.execute(
                "SELECT id, ref_type, ref, ext, sha1, provider FROM media "
                "WHERE norm_key=? AND kind IN (%s)" % ph, [nk] + list(kinds)).fetchall()
    finally:
        rc.close()
    bad = []
    for r in rows:
        mb = _thumb_bytes(r)               # opens/closes its own connections
        if mb and _is_degenerate_image(mb[1]):
            bad.append(r["id"])
    if bad:
        wc = sqlite3.connect(INDEX_DB, timeout=30)
        try:
            wc.executemany("DELETE FROM media WHERE id=?", [(i,) for i in bad])
            wc.commit()
        finally:
            wc.close()
    return len(bad)


def _thumb_bytes(r, px=256):
    """Downscaled JPEG bytes for a media row (for vision). (mime, bytes) or None."""
    p = _asset_local_path(r)
    if not p:
        return None
    try:
        from PIL import Image
        im = Image.open(p)
        im.thumbnail((px, px))
        if im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return ("image/jpeg", buf.getvalue())
    except Exception:
        return None


@app.get("/api/media-asset/{asset_id}")
def media_asset_by_id(asset_id: int, size: str = Query(None, pattern="^thumb$")):
    """Serve a specific media row by id (for art-pick candidate previews)."""
    rcon = ro(INDEX_DB)
    try:
        r = rcon.execute("SELECT id, ref_type, ref, ext, sha1, provider "
                         "FROM media WHERE id=?", (asset_id,)).fetchone()
    finally:
        rcon.close()
    if not r:
        raise HTTPException(404, "no such asset")
    p = _asset_local_path(r)
    if not p:
        raise HTTPException(404, "asset not reachable on this host")
    return _serve(p, (r["ext"] or "jpg").split("?")[0], size)


@app.post("/api/ai/art-pick/{norm_key}")
def art_pick(norm_key: str, kind: str = Query("cover")):
    """AI picks the best candidate asset for (norm_key, kind) among providers."""
    norm_key = _split_entry_key(norm_key)[0]
    if not ai.area_available("art"):
        raise HTTPException(503, "art-pick not configured (set a provider + API key)")
    lcon = ro(LIBRARY_DB)
    try:
        g = lcon.execute("SELECT canonical_title FROM games WHERE norm_key=?",
                         (norm_key,)).fetchone()
    finally:
        lcon.close()
    title = g["canonical_title"] if g else norm_key
    rcon = ro(INDEX_DB)
    try:
        rows = rcon.execute(
            "SELECT id, provider, ref_type, ref, ext, sha1 FROM media "
            "WHERE norm_key=? AND kind=? ORDER BY chosen DESC, id",
            (norm_key, kind)).fetchall()
    finally:
        rcon.close()
    cands = []
    for r in rows:
        t = _thumb_bytes(r)
        if t:
            cands.append({"id": r["id"], "provider": r["provider"], "thumb": t})
        if len(cands) >= 6:
            break
    listed = [{"id": c["id"], "provider": c["provider"]} for c in cands]
    if len(cands) < 2:
        return {"kind": kind, "candidates": listed,
                "recommended_id": cands[0]["id"] if cands else None,
                "reason": ("Only one candidate reachable on this host."
                           if cands else "No candidates reachable on this host.")}
    try:
        res = ai.pick_art(title, kind, [c["thumb"] for c in cands],
                          provider=ai.provider_for_area("art"),
                          model=ai.model_for_area("art"),
                          language=config.get("media_language") or None)
    except Exception as e:
        raise HTTPException(502, "AI error: %s" % e)
    return {"kind": kind, "candidates": listed,
            "recommended_id": cands[res["index"]]["id"], "reason": res["reason"]}


@app.post("/api/ai/art-apply")
def art_apply(body: dict):
    """Apply an art pick: mark one asset chosen for (norm_key, kind)."""
    aid, nk, kind = (body or {}).get("id"), (body or {}).get("norm_key"), (body or {}).get("kind")
    if not (aid and nk and kind):
        raise HTTPException(400, "need id, norm_key, kind")
    wcon = sqlite3.connect(INDEX_DB)
    try:
        wcon.execute("UPDATE media SET chosen=0 WHERE norm_key=? AND kind=?", (nk, kind))
        wcon.execute("UPDATE media SET chosen=1 WHERE id=?", (aid,))
        wcon.commit()
    finally:
        wcon.close()
    return {"ok": True}


def _dupe_year(v):
    """Parse a release_year/release_date attribute value to a 4-digit int, else None."""
    m = re.search(r"\d{4}", str(v or ""))
    return int(m.group()) if m else None


def _different_games(a, b):
    """True when two similarly-titled entries are almost certainly DIFFERENT games —
    a remake / re-release, not a duplicate — so dedupe must NOT fold them together.
    The discriminator is release year (Uno 2006 vs 2016, Tomb Raider 1996 vs 2013);
    a distinct IGDB match is a secondary signal when neither carries a year."""
    ya, yb = _dupe_year(a["yr"]), _dupe_year(b["yr"])
    if ya and yb:
        return abs(ya - yb) >= 2            # both years known & meaningfully apart
    ida, idb = a["igdb_id"], b["igdb_id"]
    return bool(ida and idb and ida != idb)  # no years, but distinct IGDB games


def _dedupe_candidates(con, limit=15):
    """Find likely same-game pairs norm_key missed (blocked similarity scan). Pairs
    that are really different-year remakes are excluded (see _different_games)."""
    import difflib
    rows = con.execute(
        "SELECT g.norm_key, g.canonical_title, g.sources_summary, "
        "(SELECT value FROM game_attributes ga WHERE ga.game_id=g.id "
        " AND ga.kind IN ('release_year','release_date') "
        " ORDER BY ga.kind DESC LIMIT 1) AS yr, "
        "(SELECT provider_id FROM metadata_links ml WHERE ml.game_id=g.id "
        " AND ml.provider='igdb' LIMIT 1) AS igdb_id "
        "FROM games g").fetchall()

    def loose(t):
        return re.sub(r"[^a-z0-9]", "", (t or "").lower())

    blocks = {}
    for r in rows:
        blocks.setdefault(loose(r["canonical_title"])[:5], []).append(r)
    scored = []
    for grp in blocks.values():
        if len(grp) < 2 or len(grp) > 60:        # skip singletons + huge blocks
            continue
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                a, b = grp[i], grp[j]
                if a["norm_key"] == b["norm_key"]:
                    continue
                if _different_games(a, b):       # remake, not a duplicate — skip
                    continue
                ratio = difflib.SequenceMatcher(
                    None, loose(a["canonical_title"]), loose(b["canonical_title"])).ratio()
                if ratio >= 0.82:
                    scored.append((ratio, a, b))
    scored.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for ratio, a, b in scored:
        k = (a["norm_key"], b["norm_key"])
        if k in seen:
            continue
        seen.add(k)
        out.append({"a": a["canonical_title"], "b": b["canonical_title"],
                    "a_nk": a["norm_key"], "b_nk": b["norm_key"],
                    "a_src": a["sources_summary"], "b_src": b["sources_summary"],
                    "ratio": round(ratio, 2)})
        if len(out) >= limit:
            break
    return out


@app.post("/api/ai/dedupe")
def ai_dedupe(body: dict = None):
    """Find likely duplicate pairs and have the AI adjudicate each (review-only)."""
    if not ai.area_available("dedupe"):
        raise HTTPException(503, "dedupe not configured (set a provider + API key)")
    limit = int((body or {}).get("limit", 15))
    con = lib()
    try:
        cands = _dedupe_candidates(con, limit)
    finally:
        con.close()
    if not cands:
        return {"suggestions": []}
    try:
        verdicts = ai.dedupe_pairs(
            [{"a": c["a"], "b": c["b"], "a_src": c["a_src"], "b_src": c["b_src"]}
             for c in cands],
            provider=ai.provider_for_area("dedupe"),
            model=ai.model_for_area("dedupe"))
    except Exception as e:
        raise HTTPException(502, "AI error: %s" % e)
    vmap = {int(v.get("n", i + 1)): v for i, v in enumerate(verdicts or [])}
    out = []
    for i, c in enumerate(cands):
        v = vmap.get(i + 1, {})
        out.append({**c, "same": bool(v.get("same")),
                    "confidence": v.get("confidence"), "reason": v.get("reason", "")})
    return {"suggestions": out}


@app.get("/api/games/dupes")
def games_dupes(limit: int = Query(40)):
    """Suspected duplicate pairs (fuzzy title similarity — no AI), for one-click
    Fix-duplication review. `ratio` is the title similarity (0..1)."""
    con = lib()
    try:
        return {"dupes": _dedupe_candidates(con, max(1, min(limit, 200)))}
    finally:
        con.close()


@app.post("/api/games/{nk}/merge")
def games_merge(nk: str, body: dict = Body(...)):
    """Merge two duplicate catalog entries into one. Body:
    {"other": "<norm_key>", "canonical": "this"|"other"}. The non-canonical entry
    folds into the canonical one durably (survives every rebuild); its media /
    tags / scores / ownership are re-keyed onto the survivor, while identity
    (title + IGDB match) stays with the canonical. Returns the canonical norm_key."""
    body = body or {}
    other = (body.get("other") or "").strip()
    which = body.get("canonical")
    if not other or other == nk:
        raise HTTPException(400, "need a different 'other' game to merge")
    if which not in ("this", "other"):
        raise HTTPException(400, "canonical must be 'this' or 'other'")
    con = lib()
    try:
        info = {r["norm_key"]: r for r in con.execute(
            "SELECT g.norm_key, g.canonical_title, "
            "(SELECT value FROM game_attributes ga WHERE ga.game_id=g.id "
            " AND ga.kind IN ('release_year','release_date') "
            " ORDER BY ga.kind DESC LIMIT 1) AS yr, "
            "(SELECT provider_id FROM metadata_links ml WHERE ml.game_id=g.id "
            " AND ml.provider='igdb' LIMIT 1) AS igdb_id "
            "FROM games g WHERE g.norm_key IN (?,?)", (nk, other))}
    finally:
        con.close()
    if nk not in info or other not in info:
        raise HTTPException(404, "one or both games not found")
    titles = {k: r["canonical_title"] for k, r in info.items()}
    # Warn-but-allow: merging across different release years usually means folding a
    # remake into its predecessor (Uno 2006 into 2016) — destructive. Surface it and
    # require an explicit confirm (force) rather than silently merging.
    if not body.get("force") and _different_games(info[nk], info[other]):
        ya, yb = _dupe_year(info[nk]["yr"]), _dupe_year(info[other]["yr"])
        detail = ("These look like DIFFERENT games, not duplicates"
                  + (" (%s vs %s)" % (ya, yb) if ya and yb else "")
                  + " — merging will fold one into the other. Confirm to proceed.")
        raise HTTPException(409, detail)
    to_key = nk if which == "this" else other
    from_key = other if which == "this" else nk
    try:
        merges.add(from_key, to_key, titles.get(from_key, ""), titles.get(to_key, ""))
        merges.rekey_user_data(from_key, to_key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    ok, err = _run_script("build_library.py", timeout=900)
    if not ok:
        raise HTTPException(502, "merged, but catalog rebuild failed: %s" % err)
    return {"merged": True, "canonical": to_key, "from": from_key}


@app.get("/api/games/{nk}/sources")
def game_sources(nk: str):
    """Per-source rows of a game, for the 'Peel apart' picker (which source belongs
    to the OTHER same-named game)."""
    con = lib()
    try:
        g = con.execute("SELECT id, canonical_title FROM games WHERE norm_key=?",
                        (nk,)).fetchone()
        if not g:
            raise HTTPException(404, "game not found")
        rows = [dict(r) for r in con.execute(
            "SELECT source, platform, source_id, title_raw, detail, state "
            "FROM sources WHERE game_id=? ORDER BY source, platform, title_raw",
            (g["id"],))]
        return {"norm_key": nk, "title": g["canonical_title"], "sources": rows}
    finally:
        con.close()


@app.post("/api/games/{nk}/split")
def games_split(nk: str, body: dict = Body(...)):
    """Peel selected source rows off a merged entry into a NEW, separately-identified
    game (the inverse of merge). Body: {"rows":[{"source","source_id"}],
    "title":"Uno (2006)"}. The peeled rows get their own norm_key on every rebuild;
    identify the new entry (title/IGDB) afterward like any game."""
    body = body or {}
    rows = body.get("rows") or []
    title = (body.get("title") or "").strip()
    if not rows:
        raise HTTPException(400, "select at least one source to peel off")
    if not title:
        raise HTTPException(400, "name the peeled-off game — include a year "
                                 "(e.g. \"Uno (2006)\") so it's a distinct entry")
    to_key = titlenorm.norm(title)
    if not to_key:
        raise HTTPException(400, "that title normalizes to nothing")
    if to_key == nk:
        raise HTTPException(409, "That title maps to the SAME entry — add a year "
                                 "(e.g. \"Uno (2006)\") so the peeled game is distinct.")
    con = lib()
    try:
        g = con.execute("SELECT id FROM games WHERE norm_key=?", (nk,)).fetchone()
        if not g:
            raise HTTPException(404, "game not found")
        owned = {(r["source"], str(r["source_id"])) for r in con.execute(
            "SELECT source, source_id FROM sources WHERE game_id=?", (g["id"],))}
    finally:
        con.close()
    picked = [(r.get("source"), str(r.get("source_id"))) for r in rows]
    if any(p not in owned for p in picked):
        raise HTTPException(400, "some selected rows aren't part of this game")
    if len(set(picked)) >= len(owned):
        raise HTTPException(400, "can't peel off EVERY source — leave at least one "
                                 "on the original (otherwise just rename it)")
    try:
        splits.add_many(picked, to_key, title, nk)
    except ValueError as e:
        raise HTTPException(400, str(e))
    ok, err = _run_script("build_library.py", timeout=900)
    if not ok:
        raise HTTPException(502, "peeled, but catalog rebuild failed: %s" % err)
    return {"split": True, "to_key": to_key, "title": title, "peeled": len(picked)}


@app.post("/api/games/{nk}/split-suggest")
def games_split_suggest(nk: str):
    """Agentic 'peel apart': ask the model whether this entry is really 2+ different
    same-named games and how its source rows split. Returns the suggested grouping
    (row indices are 1-based into the returned `sources`) for the user to confirm."""
    if not ai.area_available("split"):
        raise HTTPException(503, "split assist not configured (set a provider + API key)")
    con = lib()
    try:
        g = con.execute("SELECT id, canonical_title FROM games WHERE norm_key=?",
                        (nk,)).fetchone()
        if not g:
            raise HTTPException(404, "game not found")
        srcs = [dict(r) for r in con.execute(
            "SELECT source, platform, source_id, title_raw, detail "
            "FROM sources WHERE game_id=? ORDER BY source, platform, title_raw",
            (g["id"],))]
    finally:
        con.close()
    if len(srcs) < 2:
        return {"multiple": False, "reason": "only one source — nothing to peel",
                "games": [], "sources": srcs}
    # per-row year hint = any 4-digit year embedded in the listed title / detail
    for r in srcs:
        m = re.search(r"\b(19|20)\d{2}\b", "%s %s" % (r.get("title_raw") or "",
                                                      r.get("detail") or ""))
        r["year"] = int(m.group()) if m else None
    try:
        res = ai.split_adjudicate(g["canonical_title"], srcs,
                                  provider=ai.provider_for_area("split"),
                                  model=ai.model_for_area("split"))
    except Exception as e:                                  # noqa: BLE001
        raise HTTPException(502, "split assist failed: %s" % str(e)[:200])
    res["sources"] = srcs
    return res


# --------------------------------------------------------------------------- #
#  Database sync: push the catalog OUT to PocketBase / Firestore (sync.py).
#  This is the *outbound* mirror (distinct from /api/sync = ownership pull).
# --------------------------------------------------------------------------- #
_DBSYNC = {"job": None}
_DBSYNC_LOCK = threading.Lock()
_FB_SA_PATH = os.path.join(DATA, "firebase-sa.json")


def _dbsync_state():
    tgt = config.get("sync_target") or ""
    sa = config.get("firebase_sa_json") or ""
    return {
        "sync_target": tgt,
        "pb_enabled": tgt in ("pocketbase", "both"),
        "fb_enabled": tgt in ("firebase", "both"),
        "pocketbase": {
            "url": config.get("pocketbase_url") or "",
            "email": config.get("pocketbase_admin_email") or "",
            "password_set": bool(config.pocketbase_password()),
        },
        "firebase": {
            "project_id": config.get("firebase_project_id") or "",
            "database": config.get("firebase_database") or "(default)",
            "prefix": config.get("firebase_collection_prefix") or "",
            "sa_set": bool(sa and os.path.exists(sa)),
        },
        "job": _DBSYNC["job"],
    }


@app.get("/api/dbsync")
def dbsync_get():
    return _dbsync_state()


@app.post("/api/dbsync")
def dbsync_set(body: dict = Body(...)):
    b = body or {}
    pb, fb = b.get("pocketbase") or {}, b.get("firebase") or {}
    if "url" in pb:
        config.set_("pocketbase_url", (pb.get("url") or "").strip().rstrip("/"))
    if "email" in pb:
        config.set_("pocketbase_admin_email", (pb.get("email") or "").strip())
    if pb.get("password"):
        config.set_("pocketbase_admin_password", pb["password"])
    if pb.get("clear_password"):
        config.set_("pocketbase_admin_password", "")
    if "project_id" in fb:
        config.set_("firebase_project_id", (fb.get("project_id") or "").strip())
    if "database" in fb:
        config.set_("firebase_database", (fb.get("database") or "").strip() or "(default)")
    if "prefix" in fb:
        config.set_("firebase_collection_prefix", (fb.get("prefix") or "").strip())
    if fb.get("sa_json"):
        try:
            json.loads(fb["sa_json"])
        except Exception:
            raise HTTPException(400, "the service-account key is not valid JSON")
        with open(_FB_SA_PATH, "w", encoding="utf-8") as f:
            f.write(fb["sa_json"])
        os.chmod(_FB_SA_PATH, 0o600)
        config.set_("firebase_sa_json", _FB_SA_PATH)
    if fb.get("clear_sa"):
        p = config.get("firebase_sa_json")
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
        config.set_("firebase_sa_json", "")
    if "pb_enabled" in b or "fb_enabled" in b:
        cur = _dbsync_state()
        pbo = bool(b.get("pb_enabled", cur["pb_enabled"]))
        fbo = bool(b.get("fb_enabled", cur["fb_enabled"]))
        config.set_("sync_target",
                    "both" if pbo and fbo else "pocketbase" if pbo else "firebase" if fbo else "")
    return _dbsync_state()


def _result(checks):
    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks,
            "summary": "Good to go — ludodex can publish the catalog here."
            if ok else "Not ready yet — see the failed check above."}


def _pb_test():
    import urllib.request
    import urllib.error
    url = (config.get("pocketbase_url") or "").rstrip("/")
    email = config.get("pocketbase_admin_email") or ""
    pw = config.pocketbase_password() or ""
    if not (url and email and pw):
        return {"ok": False, "checks": [{"label": "Configuration", "ok": False,
                "detail": "Set the URL, admin email, and password first."}],
                "summary": "Not configured yet."}

    def http(method, path, tok=None, body=None):
        data = json.dumps(body).encode() if body is not None else None
        h = {"content-type": "application/json"}
        if tok:
            h["Authorization"] = tok
        req = urllib.request.Request(url + path, data=data, method=method, headers=h)
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})

    checks = []
    # 1 + 2: reach + superuser auth
    tok, last = None, "no response"
    try:
        for ep in ("/api/collections/_superusers/auth-with-password",
                   "/api/admins/auth-with-password"):
            try:
                st, resp = http("POST", ep, body={"identity": email, "password": pw})
                if st == 200 and resp.get("token"):
                    tok = resp["token"]
                    break
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
    except Exception as e:
        return _result([{"label": "Reach the server", "ok": False,
                         "detail": "%s (%s)" % (str(e)[:90], url)}])
    checks.append({"label": "Reached the server", "ok": True, "detail": url})
    if not tok:
        checks.append({"label": "Authenticated as superuser", "ok": False,
                       "detail": "sign-in failed (%s) — check the email/password" % last})
        return _result(checks)
    checks.append({"label": "Authenticated as superuser", "ok": True,
                   "detail": "full admin access"})
    # 3: catalog collections present?
    try:
        _, cols = http("GET", "/api/collections?perPage=200", tok=tok)
        names = {c["name"] for c in cols.get("items", [])}
        present = {}
        for n in ("games", "sources"):
            if n in names:
                _, cr = http("GET", "/api/collections/%s/records?perPage=1" % n, tok=tok)
                present[n] = cr.get("totalItems", 0)
        if present:
            checks.append({"label": "Catalog collections", "ok": True,
                           "detail": ", ".join("%s (%s records)" % (k, v) for k, v in present.items())})
        else:
            checks.append({"label": "Catalog collections", "ok": True,
                           "detail": "none yet — games & sources will be created on first sync"})
    except Exception as e:
        checks.append({"label": "Catalog collections", "ok": False, "detail": str(e)[:100]})
    # 4: prove it can create collections (create + delete a probe)
    probe = "ludodex_conntest"
    try:
        st, resp = http("POST", "/api/collections", tok=tok,
                        body={"name": probe, "type": "base",
                              "fields": [{"name": "t", "type": "text"}]})
        if st in (200, 201):
            try:
                http("DELETE", "/api/collections/%s" % resp.get("id", probe), tok=tok)
            except Exception:
                pass
            checks.append({"label": "Can create collections", "ok": True,
                           "detail": "verified — made and removed a test collection"})
        else:
            checks.append({"label": "Can create collections", "ok": False,
                           "detail": "HTTP %d" % st})
    except Exception as e:
        try:
            http("DELETE", "/api/collections/%s" % probe, tok=tok)
        except Exception:
            pass
        checks.append({"label": "Can create collections", "ok": False, "detail": str(e)[:100]})
    return _result(checks)


def _fb_test():
    import urllib.request
    import urllib.error
    import urllib.parse
    sa = config.get("firebase_sa_json") or ""
    pid = config.get("firebase_project_id") or ""
    db = config.get("firebase_database") or "(default)"
    if not (sa and os.path.exists(sa) and pid):
        return {"ok": False, "checks": [{"label": "Configuration", "ok": False,
                "detail": "Set the project id and paste the service-account key first."}],
                "summary": "Not configured yet."}
    checks = []
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests as gar
        creds = service_account.Credentials.from_service_account_file(
            sa, scopes=["https://www.googleapis.com/auth/datastore"])
        creds.refresh(gar.Request())
        token = creds.token
        checks.append({"label": "Service-account key", "ok": True,
                       "detail": "valid — minted an access token"})
    except Exception as e:
        return _result([{"label": "Service-account key", "ok": False, "detail": str(e)[:130]}])
    # reach the Firestore database
    dbid = urllib.parse.quote(db, safe="")
    u = ("https://firestore.googleapis.com/v1/projects/%s/databases/%s/documents"
         "?pageSize=1" % (pid, dbid))
    try:
        req = urllib.request.Request(u, headers={"Authorization": "Bearer %s" % token})
        urllib.request.urlopen(req, timeout=15).read()
        checks.append({"label": "Firestore database", "ok": True,
                       "detail": "reachable & writable (project %s, %s)" % (pid, db)})
    except urllib.error.HTTPError as e:
        detail = {403: "the service account lacks Firestore/Datastore access",
                  404: "project or database not found — check the ids",
                  400: "bad request — check the database id"}.get(e.code, "HTTP %d" % e.code)
        checks.append({"label": "Firestore database", "ok": False, "detail": detail})
    except Exception as e:
        checks.append({"label": "Firestore database", "ok": False, "detail": str(e)[:100]})
    return _result(checks)


@app.post("/api/dbsync/test")
def dbsync_test(body: dict = Body(default={})):
    return _pb_test() if (body or {}).get("target", "pocketbase") == "pocketbase" else _fb_test()


def _dbsync_worker(target):
    ok, err = _run_script("sync.py", args=[target], timeout=1800)
    if _DBSYNC["job"]:
        _DBSYNC["job"].update({"running": False, "finished": True, "ok": ok,
                               "error": "" if ok else err,
                               "step": "Done" if ok else "Failed"})


@app.post("/api/dbsync/run")
def dbsync_run(body: dict = Body(default={})):
    with _DBSYNC_LOCK:
        cur = _DBSYNC["job"]
        if cur and cur.get("running"):
            raise HTTPException(409, "a database sync is already running")
        target = (body or {}).get("target") or config.get("sync_target")
        if not target:
            raise HTTPException(400, "no sync target enabled — enable PocketBase and/or Firestore first")
        _DBSYNC["job"] = {"running": True, "finished": False, "target": target,
                          "step": "Syncing to %s…" % target, "ok": None, "error": ""}
    threading.Thread(target=_dbsync_worker, args=(target,), daemon=True).start()
    return _dbsync_state()


# ---------------------------------------------------------------- static SPA (last)
# Mounted at "/" AFTER all /api routes so the API takes precedence; serves the
# built React app (web/dist) when present.
from fastapi.staticfiles import StaticFiles          # noqa: E402

WEB_DIST = os.path.join(DIR, "web", "dist")
if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
