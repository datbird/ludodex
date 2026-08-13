#!/usr/bin/env python3
"""Collections & compilations — a durable overlay that records which multi-game
COMPILATIONS the user owns and what standalone games each contains, so ownership
can be *credited* (fanned out) to the member games (DESIGN §13).

A compilation ("Sega Genesis Classics", "Sonic Mega Collection") stays its own
catalog entry; this store just maps it to its members. A member is linked by the
normalized title (`titlenorm.norm`) = the catalog's `base_key`, so it resolves to
the standalone entry across rebuilds without an id. Survives catalog rebuilds like
ownership/tags. The per-game ownership CREDIT is computed at read time (server), so
recording a collection here takes effect with no rebuild.
"""
import os
import sqlite3
import time

DIR = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, DIR)
from titlenorm import norm       # noqa: E402  same normalizer as the catalog


def _db(data_dir):
    con = sqlite3.connect(os.path.join(data_dir, "collections.sqlite"))
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS collections(
        coll_key TEXT PRIMARY KEY,   -- the collection's OWN catalog entry (norm_key/base_key)
        name     TEXT NOT NULL,      -- display name, e.g. "Sega Genesis Classics"
        origin   TEXT NOT NULL DEFAULT 'ai',
        updated  REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS collection_members(
        coll_key        TEXT NOT NULL,   -- FK -> collections.coll_key
        member_key      TEXT NOT NULL,   -- norm(member_title) = the member's base_key
        member_title    TEXT NOT NULL,
        member_platform TEXT NOT NULL DEFAULT '',   -- HARDWARE only (DESIGN §11.1)
        member_year     INTEGER,
        origin          TEXT NOT NULL DEFAULT 'ai',
        added           REAL,
        PRIMARY KEY(coll_key, member_key))""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_coll_member ON collection_members(member_key)")
    # OS is its own axis and must never reach `member_platform` — an AI answering
    # "MS-DOS" or "Windows 3.1" to a bare "platform?" question was minting parallel
    # platforms for one machine. Asked separately now, stored separately.
    if not any(r[1] == "member_os" for r in
               con.execute("PRAGMA table_info(collection_members)")):
        con.execute("ALTER TABLE collection_members ADD COLUMN "
                    "member_os TEXT NOT NULL DEFAULT ''")
    # Durable NEGATIVE verdicts. Auto-detection nominates candidates every scan; a
    # nominee the AI judged not-a-collection (or couldn't enumerate) must not be
    # re-nominated — and re-BILLED — on every subsequent run.
    con.execute("""CREATE TABLE IF NOT EXISTS collection_rejected(
        coll_key TEXT PRIMARY KEY,
        reason   TEXT NOT NULL DEFAULT '',
        at       REAL)""")
    # WHO rejected it. An AI verdict is a guess and any later record may clear it; a
    # MANUAL veto is the user's decision and the model may not overturn it. Without
    # this column the two were indistinguishable, so a re-detection quietly undid a
    # removal — which is why removing a collection by hand never stuck.
    if not any(r[1] == "origin" for r in
               con.execute("PRAGMA table_info(collection_rejected)")):
        con.execute("ALTER TABLE collection_rejected ADD COLUMN "
                    "origin TEXT NOT NULL DEFAULT 'ai'")
    return con


def vetoed(data_dir, coll_key):
    """True when the USER has said this entry is not a compilation."""
    con = _db(data_dir)
    try:
        r = con.execute("SELECT 1 FROM collection_rejected WHERE coll_key=? "
                        "AND origin='manual'", ((coll_key or "").strip(),)).fetchone()
        return bool(r)
    finally:
        con.close()


def set_collection(data_dir, coll_key, name, members, origin="ai"):
    """Upsert a collection and REPLACE its member set. `members` = iterable of
    {title, platform?, os?, year?}. A member that normalizes to the collection's own
    key is dropped (a compilation never contains itself). Returns the stored member
    count.

    `platform` is HARDWARE ("Apple II", "Sega Genesis", "PC"); `os` is what ran on it
    ("MS-DOS", "Windows 3.1") and never influences the entry's platform.

    A MANUAL veto (see `clear_collection`) blocks a non-manual record outright: the
    user has ruled that this product is not a compilation, and re-detecting it is the
    model doing the same thing again, not new evidence. The user recording it
    themselves clears the veto — their word decides it in both directions."""
    coll_key = (coll_key or "").strip()
    if not coll_key or not name:
        return 0
    if origin != "manual" and vetoed(data_dir, coll_key):
        return 0
    con = _db(data_dir)
    now = time.time()
    con.execute("DELETE FROM collection_rejected WHERE coll_key=?", (coll_key,))
    con.execute("INSERT OR REPLACE INTO collections(coll_key,name,origin,updated) "
                "VALUES(?,?,?,?)", (coll_key, name, origin, now))
    con.execute("DELETE FROM collection_members WHERE coll_key=?", (coll_key,))
    seen = set()
    n = 0
    for m in members or []:
        title = (m.get("title") or "").strip()
        mkey = norm(title)
        if not mkey or mkey == coll_key or mkey in seen:
            continue                      # blank, self-reference, or dup
        seen.add(mkey)
        yr = m.get("year")
        try:
            yr = int(yr) if yr not in (None, "") else None
        except (TypeError, ValueError):
            yr = None
        con.execute(
            "INSERT INTO collection_members(coll_key,member_key,member_title,"
            "member_platform,member_os,member_year,origin,added) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (coll_key, mkey, title, (m.get("platform") or "").strip(),
             (m.get("os") or "").strip(), yr, m.get("origin") or origin, now))
        n += 1
    con.commit()
    con.close()
    return n


