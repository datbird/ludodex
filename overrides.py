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
DB = os.path.join(DIR, "attr-overrides.sqlite")


def _con():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS overrides(
        norm_key TEXT, kind TEXT, value TEXT, origin TEXT, created REAL,
        PRIMARY KEY(norm_key, kind))""")
    return con


def set_override(norm_key, kind, value, origin="manual"):
    if not (norm_key and kind) or value in (None, ""):
        raise ValueError("norm_key, kind and value are required")
    con = _con()
    con.execute("INSERT INTO overrides(norm_key,kind,value,origin,created) "
                "VALUES(?,?,?,?,?) ON CONFLICT(norm_key,kind) DO UPDATE SET "
                "value=excluded.value, origin=excluded.origin, created=excluded.created",
                (norm_key, kind, str(value), origin or "manual", time.time()))
    con.commit()
    con.close()


def clear_override(norm_key, kind):
    con = _con()
    con.execute("DELETE FROM overrides WHERE norm_key=? AND kind=?", (norm_key, kind))
    con.commit()
    con.close()


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
