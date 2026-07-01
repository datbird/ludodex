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
import os
import re
import sqlite3
import sys

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)
import config          # noqa: E402  pipeline config store (config.sqlite)
import media           # noqa: E402  pipeline vocab/priority (pure data)
import media_choose    # noqa: E402  reuse _materialize_row (non-destructive)
from . import ai       # noqa: E402  AI features (server package)

LIBRARY_DB = os.path.join(DIR, "game-library.sqlite")
INDEX_DB = os.path.join(DIR, "media-index.sqlite")
REPO = media_choose.repo_dir()            # content-addressed media repo (DIR/media)
THUMBS = os.path.join(REPO, ".thumbs")
os.makedirs(THUMBS, exist_ok=True)

# Source flags that exist as columns on `games` (the rest live in `sources`).
COLUMN_SOURCES = ("emulation", "steam", "gog", "epic", "itch", "archive")

app = FastAPI(title="ludodex", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ----------------------------------------------------------------------------- db
def ro(path):
    """Open a SQLite db read-only (one connection per request — cheap, thread-safe)."""
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    con.row_factory = sqlite3.Row
    return con


def lib():
    """game-library connection with media-index ATTACHed read-only as `m`."""
    con = ro(LIBRARY_DB)
    con.execute("ATTACH DATABASE ? AS m", ("file:%s?mode=ro" % INDEX_DB,))
    return con


# ------------------------------------------------------------------------- routes
@app.get("/api/stats")
def stats():
    con = lib()
    try:
        g = con.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        cross = con.execute("SELECT COUNT(*) FROM games WHERE n_kinds>1").fetchone()[0]
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
            "cross_source": cross,
            "by_source": by_source,
            "media": {"games_with_art": total_with, "by_kind": coverage},
        }
    finally:
        con.close()


@app.get("/api/facets")
def facets():
    """Distinct sources + platforms for the UI filter dropdowns."""
    con = lib()
    try:
        sources = [r["source"] for r in con.execute(
            "SELECT DISTINCT source FROM sources ORDER BY source")]
        platforms = [r["platform"] for r in con.execute(
            "SELECT platform, COUNT(*) c FROM sources WHERE platform IS NOT NULL "
            "AND platform!='' GROUP BY platform ORDER BY c DESC")]
        return {"sources": sources, "platforms": platforms}
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
    "has_cover": "EXISTS(SELECT 1 FROM m.media md WHERE md.norm_key=g.norm_key "
                 "AND md.chosen=1 AND md.kind='cover')",
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


def _order_by(sort):
    """Build an ORDER BY clause from an ordered list of SORT_SQL keys; always
    ends with a stable title tiebreak."""
    parts = []
    for k in (sort or []):
        if k in SORT_SQL:
            expr, d = SORT_SQL[k]
            parts.append("%s %s" % (expr, d))
    parts.append("g.canonical_title COLLATE NOCASE ASC")
    return " ORDER BY " + ", ".join(parts)


def _query_games(con, q=None, source=None, platform=None, has_kind=None,
                 include=None, exclude=None, sort=None, limit=60, offset=0):
    """Core catalog query — shared by /api/games and AI /api/search.
    include/exclude are lists of FLAG_SQL keys (a flag can't be in both);
    sort is an ordered list of SORT_SQL keys (1st, 2nd, 3rd priority)."""
    where, args = [], []
    if q:
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
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    total = con.execute("SELECT COUNT(*) FROM games g" + clause, args).fetchone()[0]
    rows = con.execute(
        "SELECT g.norm_key, g.canonical_title, g.n_sources, g.n_kinds, "
        "g.sources_summary, "
        "(SELECT group_concat(DISTINCT s.platform) FROM sources s "
        "   WHERE s.game_id=g.id AND s.platform IS NOT NULL AND s.platform!='') AS platforms, "
        "EXISTS(SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id) AS matched, "
        "EXISTS(SELECT 1 FROM m.media md WHERE md.norm_key=g.norm_key "
        "       AND md.chosen=1 AND md.kind='cover') AS has_cover "
        "FROM games g" + clause + _order_by(sort) + " LIMIT ? OFFSET ?",
        args + [limit, offset]).fetchall()
    items = [{
        "norm_key": r["norm_key"],
        "title": r["canonical_title"],
        "n_sources": r["n_sources"],
        "n_kinds": r["n_kinds"],
        "sources_summary": r["sources_summary"],
        "platforms": r["platforms"] or "",
        "matched": bool(r["matched"]),
        "has_cover": bool(r["has_cover"]),
    } for r in rows]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@app.get("/api/games")
