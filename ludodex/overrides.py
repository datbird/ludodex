#!/usr/bin/env python3
"""Per-attribute provenance overrides — the user's manual re-pointing of a game's
canonical attribute value to a specific source (another provider) or a hand-typed
value. Durable (`attr-overrides.sqlite`), applied on top of the built catalog at
query time so it survives rebuilds and never fights the regenerable output DB.

One override per (norm_key, kind): it names the value the user wants treated as
canonical for that attribute, plus where it came from (a provider id, or 'manual').
"""
import os
import sqlite3
import time

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
DB = os.path.join(DATA, "attr-overrides.sqlite")


def _con():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS overrides(
        norm_key TEXT, kind TEXT, value TEXT, origin TEXT, created REAL,
        PRIMARY KEY(norm_key, kind))""")
    # Heal a backing-store-stripped table (a narrow pull can drop the non-key
    # columns — here value/origin/created — which would break every attribute read
    # and the bulk attribute editor). PK matches the sync key, so ALTER-add suffices.
    have = {r[1] for r in con.execute("PRAGMA table_info(overrides)")}
    for col, decl in (("value", "TEXT"), ("origin", "TEXT"), ("created", "REAL"),
                      ("set_by", "TEXT")):
        if col not in have:
            con.execute("ALTER TABLE overrides ADD COLUMN %s %s" % (col, decl))
    con.commit()
    return con


def set_override(norm_key, kind, value, origin="manual", by="user"):
    """Record an override. Returns True if it was written, False if it was refused.

    `origin` names where the VALUE came from ('manual', 'igdb', 'screenscraper', ...).
    `by` names who chose it: 'user' for anything a person asked for, 'auto' for anything
    a pass decided on its own. They are different questions, and only the second one can
    answer "may this write win": a user who picks IGDB's release year in the UI stores
    origin='igdb', which is exactly what the attribute adjudicator stores when it decides
    the same thing without being asked.

    AN AUTOMATIC WRITE NEVER OVERWRITES A USER'S. This table is where the user's
    corrections live, and the wand's consensus pass used to replace them silently, with
    no undo and no trace — the review page then showed the machine's answer as the
    user's own. Refusing is the only safe direction; the pass has nothing to lose.

    Rows written before `set_by` existed are read as 'user' when their origin is manual
    (a hand-typed value, protect it) and 'auto' otherwise."""
    if not (norm_key and kind) or value in (None, ""):
        raise ValueError("norm_key, kind and value are required")
    by = "user" if (by or "user") != "auto" else "auto"
    con = _con()
    try:
        if by == "auto":
            prev = con.execute("SELECT origin, set_by FROM overrides WHERE norm_key=? "
                               "AND kind=?", (norm_key, kind)).fetchone()
            if prev is not None:
                was = prev["set_by"] or ("user" if (prev["origin"] or "") == "manual"
                                         else "auto")
                if was == "user":
                    return False
        con.execute(
            "INSERT INTO overrides(norm_key,kind,value,origin,created,set_by) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(norm_key,kind) DO UPDATE SET "
            "value=excluded.value, origin=excluded.origin, created=excluded.created, "
            "set_by=excluded.set_by",
            (norm_key, kind, str(value), origin or "manual", time.time(), by))
        con.commit()
    finally:
        con.close()
    return True


def clear_override(norm_key, kind):
    con = _con()
    con.execute("DELETE FROM overrides WHERE norm_key=? AND kind=?", (norm_key, kind))
    con.commit()
    con.close()


def set_overrides(rows, origin="manual", by="user"):
    """`rows` = [(norm_key, kind, value)]. ONE connection, ONE commit.

    The bulk attribute editor called set_override per key, and that function opens and
    closes its own connection each time, so a 20,000-key edit did 20,000 connect+commit
    cycles inside one synchronous request. Returns the number of rows WRITTEN, which is
    not the number offered: the same rule applies as for a single write, so an automatic
    pass still cannot overwrite a value a person chose."""
    by = "user" if (by or "user") != "auto" else "auto"
    rows = [(nk, k, v) for nk, k, v in (rows or [])
            if nk and k and v not in (None, "")]
    if not rows:
        return 0
    con = _con()
    try:
        if by == "auto":
            keep = set()
            for nk, k, _v in rows:
                prev = con.execute("SELECT origin, set_by FROM overrides WHERE "
                                   "norm_key=? AND kind=?", (nk, k)).fetchone()
                if prev is not None:
                    was = prev["set_by"] or ("user" if (prev["origin"] or "") == "manual"
                                             else "auto")
                    if was == "user":
                        keep.add((nk, k))
            rows = [r for r in rows if (r[0], r[1]) not in keep]
            if not rows:
                return 0
        now = time.time()
        con.executemany(
            "INSERT INTO overrides(norm_key,kind,value,origin,created,set_by) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(norm_key,kind) DO UPDATE SET "
            "value=excluded.value, origin=excluded.origin, created=excluded.created, "
            "set_by=excluded.set_by",
            [(nk, k, str(v), origin or "manual", now, by) for nk, k, v in rows])
        con.commit()
    finally:
        con.close()
    return len(rows)


def clear_overrides(norm_keys, kind):
    """Same, for the clear path. Returns the number of keys cleared."""
    keys = [k for k in (norm_keys or []) if k]
    if not (keys and kind):
        return 0
    con = _con()
    try:
        con.executemany("DELETE FROM overrides WHERE norm_key=? AND kind=?",
                        [(k, kind) for k in keys])
        con.commit()
    finally:
        con.close()
    return len(keys)


def overrides_for(norm_key):
    """{kind: {value, origin}} of the user's chosen canonical values for a game."""
    con = _con()
    out = {r["kind"]: {"value": r["value"], "origin": r["origin"]}
           for r in con.execute("SELECT kind, value, origin FROM overrides "
                                "WHERE norm_key=?", (norm_key,))}
    con.close()
    return out


