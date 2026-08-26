#!/usr/bin/env python3
"""Entries the user has pinned to their own card.

The edition fold reads IGDB's parent graph, and IGDB's `expanded_game` type is loose:
it links Scholar of the First Sin to Dark Souls II, which is right, and Arcade Paradise
VR to Arcade Paradise, which is arguable. A rule that reads a provider will always have
a tail of cases the user disagrees with, so the user gets a reverse.

A pin changes the CARD only: the entry's game_key, its provider link and its art are
untouched.

WHY ITS OWN FILE, and not metadata-cache.sqlite where `entry_res` lives. A pin is a
HUMAN DECISION. `reset.py` classes metadata-cache as an IMPORT database, under a comment
promising that deleting those "costs only the time to re-import, no human decision is
lost". A library-scope reset would therefore have deleted every pin, silently, while
telling the user nothing was lost. Curation lives in curation stores; this is one.
"""
import os
import sqlite3
import time

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
DB = os.path.join(DATA, "card-unfold.sqlite")


def ensure(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS card_unfold("
        "entry_key TEXT PRIMARY KEY, pinned_at INTEGER)")


def con_db():
    con = sqlite3.connect(DB)
    ensure(con)
    return con


def set_unfold(con, entry_key):
    """Pin one entry to its own card."""
    ensure(con)
    con.execute("INSERT OR REPLACE INTO card_unfold(entry_key,pinned_at) VALUES(?,?)",
                (entry_key, int(time.time())))


def clear_unfold(con, entry_key):
    """Let the entry fold again on the next rebuild."""
    ensure(con)
    con.execute("DELETE FROM card_unfold WHERE entry_key=?", (entry_key,))


def load(con):
    """{entry_key} for every entry pinned to its own card, on a caller's connection."""
    ensure(con)
    return {r[0] for r in con.execute("SELECT entry_key FROM card_unfold")}


def load_all():
    """{entry_key} read from the store on disk. For `build_library`.

    AN ABSENT FILE MEANS NO PINS, which is a real answer. ANY OTHER FAILURE RAISES, and
    that distinction is the whole point of this function. Swallowing a read error into
    an empty set would make a locked or corrupt store indistinguishable from "the user
    pinned nothing", so a rebuild would quietly fold back every card they had separated.
    That is the fail-open shape the 2026-08-23 audit spent its whole HIGH batch on: a
    miss returning empty, and the caller reading it as consent.
    """
    if not os.path.exists(DB):
        return set()
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    try:
        return {r[0] for r in con.execute("SELECT entry_key FROM card_unfold")}
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return set()               # created but never written to: no pins yet
        raise
    finally:
        con.close()