def games(
    q: str = Query(None, description="substring match on title"),
    source: str = Query(None, description="filter to games available from this source"),
    platform: str = Query(None, description="filter by platform"),
    has_kind: str = Query(None, description="only games with a chosen asset of this kind"),
    include: str = Query(None, description="comma-list of source/status flags a game MUST have"),
    exclude: str = Query(None, description="comma-list of source/status flags a game must NOT have"),
    sort: str = Query(None, description="comma-list of sort keys in priority order (1st,2nd,3rd)"),
    limit: int = Query(60, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    inc = [f for f in (include or "").split(",") if f]
    exc = [f for f in (exclude or "").split(",") if f]
    srt = [f for f in (sort or "").split(",") if f]
    con = lib()
    try:
        return _query_games(con, q, source, platform, has_kind, inc, exc, srt, limit, offset)
    finally:
        con.close()


@app.get("/api/games/{norm_key}")
def game_detail(norm_key: str):
    con = lib()
    try:
        g = con.execute("SELECT * FROM games WHERE norm_key=?", (norm_key,)).fetchone()
        if not g:
            raise HTTPException(404, "no such game")
        gid = g["id"]
        sources = [dict(r) for r in con.execute(
            "SELECT source, platform, source_id, title_raw, detail "
            "FROM sources WHERE game_id=?", (gid,))]
        attrs = {}
        for r in con.execute("SELECT kind, value FROM game_attributes "
                             "WHERE game_id=?", (gid,)):
            attrs.setdefault(r["kind"], []).append(r["value"])
        links = [dict(r) for r in con.execute(
            "SELECT provider, provider_id, slug, url FROM metadata_links "
            "WHERE game_id=?", (gid,))]
        media_kinds = [r["kind"] for r in con.execute(
            "SELECT DISTINCT kind FROM m.media WHERE norm_key=? AND chosen=1 "
            "ORDER BY kind", (norm_key,))]
        return {
            "norm_key": norm_key,
            "title": g["canonical_title"],
            "sources": sources,
            "attributes": attrs,
            "metadata_links": links,
            "media_kinds": media_kinds,
        }
    finally:
        con.close()


# ------------------------------------------------------------------- media resolver
def _serve(path, ext, size):
    """Return a FileResponse, optionally downscaled to a cached thumbnail."""
    if size == "thumb":
        sha = os.path.splitext(os.path.basename(path))[0]
        tpath = os.path.join(THUMBS, "%s_thumb.%s" % (sha, ext))
        if not os.path.exists(tpath):
            from PIL import Image
            try:
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
    """Resolve + stream the chosen asset for (norm_key, kind).

    1. chosen row with sha1 + repo file present     -> stream it.
    2. chosen URL ref not yet materialized          -> fetch, cache, backfill, stream.
    3. chosen `file` ref (lives only on the producer)-> 404 (push it from the Deck).
    """
    rcon = ro(INDEX_DB)
    try:
        r = rcon.execute(
            "SELECT id, ref_type, ref, ext, sha1, provider FROM media "
            "WHERE norm_key=? AND kind=? AND chosen=1 LIMIT 1",
            (norm_key, kind)).fetchone()
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
        if isinstance(val, dict):
            prov, mdl = val.get("provider", ""), val.get("model", None)
        else:
            prov, mdl = (val or ""), None
        if prov and prov not in ai.PROVIDERS:
            raise HTTPException(400, "unknown provider %r" % prov)
        config.set_("ai_area_" + area_id, prov or "")
        if mdl is not None:
            config.set_("ai_area_" + area_id + "_model", mdl or "")
    return ai.status()


@app.get("/api/ai/models/{provider}")
def ai_models(provider: str, refresh: bool = False):
    """All models the provider's API reports (cached; curated fallback)."""
    if provider not in ai.PROVIDERS:
        raise HTTPException(400, "unknown provider %r" % provider)
    return {"provider": provider, "models": ai.list_models(provider, refresh=refresh)}


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
                          model=ai.model_for_area("art"))
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


def _dedupe_candidates(con, limit=15):
    """Find likely same-game pairs norm_key missed (blocked similarity scan)."""
    import difflib
    rows = con.execute("SELECT norm_key, canonical_title, sources_summary "
                       "FROM games").fetchall()

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


# ---------------------------------------------------------------- static SPA (last)
# Mounted at "/" AFTER all /api routes so the API takes precedence; serves the
# built React app (web/dist) when present.
from fastapi.staticfiles import StaticFiles          # noqa: E402

WEB_DIST = os.path.join(DIR, "web", "dist")
if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