def user_override(norm_key, kind):
    """The value a PERSON chose for this attribute, or None if only a pass wrote one.

    `overrides_for` answers "what is the canonical value", which is the right question
    for display and for the build merge: there, a value the wand's consensus pass settled
    on is as good as a hand-typed one.

    It is the wrong question for the acceptance gate. `matchgate.game_era` lets a user
    widen a game's era downward — that is how Akalabeth's 1979 Apple II release gets
    stated at all, since IGDB's record starts at 1998 — and an automatic write must never
    reach that path. An override the consensus pass derived from ScreenScraper is just
    ScreenScraper's year wearing a different hat, so honouring it would let a provider's
    year become the evidence that its own year is right. That is the circularity the era
    rule exists to refuse, and it is how Resident Evil 4 (2023) would license the 2005
    GameCube record it once wore.

    Same legacy read as `set_override`: rows written before `set_by` existed count as a
    person's when their origin is 'manual', and as a pass's otherwise.

    READ-ONLY, and it never creates the database. `_con()` runs DDL and a commit on every
    open, which is the connect-per-row cost `set_overrides` exists to avoid, and the gate
    asks this once per provider row. Reading an era must also not have a side effect on
    disk: an absent file is simply an absent answer, not a table to go and create.
    """
    if not (norm_key and kind) or not os.path.exists(DB):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    con.row_factory = sqlite3.Row
    try:
        r = con.execute("SELECT value, origin, set_by FROM overrides WHERE norm_key=? "
                        "AND kind=?", (norm_key, kind)).fetchone()
    except sqlite3.OperationalError:      # no table, or one stripped of these columns
        return None
    finally:
        con.close()
    if r is None:
        return None
    by = r["set_by"] or ("user" if (r["origin"] or "") == "manual" else "auto")
    return (r["value"] or None) if by == "user" else None


def all_overrides():
    """{norm_key: {kind: {value, origin}}} — for the build_library merge."""
    con = _con()
    out = {}
    for r in con.execute("SELECT norm_key, kind, value, origin FROM overrides"):
        out.setdefault(r["norm_key"], {})[r["kind"]] = {"value": r["value"],
                                                        "origin": r["origin"]}
    con.close()
    return out
