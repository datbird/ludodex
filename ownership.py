#!/usr/bin/env python3
"""Durable per-format ownership facts — the manual layer that store/ROM syncs
can't know: physical-disc ownership, and per-platform *wants* that coexist with
what you already own.

ludodex's auto sources (store accounts, ROM files) all mean "I have this". This
store adds the two things they can't express:
  • PHYSICAL ownership   — "I own the physical GameCube disc"      (form=physical, state=have)
  • per-format WANT      — "I want the Switch ROM of a game I own" (form=rom,      state=want)

Keyed by norm_key so a row survives library rebuilds (game ids are regenerated).
build_library merges these in as ordinary `sources` rows carrying their state, so
a single game can hold have- and want-sources across several platforms at once.
The game's overall Owned/Wanted is then DERIVED: owned if any source is 'have'.
"""
import os
import sqlite3

# forms map to a `sources.source` kind; 'digital' stays generic (a manual note
# about a store copy we don't sync). 'rom' == the emulation source kind.
FORMS = {"physical", "rom", "digital"}
STATES = {"have", "want"}
FORM_TO_SOURCE = {"physical": "physical", "rom": "emulation", "digital": "digital"}


_OWNERSHIP_DDL = """CREATE TABLE IF NOT EXISTS ownership(
        norm_key TEXT NOT NULL,
        title    TEXT NOT NULL,       -- seeds a game row if the title is new
        form     TEXT NOT NULL,       -- physical | rom | digital
        platform TEXT NOT NULL,       -- gamecube, switch, … ('' = unspecified)
        state    TEXT NOT NULL,       -- have | want
        note     TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (norm_key, form, platform, state))"""
_OWNERSHIP_COLS = {"norm_key", "title", "form", "platform", "state", "note"}


def _db(data_dir):
    p = os.path.join(data_dir, "ownership.sqlite")
    con = sqlite3.connect(p)
    con.execute(_OWNERSHIP_DDL)
    # Heal a table the backing-store sync recreated with only its key columns: the
    # dbsync pull builds a table from the columns present in remote data and a reduced
    # key, so a never-populated ownership store lands as (norm_key, form) — missing
    # platform/state/title/note AND with the wrong PRIMARY KEY. CREATE IF NOT EXISTS
    # above then no-ops, and every ownership query dies on "no such column: platform".
    # This module owns the schema, so reconcile here. Empty (the real case) → rebuild
    # clean so the PK is correct too; non-empty → add the missing columns best-effort.
    have = {r[1] for r in con.execute("PRAGMA table_info(ownership)")}
    if not _OWNERSHIP_COLS.issubset(have):
        if con.execute("SELECT COUNT(*) FROM ownership").fetchone()[0] == 0:
            con.execute("DROP TABLE ownership")
            con.execute(_OWNERSHIP_DDL)
        else:
            for col in ("title", "platform", "state", "note"):
                if col not in have:
                    con.execute("ALTER TABLE ownership ADD COLUMN %s TEXT NOT NULL "
                                "DEFAULT ''" % col)
    con.commit()
    return con


def list_for(data_dir, norm_key):
    con = _db(data_dir)
    try:
        return [dict(form=f, platform=p, state=s, note=n)
                for f, p, s, n in con.execute(
                    "SELECT form, platform, state, note FROM ownership "
                    "WHERE norm_key=? ORDER BY state, form, platform", (norm_key,))]
    finally:
        con.close()


def set_fact(data_dir, norm_key, title, form, platform, state, note=""):
    """Add/replace one ownership fact. Returns the normalised row."""
    form = (form or "").lower().strip()
    state = (state or "").lower().strip()
    platform = (platform or "").strip()
    if form not in FORMS or state not in STATES:
        raise ValueError("form must be one of %s and state one of %s"
                         % (sorted(FORMS), sorted(STATES)))
    con = _db(data_dir)
    try:
        con.execute("INSERT OR REPLACE INTO ownership"
                    "(norm_key,title,form,platform,state,note) VALUES(?,?,?,?,?,?)",
                    (norm_key, title, form, platform, state, note or ""))
        con.commit()
    finally:
        con.close()
    return dict(form=form, platform=platform, state=state, note=note or "")


def clear_fact(data_dir, norm_key, form, platform, state):
    con = _db(data_dir)
    try:
        con.execute("DELETE FROM ownership WHERE norm_key=? AND form=? AND "
                    "platform=? AND state=?",
                    (norm_key, (form or "").lower(), platform or "",
                     (state or "").lower()))
        con.commit()
    finally:
        con.close()


def all_facts(data_dir):
    """Every fact — for build_library to merge. Yields (norm_key,title,source,
    platform,state,note) with `form` already mapped to a sources.source kind."""
    p = os.path.join(data_dir, "ownership.sqlite")
    if not os.path.exists(p):
        return
    con = sqlite3.connect(p)
    try:
        for nk, title, form, platform, state, note in con.execute(
                "SELECT norm_key,title,form,platform,state,note FROM ownership"):
            yield (nk, title, FORM_TO_SOURCE.get(form, form), platform, state, note)
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()
