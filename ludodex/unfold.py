#!/usr/bin/env python3
"""Entries the user has pinned to their own card.

The edition fold reads IGDB's parent graph, and IGDB's `expanded_game` type is loose:
it links Scholar of the First Sin to Dark Souls II, which is right, and Arcade Paradise
VR to Arcade Paradise, which is arguable. A rule that reads a provider will always have
a tail of cases the user disagrees with, so the user gets a reverse.

A pin here is durable and a rebuild never overwrites it, exactly like `entry_res`'s
per-entry resolution. It changes the CARD only: the entry's game_key, its provider link
and its art are untouched.

Lives in metadata-cache.sqlite alongside entry_resolution.
"""
import time


def ensure(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS card_unfold("
        "entry_key TEXT PRIMARY KEY, pinned_at INTEGER)")


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
    """{entry_key} for every entry pinned to its own card — for build_library."""
    ensure(con)
    return {r[0] for r in con.execute("SELECT entry_key FROM card_unfold")}
