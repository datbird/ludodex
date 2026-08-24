#!/usr/bin/env python3
"""Per-game, per-image-kind framing — how one image is positioned + zoomed inside
its display viewport (the detail hero for a 'background', the poster tile for a
'cover', etc.). Provider art rarely crops the way you'd want for a given frame,
so this is a durable manual overlay applied at RENDER time (never baked into the
catalog), keyed by norm_key so it survives rebuilds.

A frame = four edge insets (% of the viewport; negative bleeds past the edge to
crop, positive letterboxes) plus a zoom (0.1–5.0, i.e. 10%–500%).
"""
import os
import sqlite3

FIELDS = ("top", "right", "bottom", "left", "zoom")

# Files whose schema this process has already settled. `_db()` ran two CREATEs, two
# PRAGMA table_info scans, up to seven ALTERs and a commit on EVERY call — and
# `get_all`/`for_keys` call it per request, so drawing one page of the library grid meant
# a write transaction against framing.sqlite. The heal itself is still needed (the
# backing-store sync can recreate a table from only the columns the remote data held), so
# it is not removed; it just stops being repeated. Per PATH, not global: the same process
# opens more than one data dir in the tests and in a device export.
_HEALED = set()


def _db(data_dir):
    path = os.path.join(data_dir, "framing.sqlite")
    con = sqlite3.connect(path)
    if path in _HEALED:
        return con
    _ensure(con)
    _HEALED.add(path)
    return con


def _ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS framing(
        norm_key TEXT NOT NULL, kind TEXT NOT NULL,
        m_top REAL DEFAULT 0, m_right REAL DEFAULT 0,
        m_bottom REAL DEFAULT 0, m_left REAL DEFAULT 0,
        zoom REAL DEFAULT 1,
        PRIMARY KEY(norm_key, kind))""")
    # Heal a legacy / externally-created table that is missing the geometry columns.
    # The backing-store sync (dbsync._local_apply) recreates a table from ONLY the
    # columns present in the remote data, so if framing was pulled before any frame
    # was set it lands as just (norm_key, kind) — and CREATE TABLE IF NOT EXISTS
    # above can't add columns to a table that already exists. This module owns the
    # framing schema, so it is the right place to reconcile it. Idempotent.
    have = {r[1] for r in con.execute("PRAGMA table_info(framing)")}
    for col, default in (("m_top", 0), ("m_right", 0), ("m_bottom", 0),
                         ("m_left", 0), ("zoom", 1)):
        if col not in have:
            con.execute("ALTER TABLE framing ADD COLUMN %s REAL DEFAULT %s"
                        % (col, default))
    # per-game hero override — which media drives the detail hero (same "durable
    # render overlay" idea as framing, so it lives in the same store). Value is
    # 'marquee' (force the scrolling dance) or a media kind to force as the static
    # background; absent = 'auto' (the default hero/background/header → marquee logic).
    con.execute("""CREATE TABLE IF NOT EXISTS hero_pref(
        norm_key TEXT PRIMARY KEY, source TEXT NOT NULL)""")
    # Same backing-store-strip heal as `framing` above: a narrow pull can recreate
    # hero_pref as just (norm_key), dropping `source`. Empty → rebuild; else add it.
    hp = {r[1] for r in con.execute("PRAGMA table_info(hero_pref)")}
    if "source" not in hp:
        if con.execute("SELECT COUNT(*) FROM hero_pref").fetchone()[0] == 0:
            con.execute("DROP TABLE hero_pref")
            con.execute("CREATE TABLE hero_pref(norm_key TEXT PRIMARY KEY, "
                        "source TEXT NOT NULL)")
        else:
            con.execute("ALTER TABLE hero_pref ADD COLUMN source TEXT NOT NULL DEFAULT ''")
    con.commit()
    return con


def _row(t, r, b, l, z):
    return {"top": t, "right": r, "bottom": b, "left": l, "zoom": z}


def get_all(data_dir, norm_key):
    """{kind: frame} for one game — for the detail view."""
    con = _db(data_dir)
    try:
        return {k: _row(t, r, b, l, z) for k, t, r, b, l, z in con.execute(
            "SELECT kind,m_top,m_right,m_bottom,m_left,zoom FROM framing "
            "WHERE norm_key=?", (norm_key,))}
    finally:
        con.close()


def for_keys(data_dir, norm_keys, kind):
    """{norm_key: frame} for one kind across many games — for the library grid."""
    norm_keys = list(norm_keys)
    if not norm_keys:
        return {}
    con = _db(data_dir)
    try:
        out = {}
        for i in range(0, len(norm_keys), 400):        # chunk to stay under SQLite's var cap
            batch = norm_keys[i:i + 400]
            ph = ",".join("?" * len(batch))
            for nk, t, r, b, l, z in con.execute(
                    "SELECT norm_key,m_top,m_right,m_bottom,m_left,zoom FROM framing "
                    "WHERE kind=? AND norm_key IN (%s)" % ph, [kind] + batch):
                out[nk] = _row(t, r, b, l, z)
        return out
    finally:
        con.close()


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def set_frame(data_dir, norm_key, kind, top=0, right=0, bottom=0, left=0, zoom=1.0):
    top = _clamp(top, -100, 100, 0); right = _clamp(right, -100, 100, 0)
    bottom = _clamp(bottom, -100, 100, 0); left = _clamp(left, -100, 100, 0)
    zoom = _clamp(zoom, 0.1, 5.0, 1.0)
    con = _db(data_dir)
    try:
        # an all-default frame is a no-op — drop the row so it reads as "unframed"
        if not top and not right and not bottom and not left and abs(zoom - 1) < 1e-6:
            con.execute("DELETE FROM framing WHERE norm_key=? AND kind=?", (norm_key, kind))
        else:
            con.execute("INSERT OR REPLACE INTO framing"
                        "(norm_key,kind,m_top,m_right,m_bottom,m_left,zoom) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (norm_key, kind, top, right, bottom, left, zoom))
        con.commit()
    finally:
        con.close()
    return _row(top, right, bottom, left, zoom)


def clear(data_dir, norm_key, kind):
    con = _db(data_dir)
    try:
        con.execute("DELETE FROM framing WHERE norm_key=? AND kind=?", (norm_key, kind))
        con.commit()
    finally:
        con.close()


def get_hero(data_dir, norm_key):
    """The hero override for one game: 'marquee', a media kind, or None (= auto)."""
    con = _db(data_dir)
    try:
        r = con.execute("SELECT source FROM hero_pref WHERE norm_key=?",
                        (norm_key,)).fetchone()
        return r[0] if r else None
    finally:
        con.close()


def set_hero(data_dir, norm_key, source):
    """Set/clear the hero override. Empty, 'auto' or anything that is not a media KIND
    clears it (back to default), and the cleared value is what comes back.

    The value is read as a media kind and used to choose what the detail hero displays,
    so a typo or a retired kind name used to be stored happily and then silently do
    nothing — which reads to a user as the feature being broken rather than the input
    being wrong. media.KINDS is the vocabulary; validating against it here means there is
    no second list to keep in step."""
    import media
    if source and source not in media.KINDS and source != "auto":
        source = None                     # not a kind: refuse it rather than store a no-op
    con = _db(data_dir)
    try:
        if not source or source == "auto":
            con.execute("DELETE FROM hero_pref WHERE norm_key=?", (norm_key,))
            source = None
        else:
            con.execute("INSERT OR REPLACE INTO hero_pref(norm_key,source) VALUES(?,?)",
                        (norm_key, str(source)))
        con.commit()
    finally:
        con.close()
    return source
