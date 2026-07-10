#!/usr/bin/env python3
"""Durable per-asset media flags, keyed by the stable (norm_key, kind, provider,
ref) identity so they survive media-index rescans:

  * banned    — remove the asset AND never (re)download it from a provider again.
                Enforced in media_fetch.put() (banned refs are skipped) and by
                deleting the row from the index at ban time.
  * no_redist — keep the asset locally, but don't copy it to other machines when
                games are distributed to them. (Redistributable is the default.)

Rows with neither flag set are pruned, so the table only holds the exceptions.
"""
import os
import sqlite3
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
DB = os.path.join(DATA, "media-flags.sqlite")


def _con():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS media_flags(
        norm_key TEXT, kind TEXT, provider TEXT, ref TEXT,
        banned INTEGER DEFAULT 0, no_redist INTEGER DEFAULT 0, updated REAL,
        PRIMARY KEY(norm_key, kind, provider, ref))""")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _set(nk, kind, provider, ref, col, val):
    con = _con()
    con.execute(
        "INSERT INTO media_flags(norm_key,kind,provider,ref,%s,updated) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(norm_key,kind,provider,ref) "
        "DO UPDATE SET %s=excluded.%s, updated=excluded.updated" % (col, col, col),
        (nk, kind, provider, ref, int(val), time.time()))
    con.execute("DELETE FROM media_flags WHERE banned=0 AND no_redist=0")
    con.commit()
    con.close()


def ban(nk, kind, provider, ref):
    _set(nk, kind, provider, ref, "banned", 1)


def unban(nk, kind, provider, ref):
    _set(nk, kind, provider, ref, "banned", 0)


def set_redist(nk, kind, provider, ref, redistributable):
    _set(nk, kind, provider, ref, "no_redist", 0 if redistributable else 1)


def banned_set():
    """{(norm_key, kind, provider, ref)} that must never be (re)downloaded."""
    if not os.path.exists(DB):
        return set()
    con = _con()
    s = {(r[0], r[1], r[2], r[3]) for r in con.execute(
        "SELECT norm_key, kind, provider, ref FROM media_flags WHERE banned=1")}
    con.close()
    return s


def no_redist_for(nk):
    """{(kind, provider, ref)} flagged not-redistributable for one game."""
    if not os.path.exists(DB):
        return set()
    con = _con()
    s = {(r[0], r[1], r[2]) for r in con.execute(
        "SELECT kind, provider, ref FROM media_flags "
        "WHERE no_redist=1 AND norm_key=?", (nk,))}
    con.close()
    return s


def no_redist_set():
    """{(norm_key, kind, provider, ref)} flagged not-redistributable, library-wide
    — the filter a copy-to-device pass applies so banned-from-sharing art stays put."""
    if not os.path.exists(DB):
        return set()
    con = _con()
    s = {(r[0], r[1], r[2], r[3]) for r in con.execute(
        "SELECT norm_key, kind, provider, ref FROM media_flags WHERE no_redist=1")}
    con.close()
    return s


def list_banned():
    """Banned assets (most recent first) for the Settings 'unban' list."""
    if not os.path.exists(DB):
        return []
    con = _con()
    out = [{"norm_key": r[0], "kind": r[1], "provider": r[2], "ref": r[3],
            "updated": r[4]} for r in con.execute(
        "SELECT norm_key, kind, provider, ref, updated FROM media_flags "
        "WHERE banned=1 ORDER BY updated DESC")]
    con.close()
    return out