def mark_rejected(data_dir, coll_key, reason="", origin="ai"):
    """Persist a 'not a collection' verdict so auto-detection never re-nominates (and
    re-bills) this candidate.

    `origin='ai'` is a guess — any later record clears it. `origin='manual'` is the
    user's veto: only they can lift it (see `set_collection`)."""
    coll_key = (coll_key or "").strip()
    if not coll_key:
        return
    con = _db(data_dir)
    con.execute("INSERT OR REPLACE INTO collection_rejected(coll_key,reason,at,origin) "
                "VALUES(?,?,?,?)",
                (coll_key, (reason or "")[:200], time.time(), origin or "ai"))
    con.commit()
    con.close()


def rejected_keys(data_dir):
    """coll_keys auto-detection must not re-nominate (see mark_rejected)."""
    con = _db(data_dir)
    try:
        return {r[0] for r in con.execute("SELECT coll_key FROM collection_rejected")}
    finally:
        con.close()


def get_collection(data_dir, coll_key):
    """The collection rooted at `coll_key` (its own entry), or None if that entry
    isn't a recorded collection. Includes its member list."""
    con = _db(data_dir)
    try:
        c = con.execute("SELECT coll_key,name,origin FROM collections WHERE coll_key=?",
                        (coll_key,)).fetchone()
        if not c:
            return None
        members = [dict(r) for r in con.execute(
            "SELECT member_key,member_title,member_platform,member_os,member_year,origin "
            "FROM collection_members WHERE coll_key=? ORDER BY member_title",
            (coll_key,))]
        return {"coll_key": c["coll_key"], "name": c["name"],
                "origin": c["origin"], "members": members}
    finally:
        con.close()


def is_collection(data_dir, coll_key):
    con = _db(data_dir)
    try:
        return con.execute("SELECT 1 FROM collections WHERE coll_key=?",
                           (coll_key,)).fetchone() is not None
    finally:
        con.close()


def credits_for(data_dir, member_key):
    """Every collection that CONTAINS `member_key` (a game's base_key): a list of
    {coll_key, name, member_platform}. The caller checks whether each collection is
    actually owned before crediting."""
    con = _db(data_dir)
    try:
        return [dict(coll_key=r["coll_key"], name=r["name"],
                     member_platform=r["member_platform"])
                for r in con.execute(
                    "SELECT c.coll_key, c.name, m.member_platform "
                    "FROM collection_members m JOIN collections c "
                    "ON c.coll_key=m.coll_key WHERE m.member_key=?", (member_key,))]
    finally:
        con.close()


def all_collections(data_dir):
    con = _db(data_dir)
    try:
        return [dict(coll_key=r["coll_key"], name=r["name"], origin=r["origin"],
                     n_members=r["n"]) for r in con.execute(
            "SELECT c.coll_key, c.name, c.origin, "
            "(SELECT COUNT(*) FROM collection_members m WHERE m.coll_key=c.coll_key) n "
            "FROM collections c ORDER BY c.name")]
    finally:
        con.close()


def clear_collection(data_dir, coll_key, reason="", origin=""):
    """Remove a collection and its member list.

    With `origin='manual'` the removal is also RECORDED as a veto, because otherwise it
    is forgotten: auto-detection checks `rejected_keys()` before nominating, sees a key
    it has never judged, and proposes the same bundle back on the next scan. Some
    products only a person can rule on — Retro Game Crunch presents exactly like a
    compilation, but its seven mini-games have no standalone releases — and the model
    will never reach that from the title."""
    con = _db(data_dir)
    con.execute("DELETE FROM collection_members WHERE coll_key=?", (coll_key,))
    con.execute("DELETE FROM collections WHERE coll_key=?", (coll_key,))
    con.commit()
    con.close()
    if origin == "manual":
        mark_rejected(data_dir, coll_key, reason or "removed by hand", origin="manual")
