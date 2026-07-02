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
        confidence REAL, model TEXT, created REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS scan_runs(
        id INTEGER PRIMARY KEY, target TEXT, total INTEGER, done INTEGER DEFAULT 0,
        findings INTEGER DEFAULT 0, status TEXT, created REAL, finished REAL)""")
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
        for r in own.execute("SELECT kind, value FROM game_attributes WHERE game_id=?",
                             (gid,)):
            have.setdefault(r["kind"], []).append(r["value"])
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
                "missing": missing, "match": _match_info(gid, own)}
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
               "current_match": ctx.get("match"), "missing": ctx.get("missing")}
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
    return d


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
    for r in con.execute("SELECT norm_key, payload_json FROM findings "
                         "WHERE status='accepted'"):
        attrs = (json.loads(r["payload_json"] or "{}").get("attributes") or {})
        if attrs:
            out[r["norm_key"]] = attrs
    con.close()
    return out


# --------------------------------------------------------------------- scan runs
def scan_new(target, total):
    con = _con()
    cur = con.execute("INSERT INTO scan_runs(target,total,status,created) "
                      "VALUES(?,?, 'running', ?)", (target, total, time.time()))
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
    return dict(r) if r else None


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
