#!/usr/bin/env python3
"""Disabled metadata-provider identities (tiered ingest, editable identity badges).

When a user disables a provider (igdb / screenscraper / …) on a game, that provider's
attribute values and media drop out of use and the game falls back to the next
provider's RETAINED value (see build_library.provider_attrs). Durable
(`identity-disable.sqlite`), applied at query time so it survives rebuilds — the same
pattern as attr-overrides. Store-ownership badges (steam/gog/…) are facts and are never
disable-able; only metadata providers are.

One row per (norm_key, provider) = "this provider is turned off for this game".
"""
import os
import sqlite3
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
DB = os.path.join(DATA, "identity-disable.sqlite")


def _con():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS disabled_identity(
        norm_key TEXT, provider TEXT, created REAL,
        PRIMARY KEY(norm_key, provider))""")
    # Heal a backing-store-stripped table (a narrow pull can drop non-key columns).
    have = {r[1] for r in con.execute("PRAGMA table_info(disabled_identity)")}
    if "created" not in have:
        con.execute("ALTER TABLE disabled_identity ADD COLUMN created REAL")
    con.commit()
    return con


def set_disabled(norm_key, provider, disabled=True):
    """Turn a provider off (disabled=True) or back on (False) for one game."""
    if not norm_key or not provider:
        return
    con = _con()
    try:
        if disabled:
            con.execute("INSERT OR REPLACE INTO disabled_identity(norm_key,provider,created) "
                        "VALUES(?,?,?)", (norm_key, provider, time.time()))
        else:
            con.execute("DELETE FROM disabled_identity WHERE norm_key=? AND provider=?",
                        (norm_key, provider))
        con.commit()
    finally:
        con.close()


def disabled_for(norm_key):
    """Set of providers the user has disabled for this game."""
    if not norm_key:
        return set()
    con = _con()
    try:
        return {r["provider"] for r in con.execute(
            "SELECT provider FROM disabled_identity WHERE norm_key=?", (norm_key,))}
    finally:
        con.close()
