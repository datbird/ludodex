#!/usr/bin/env python3
"""AI metadata audit & supplement — durable store + scan context.

Builds, per game, the context the `metadata` AI area reasons over (title, systems,
sources, current provider match, known attributes, and which attributes are
missing), and persists the AI's *findings* in a durable overlay
(`ai-metadata.sqlite`) that survives catalog rebuilds. Findings are proposals the
user reviews (accept/reject); accepted supplements surface in the game detail and
can be baked into the catalog on the next build (fill-gaps, lowest precedence).

This module does the sqlite work only — the AI call itself lives in server/ai.py
(`analyze_game`) and is orchestrated by the server's scan job.
"""
import json
import os
import sqlite3
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "ai-metadata.sqlite")
LIBRARY_DB = os.path.join(DIR, "game-library.sqlite")
CACHE_DB = os.path.join(DIR, "metadata-cache.sqlite")
MEDIA_INDEX = os.path.join(DIR, "media-index.sqlite")

# Factual attributes the model can reasonably supply; scores/subjective kinds are
# deliberately excluded. Holes in these drive the "missing" list + "missing" target.
SUPPLEMENT_KINDS = ["release_year", "genres", "developers", "publishers",
                    "description", "themes", "game_modes", "player_perspectives"]


# ------------------------------------------------------------------- durable store
def _con():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS findings(
        id INTEGER PRIMARY KEY, run_id INTEGER, norm_key TEXT, title TEXT,
        kind TEXT, status TEXT DEFAULT 'proposed', payload_json TEXT,
        confidence REAL, model TEXT, created REAL, selection_json TEXT)""")
    if "selection_json" not in {r[1] for r in
                                con.execute("PRAGMA table_info(findings)")}:
        con.execute("ALTER TABLE findings ADD COLUMN selection_json TEXT")
    con.execute("""CREATE TABLE IF NOT EXISTS scan_runs(
        id INTEGER PRIMARY KEY, target TEXT, total INTEGER, done INTEGER DEFAULT 0,
        findings INTEGER DEFAULT 0, status TEXT, created REAL, finished REAL,
        web INTEGER DEFAULT 0, match_provider INTEGER DEFAULT 0, keys_json TEXT)""")
    cols = {r[1] for r in con.execute("PRAGMA table_info(scan_runs)")}
    for col, decl in (("web", "INTEGER DEFAULT 0"),
                      ("match_provider", "INTEGER DEFAULT 0"),
                      ("keys_json", "TEXT")):
        if col not in cols:
            con.execute("ALTER TABLE scan_runs ADD COLUMN %s %s" % (col, decl))
    con.execute("CREATE INDEX IF NOT EXISTS ix_find_nk ON findings(norm_key)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_find_status ON findings(status)")
    return con


def _lib():
    con = sqlite3.connect("file:%s?mode=ro" % LIBRARY_DB, uri=True)
    con.row_factory = sqlite3.Row
    return con


# ----------------------------------------------------------------- game context
def _match_info(gid, lib):
    """Current provider (IGDB) match as {title, year, slug} — or None if unmatched."""
    ml = lib.execute("SELECT provider_id, slug FROM metadata_links "
                     "WHERE game_id=? AND provider='igdb'", (gid,)).fetchone()
    if not ml:
        return None
    info = {"slug": ml["slug"] or "", "title": None, "year": None}
    if os.path.exists(CACHE_DB):
        try:
            mc = sqlite3.connect(CACHE_DB)
            row = mc.execute("SELECT payload_json FROM igdb_meta WHERE igdb_id=?",
                             (int(ml["provider_id"]),)).fetchone()
            mc.close()
            if row:
                rec = json.loads(row[0])
                info["title"] = rec.get("name")
                frd = rec.get("first_release_date")
                if frd:
                    info["year"] = time.gmtime(int(frd)).tm_year
        except Exception:
            pass
    return info


# media kinds worth reporting coverage/gaps on (for the AI's cross-reference)
KEY_MEDIA = ["cover", "background", "logo", "screenshot", "video", "marquee"]


def _media_by_provider(norm_key):
    """{provider: [kinds]} of media indexed for a game, so the AI can see what
    each provider (ScreenScraper, IGDB, Steam, …) already supplies + the gaps."""
    if not os.path.exists(MEDIA_INDEX):
        return {"by_provider": {}, "have": [], "missing": KEY_MEDIA}
    out = {}
    have = set()
    try:
        mi = sqlite3.connect(MEDIA_INDEX)
        for kind, prov in mi.execute("SELECT DISTINCT kind, provider FROM media "
                                     "WHERE norm_key=?", (norm_key,)):
            out.setdefault(prov or "?", set()).add(kind)
            have.add(kind)
        mi.close()
    except Exception:
        return {"by_provider": {}, "have": [], "missing": KEY_MEDIA}
    return {"by_provider": {p: sorted(k) for p, k in out.items()},
            "have": sorted(have), "missing": [k for k in KEY_MEDIA if k not in have]}


def game_context(norm_key, lib=None):
    """Assemble the AI context dict for one game (or None if it's gone)."""
    own = lib or _lib()
    try:
        g = own.execute("SELECT id, canonical_title FROM games WHERE norm_key=?",
                        (norm_key,)).fetchone()
        if not g:
            return None
        gid = g["id"]
        have = {}
        by_source = {}            # origin -> set(kinds) — who supplied what
        for r in own.execute("SELECT kind, value, origin FROM game_attributes "
                             "WHERE game_id=?", (gid,)):
            have.setdefault(r["kind"], []).append(r["value"])
            for o in (r["origin"] or "").split(","):
                o = o.strip()
                if o:
                    by_source.setdefault(o, set()).add(r["kind"])
        systems, sources = [], []
        for r in own.execute("SELECT DISTINCT source, platform FROM sources "
                             "WHERE game_id=?", (gid,)):
            if r["platform"] and r["platform"] not in systems:
                systems.append(r["platform"])
            if r["source"] and r["source"] not in sources:
                sources.append(r["source"])
        missing = [k for k in SUPPLEMENT_KINDS if k not in have]
        return {"norm_key": norm_key, "title": g["canonical_title"],
                "systems": systems, "sources": sources, "have": have,
                "missing": missing, "match": _match_info(gid, own),
                "by_source": {k: sorted(v) for k, v in by_source.items()},
                "media": _media_by_provider(norm_key)}
    finally:
        if lib is None:
            own.close()


def targets(target="unmatched", limit=200):
    """norm_keys to scan for a target set: 'unmatched' (no provider link),
    'matched' (verify existing matches), 'missing' (holes in SUPPLEMENT_KINDS),
    or 'all'."""
    lib = _lib()
    try:
        if target == "unmatched":
            q = ("SELECT g.norm_key FROM games g WHERE NOT EXISTS("
                 "SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id)")
            args = []
        elif target == "matched":
            q = ("SELECT g.norm_key FROM games g WHERE EXISTS("
                 "SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id)")
            args = []
        elif target == "missing":
            ph = ",".join("?" * len(SUPPLEMENT_KINDS))
            q = ("SELECT g.norm_key FROM games g WHERE (SELECT COUNT(DISTINCT kind) "
                 "FROM game_attributes ga WHERE ga.game_id=g.id AND ga.kind IN (%s)) "
                 "< ?" % ph)
            args = SUPPLEMENT_KINDS + [len(SUPPLEMENT_KINDS)]
        else:                                   # all
            q, args = "SELECT g.norm_key FROM games g", []
        rows = lib.execute(q + " ORDER BY g.norm_key LIMIT ?", args + [limit]).fetchall()
        return [r[0] for r in rows]
    finally:
        lib.close()


def target_count(target="unmatched"):
    lib = _lib()
    try:
        if target == "unmatched":
            n = lib.execute("SELECT COUNT(*) FROM games g WHERE NOT EXISTS("
                            "SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id)"
                            ).fetchone()[0]
        elif target == "matched":
            n = lib.execute("SELECT COUNT(*) FROM games g WHERE EXISTS("
                            "SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id)"
                            ).fetchone()[0]
        elif target == "missing":
            ph = ",".join("?" * len(SUPPLEMENT_KINDS))
            n = lib.execute("SELECT COUNT(*) FROM games g WHERE (SELECT COUNT(DISTINCT "
                            "kind) FROM game_attributes ga WHERE ga.game_id=g.id AND "
                            "ga.kind IN (%s)) < ?" % ph,
                            SUPPLEMENT_KINDS + [len(SUPPLEMENT_KINDS)]).fetchone()[0]
        else:
            n = lib.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        return n
    finally:
        lib.close()


# ------------------------------------------------------------------- findings I/O
def store_finding(run_id, ctx, result, model=""):
    """Persist an actionable finding from analyze_game()'s result. Returns the
    finding kind, or None when nothing actionable (verified-ok, no gaps)."""
    match = result.get("match") or {}
    attrs = {k: v for k, v in (result.get("attributes") or {}).items()
             if k in SUPPLEMENT_KINDS and v not in (None, "", [], {})}
    status_m = (match.get("status") or "").lower()
    actionable = bool(attrs) or status_m in ("wrong", "unmatched", "unsure")
    if not actionable:
        return None
    kind = ("match" if status_m in ("wrong", "unsure")
            else "identify" if status_m == "unmatched" or not ctx.get("match")
            else "supplement")
    payload = {"match": match, "attributes": attrs,
               "notes": result.get("notes", ""),
               "current_match": ctx.get("match"), "missing": ctx.get("missing"),
               "sources": result.get("sources") or [], "web": bool(result.get("web")),
               "provider_match": result.get("provider_match"),   # IGDB hit (compat)
               "provider_matches": result.get("provider_matches") or []}  # all providers
    con = _con()
    # a re-scan supersedes an earlier *un-reviewed* finding for the same game;
    # accepted/rejected findings are the user's decision and are left alone.
    con.execute("DELETE FROM findings WHERE norm_key=? AND status='proposed'",
                (ctx["norm_key"],))
    con.execute("INSERT INTO findings(run_id,norm_key,title,kind,status,payload_json,"
                "confidence,model,created) VALUES(?,?,?,?, 'proposed', ?,?,?,?)",
                (run_id, ctx["norm_key"], ctx["title"], kind,
                 json.dumps(payload), float(match.get("confidence") or 0), model,
                 time.time()))
    con.commit()
    con.close()
    return kind


def _finding_row(r):
    d = dict(r)
    d["payload"] = json.loads(d.pop("payload_json") or "{}")
    d["selection"] = json.loads(d.pop("selection_json", None) or "null")
    return d


def apply_selection(selections):
    """Mark the chosen changes for application. `selections` = [{finding_id,
    attributes:[kinds]|null (null = all), match:bool}]. Each named finding becomes
    'accepted' carrying its selection; unlisted findings are left as-is."""
    con = _con()
    for sel in selections or []:
        fid = sel.get("finding_id")
        if not fid:
            continue
        selj = json.dumps({"attributes": sel.get("attributes"),
                           "match": bool(sel.get("match", True))})
        con.execute("UPDATE findings SET status='accepted', selection_json=? "
                    "WHERE id=?", (selj, int(fid)))
    con.commit()
    con.close()


def findings_list(status=None, kind=None, limit=300):
    con = _con()
    q = "SELECT * FROM findings"
    cond, args = [], []
    if status:
        cond.append("status=?")
        args.append(status)
    if kind:
        cond.append("kind=?")
        args.append(kind)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY confidence DESC, created DESC LIMIT ?"
    args.append(limit)
    rows = [_finding_row(r) for r in con.execute(q, args)]
    con.close()
    return rows


def findings_counts():
    con = _con()
    out = {}
    for r in con.execute("SELECT kind, status, COUNT(*) n FROM findings "
                         "GROUP BY kind, status"):
        out.setdefault(r["kind"], {})[r["status"]] = r["n"]
    con.close()
    return out


def set_status(finding_id, status):
    if status not in ("proposed", "accepted", "rejected"):
        raise ValueError("bad status")
    con = _con()
    con.execute("UPDATE findings SET status=? WHERE id=?", (status, finding_id))
    con.commit()
    con.close()


def finding_for(norm_key):
    """The most relevant non-rejected finding for a game (for the detail view)."""
    con = _con()
    r = con.execute("SELECT * FROM findings WHERE norm_key=? AND status!='rejected' "
                    "ORDER BY (status='accepted') DESC, confidence DESC, created DESC "
                    "LIMIT 1", (norm_key,)).fetchone()
    con.close()
    return _finding_row(r) if r else None


def accepted_supplements():
    """All accepted supplement attributes as {norm_key: {kind: value}} for the
    build_library fill-gaps merge."""
    con = _con()
    out = {}
    for r in con.execute("SELECT norm_key, payload_json, selection_json FROM "
                         "findings WHERE status='accepted'"):
        attrs = (json.loads(r["payload_json"] or "{}").get("attributes") or {})
        sel = json.loads(r["selection_json"] or "null")
        if sel and sel.get("attributes") is not None:   # only the ticked kinds
            attrs = {k: v for k, v in attrs.items() if k in sel["attributes"]}
        if attrs:
            out[r["norm_key"]] = attrs
    con.close()
    return out


def _accepted_matches_raw():
    """Every provider match on accepted findings whose match was selected — each
    as {norm_key, provider, igdb_id?, ss_id?, system?}. Reads both the legacy
    single provider_match and the provider_matches list."""
    con = _con()
    out = []
    for r in con.execute("SELECT norm_key, payload_json, selection_json FROM "
                         "findings WHERE status='accepted'"):
        pl = json.loads(r["payload_json"] or "{}")
        sel = json.loads(r["selection_json"] or "null")
        if sel and not sel.get("match", True):
            continue
        pms = list(pl.get("provider_matches") or [])
        if not pms and pl.get("provider_match"):        # legacy single (IGDB)
            pms = [dict(pl["provider_match"], provider="igdb")]
        for pm in pms:
            prov = pm.get("provider") or ("igdb" if pm.get("igdb_id") else None)
            if prov:
                out.append({"norm_key": r["norm_key"], "provider": prov,
                            "igdb_id": pm.get("igdb_id"), "ss_id": pm.get("ss_id"),
                            "system": pm.get("system")})
    con.close()
    return out


def accepted_provider_matches():
    """Accepted IGDB matches — [{norm_key, igdb_id}] — for igdb_resolution."""
    return [{"norm_key": m["norm_key"], "igdb_id": int(m["igdb_id"])}
            for m in _accepted_matches_raw()
            if m["provider"] == "igdb" and m.get("igdb_id")]


def accepted_ss_matches():
    """Accepted ScreenScraper matches — [{norm_key, ss_id, system}] — to fetch +
    cache into ss_game so a rebuild links them and pulls SS's rich media."""
    return [{"norm_key": m["norm_key"], "ss_id": int(m["ss_id"]),
             "system": m.get("system") or ""}
            for m in _accepted_matches_raw()
            if m["provider"] == "screenscraper" and m.get("ss_id")]


# --------------------------------------------------------------------- scan runs
def scan_new(target, keys, web=0, match_provider=0):
    con = _con()
    cur = con.execute("INSERT INTO scan_runs(target,total,web,match_provider,"
                      "keys_json,status,created) VALUES(?,?,?,?,?, 'running', ?)",
                      (target, len(keys), 1 if web else 0, 1 if match_provider else 0,
                       json.dumps(keys), time.time()))
    rid = cur.lastrowid
    con.commit()
    con.close()
    return rid


def scan_progress(run_id, done, findings):
    con = _con()
    con.execute("UPDATE scan_runs SET done=?, findings=? WHERE id=?",
                (done, findings, run_id))
    con.commit()
    con.close()


def scan_finish(run_id, status):
    con = _con()
    con.execute("UPDATE scan_runs SET status=?, finished=? WHERE id=?",
                (status, time.time(), run_id))
    con.commit()
    con.close()


def scan_get(run_id):
    con = _con()
    r = con.execute("SELECT * FROM scan_runs WHERE id=?", (run_id,)).fetchone()
    con.close()
    if not r:
        return None
    d = dict(r)
    d["keys"] = json.loads(d.get("keys_json") or "[]")
    return d


def scan_delete(run_id):
    """Drop a scan run record (findings are kept — they're the valuable output)."""
    con = _con()
    con.execute("DELETE FROM scan_runs WHERE id=?", (run_id,))
    con.commit()
    con.close()


def scans_list(limit=20):
    con = _con()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM scan_runs ORDER BY created DESC LIMIT ?", (limit,))]
    con.close()
    return rows
