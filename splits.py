#!/usr/bin/env python3
"""Durable game splits ("peel apart") — the inverse of merges.py.

Sometimes two genuinely DIFFERENT games share a name (a remake / re-release —
"Uno" 2006 vs 2016, "Tomb Raider" 1996 vs 2013) and, because titlenorm maps both
to the same norm_key, they collapse into ONE catalog entry that quietly holds
sources from both games. A split records that specific SOURCE ROWS — keyed by
(source, source_id) — belong to a peeled-off game with its own norm_key + title,
so build_library routes them to a separate entry on EVERY rebuild.

This is intentionally per-source-row (not per-title): the un-peeled sources stay
on the original entry, only the selected rows move. The peeled entry is then
identified normally (title / IGDB match) like any other game.
"""
import os
import sqlite3
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
DB = os.path.join(DATA, "splits.sqlite")


def _con():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS peels(
        source TEXT NOT NULL, source_id TEXT NOT NULL,
        to_key TEXT NOT NULL, to_title TEXT,
        from_key TEXT, created REAL,
        PRIMARY KEY(source, source_id))""")
    return con


def overrides():
    """{(source, source_id): (to_key, to_title)} for build_library — each peeled
    source row is forced onto its own norm_key + title instead of the natural one."""
    if not os.path.exists(DB):
        return {}
    con = _con()
    out = {(r[0], str(r[1])): (r[2], r[3])
           for r in con.execute("SELECT source, source_id, to_key, to_title FROM peels")}
    con.close()
    return out


def add(source, source_id, to_key, to_title="", from_key=""):
    """Peel one source row onto to_key. to_key MUST differ from the row's current
    (from) key — otherwise nothing is actually separated."""
    if not source or source_id in (None, "") or not to_key:
        raise ValueError("bad peel: need source, source_id and a target key")
    if to_key == from_key:
        raise ValueError("the peeled game needs a distinct title/year")
    con = _con()
    con.execute(
        "INSERT INTO peels(source,source_id,to_key,to_title,from_key,created) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(source,source_id) DO UPDATE SET "
        "to_key=excluded.to_key, to_title=excluded.to_title, "
        "from_key=excluded.from_key, created=excluded.created",
        (source, str(source_id), to_key, to_title, from_key, time.time()))
    con.commit()
    con.close()


def add_many(rows, to_key, to_title="", from_key=""):
    """Peel a batch of (source, source_id) rows onto one new entry."""
    for source, source_id in rows:
        add(source, source_id, to_key, to_title, from_key)


def remove(source, source_id):
    """Un-peel a single row (it returns to its natural entry on the next rebuild)."""
    if not os.path.exists(DB):
        return
    con = _con()
    con.execute("DELETE FROM peels WHERE source=? AND source_id=?",
                (source, str(source_id)))
    con.commit()
    con.close()


def remove_key(to_key):
    """Un-peel every row that was peeled onto to_key (undo a whole split)."""
    if not os.path.exists(DB):
        return
    con = _con()
    con.execute("DELETE FROM peels WHERE to_key=?", (to_key,))
    con.commit()
    con.close()


def list_peels():
    if not os.path.exists(DB):
        return []
    con = _con()
    out = [{"source": r[0], "source_id": r[1], "to_key": r[2],
            "to_title": r[3], "from_key": r[4], "created": r[5]}
           for r in con.execute(
               "SELECT source, source_id, to_key, to_title, from_key, created "
               "FROM peels ORDER BY created DESC")]
    con.close()
    return out
