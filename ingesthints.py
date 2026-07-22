#!/usr/bin/env python3
"""AI-derived ingest hints — what the model read off a ROM's path that the
algorithmic parse could not.

build_romdb/romtags derive a title from a filename with rules: strip region tags,
version markers, dump flags, expand a few known abbreviations. That is fast, free
and right most of the time. It is wrong in the ways rules are always wrong —
cryptic 8.3 names ("SMW_U_[!]"), un-expanded abbreviations ("FF7"), Japanese
romanisations, and files whose folder lies about the system.

A hint records that for ONE (system, game) pair as the ROM index sees it, the
model believes the real title / platform / year is something else. build_library
applies it at add() time, exactly like a peel (see splits.py), so it survives
every rebuild without mutating the ROM index — the index stays a faithful
description of what is on disk.

Hints are ADVISORY and never outrank a human: a manual pin or an entry override
still wins downstream. They are also fully reversible — drop the row and the next
rebuild returns to the algorithmic title.
"""
import os
import sqlite3
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
DB = os.path.join(DATA, "ingest-hints.sqlite")


def _con():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS hints(
        system TEXT NOT NULL,          -- system label as the ROM index recorded it
        game TEXT NOT NULL,            -- algorithmic title the index derived
        to_title TEXT,                 -- model's title ('' = keep algorithmic)
        to_platform TEXT,              -- model's platform ('' = keep folder's)
        year INTEGER,
        confidence REAL DEFAULT 0,
        model TEXT DEFAULT '',
        sample_path TEXT DEFAULT '',   -- one real relpath, so a human can audit it
        created REAL,
        PRIMARY KEY(system, game))""")
    return con


def overrides(min_confidence=0.0):
    """{(system, game): (to_title, to_platform, year)} for build_library.

    Only hints at or above min_confidence are returned, so the caller can tighten
    the bar without deleting anything."""
    if not os.path.exists(DB):
        return {}
    con = _con()
    out = {}
    for r in con.execute("SELECT system, game, to_title, to_platform, year, confidence "
                         "FROM hints WHERE confidence >= ?", (min_confidence,)):
        if not (r[2] or r[3] or r[4]):      # a hint that changes nothing is not a hint
            continue
        out[(r[0], r[1])] = (r[2] or "", r[3] or "", r[4])
    con.close()
    return out


def put(system, game, to_title="", to_platform="", year=None, confidence=0.0,
        model="", sample_path=""):
    """Record (or replace) one hint. A hint that asserts nothing is dropped rather
    than stored, so `overrides()` never has to filter noise it could have avoided."""
    if not system and not game:
        return False
    if not (to_title or to_platform or year):
        return False
    con = _con()
    con.execute(
        "INSERT INTO hints(system,game,to_title,to_platform,year,confidence,model,"
        "sample_path,created) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(system,game) DO UPDATE SET to_title=excluded.to_title, "
        "to_platform=excluded.to_platform, year=excluded.year, "
        "confidence=excluded.confidence, model=excluded.model, "
        "sample_path=excluded.sample_path, created=excluded.created",
        (system, game, to_title, to_platform, year, float(confidence or 0), model,
         sample_path, time.time()))
    con.commit()
    con.close()
    return True


def have_keys():
    """{(system, game)} already hinted — so a re-run skips what it already asked
    about instead of paying for it twice."""
    if not os.path.exists(DB):
        return set()
    con = _con()
    out = {(r[0], r[1]) for r in con.execute("SELECT system, game FROM hints")}
    con.close()
    return out


def count():
    if not os.path.exists(DB):
        return 0
    con = _con()
    n = con.execute("SELECT COUNT(*) FROM hints").fetchone()[0]
    con.close()
    return n


def clear(system=None):
    """Drop every hint (or just one system's). The next rebuild reverts to the
    algorithmic titles — this is the undo."""
    if not os.path.exists(DB):
        return 0
    con = _con()
    if system:
        n = con.execute("DELETE FROM hints WHERE system=?", (system,)).rowcount
    else:
        n = con.execute("DELETE FROM hints").rowcount
    con.commit()
    con.close()
    return n


def listing(limit=500):
    """Recent hints for the UI / an audit — newest first."""
    if not os.path.exists(DB):
        return []
    con = _con()
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM hints ORDER BY created DESC LIMIT ?", (limit,))]
    con.close()
    return rows
