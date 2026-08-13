#!/usr/bin/env python3
"""Per-entry IGDB resolution overrides.

`igdb_resolution` holds one id per title (`norm_key`). When a single title is genuinely
TWO different games on different platforms — the 1986 Amiga/Atari "Portal" text adventure
vs. Valve's 2007 PC "Portal" — the shared slot can only be right for one of them. This is
the additive per-entry layer: an entry `(norm_key, platform)` may carry its own igdb_id,
and `build_library` prefers it over the title-level resolution. The store/appid-owning
entry keeps the title-level resolution untouched; the era-compatible ROM entries get their
own correct identity here. Lives in metadata-cache.sqlite alongside igdb_resolution.
"""
import time


def ensure(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS entry_resolution("
        "norm_key TEXT, platform TEXT, igdb_id INTEGER, matched_by TEXT, "
        "resolved_at INTEGER, PRIMARY KEY(norm_key, platform))")


def set_entry(con, norm_key, platform, igdb_id, matched_by="ai_entry"):
    ensure(con)
    con.execute(
        "INSERT OR REPLACE INTO entry_resolution(norm_key,platform,igdb_id,matched_by,"
        "resolved_at) VALUES(?,?,?,?,?)",
        (norm_key, platform, int(igdb_id), matched_by, int(time.time())))


def set_detach(con, norm_key, platform):
    """Mark an entry as NOT the title's game — a homebrew/demake or a different game that
    merely shares the name (the Atari 2600 homebrew "Doom" vs id's real Doom). It forfeits
    the title-level resolution and stays its own identity (the title bucket / unmatched),
    so it never inherits the official game's metadata or art. Stored as igdb_id=0 so it's
    excluded from load()'s override map; read separately via load_detached()."""
    ensure(con)
    con.execute(
        "INSERT OR REPLACE INTO entry_resolution(norm_key,platform,igdb_id,matched_by,"
        "resolved_at) VALUES(?,?,?,?,?)",
        (norm_key, platform, 0, "detached", int(time.time())))


def load_detached(con):
    """{(norm_key, platform)} for entries explicitly detached from the title's game."""
    ensure(con)
    return {(r[0], r[1]) for r in con.execute(
        "SELECT norm_key, platform FROM entry_resolution WHERE matched_by='detached'")}


def clear_entry(con, norm_key, platform):
    ensure(con)
    con.execute("DELETE FROM entry_resolution WHERE norm_key=? AND platform=?",
                (norm_key, platform))


def load(con):
    """{(norm_key, platform): igdb_id} for every entry override — for build_library."""
    ensure(con)
    return {(r[0], r[1]): r[2] for r in con.execute(
        "SELECT norm_key, platform, igdb_id FROM entry_resolution WHERE igdb_id>0")}
