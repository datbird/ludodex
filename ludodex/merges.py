#!/usr/bin/env python3
"""Durable game merges — fold a duplicate catalog entry into a canonical one.

A merge records `from_key -> to_key` (both are title norm_keys). build_library
applies these when it dedupes, so the two entries collapse into one on EVERY
rebuild (the merge survives syncs). At merge time we also re-key the per-game
USER/derived data (media, tags, scores, ownership, framing, …) from the merged-
away key onto the canonical one, so the surviving game keeps everything both had.
Identity (title + IGDB match) stays with the canonical (source-of-truth) entry.
"""
import os
import sqlite3
import time

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
DB = os.path.join(DATA, "merges.sqlite")

# (db filename, table) pairs that carry a per-game `norm_key` and should follow a
# merge. metadata-cache (igdb_resolution) is DELIBERATELY excluded — identity/
# match belongs to the canonical entry — as is manual-games (a source identity,
# folded by build_library via the alias instead).
_REKEY = [
    ("media-index.sqlite", "media"),
    ("media-flags.sqlite", "media_flags"),
    ("framing.sqlite", "framing"),
    ("ownership.sqlite", "ownership"),
    ("tags.sqlite", "user_tags"),
    ("user-media.sqlite", "user_media"),
    ("steam-tags.sqlite", "steam_tags"),
    ("scores.sqlite", "ratings"),
    ("scores.sqlite", "game_scores"),
    ("scores.sqlite", "store_type"),
    ("attr-overrides.sqlite", "overrides"),
    ("connections.sqlite", "device_wants"),
    ("ai-metadata.sqlite", "findings"),
]


def _con():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS merges(
        from_key TEXT PRIMARY KEY, to_key TEXT NOT NULL,
        from_title TEXT, to_title TEXT, created REAL)""")
    return con


def alias_map():
    """{from_key: canonical_key}, chains resolved (A->B->C collapses A,B -> C)."""
    if not os.path.exists(DB):
        return {}
    con = _con()
    raw = {r[0]: r[1] for r in con.execute("SELECT from_key, to_key FROM merges")}
    con.close()

    def resolve(k, seen):
        nxt = raw.get(k)
        if nxt is None or nxt == k or nxt in seen:
            return k
        return resolve(nxt, seen | {k})
    return {k: resolve(v, {k}) for k, v in raw.items()}


def add(from_key, to_key, from_title="", to_title=""):
    """Record from_key -> to_key. Redirects any existing merge that pointed at
    from_key to the new canonical, so chains stay flat and cycles can't form."""
    if not from_key or not to_key or from_key == to_key:
        raise ValueError("bad merge keys")
    con = _con()
    # resolve to_key to its canonical ROOT (a merge target is always a root), then
    # reject if that root is from_key (would create a cycle).
    chain = {r[0]: r[1] for r in con.execute("SELECT from_key, to_key FROM merges")}
    seen, root = set(), to_key
    while root in chain and root not in seen:
        seen.add(root)
        root = chain[root]
    if root == from_key:
        con.close()
        raise ValueError("merge would create a cycle")
    to_key = root
    con.execute("INSERT INTO merges(from_key,to_key,from_title,to_title,created) "
                "VALUES(?,?,?,?,?) ON CONFLICT(from_key) DO UPDATE SET "
                "to_key=excluded.to_key, to_title=excluded.to_title, created=excluded.created",
                (from_key, to_key, from_title, to_title, time.time()))
    con.execute("UPDATE merges SET to_key=? WHERE to_key=?", (to_key, from_key))
    con.commit()
    con.close()


def remove(from_key):
    """Un-merge: stop folding from_key (its entry reappears on the next rebuild).
    Does NOT un-re-key data that already moved to the canonical."""
    if not os.path.exists(DB):
        return
    con = _con()
    con.execute("DELETE FROM merges WHERE from_key=?", (from_key,))
    con.commit()
    con.close()


def list_merges():
    if not os.path.exists(DB):
        return []
    con = _con()
    out = [{"from_key": r[0], "to_key": r[1], "from_title": r[2],
            "to_title": r[3], "created": r[4]}
           for r in con.execute("SELECT from_key,to_key,from_title,to_title,created "
                                "FROM merges ORDER BY created DESC")]
    con.close()
    return out


def rekey_user_data(from_key, to_key):
    """Move every per-game row from `from_key` onto `to_key` across the durable
    per-game stores, so the merged game keeps both entries' media/tags/scores/
    ownership/etc. On a key collision the canonical's row wins (union). Best-effort
    — a missing db/table is skipped. Returns the number of tables touched."""
    n = 0
    for fname, table in _REKEY:
        p = os.path.join(DATA, fname)
        if not os.path.exists(p):
            continue
        con = sqlite3.connect(p)
        try:
            con.execute("UPDATE OR IGNORE %s SET norm_key=? WHERE norm_key=?"
                        % table, (to_key, from_key))
            con.execute("DELETE FROM %s WHERE norm_key=?" % table, (from_key,))
            con.commit()
            n += 1
        except sqlite3.OperationalError:
            pass
        con.close()
    return n
