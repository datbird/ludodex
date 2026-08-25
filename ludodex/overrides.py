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


def all_overrides():
    """{norm_key: {kind: {value, origin}}} — for the build_library merge."""
    con = _con()
    out = {}
    for r in con.execute("SELECT norm_key, kind, value, origin FROM overrides"):
        out.setdefault(r["norm_key"], {})[r["kind"]] = {"value": r["value"],
                                                        "origin": r["origin"]}
    con.close()
    return out
