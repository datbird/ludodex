#!/usr/bin/env python3
"""Publish intent — which ENTRIES belong on which device.

WHY THIS EXISTS RATHER THAN device_wants. Intent used to be keyed by `norm_key`, which
is a title. A title is not a thing you can put on a device: "Rayman" is the PS1 game and
the Saturn game and the Steam one, and a device either has each of those or it doesn't,
independently. Keyed by title, the queue could not express "the Saturn one, not the PS1
one" — which is not an edge case, it is the ordinary case for anyone whose ROM library
overlaps their store libraries.

So intent moves to `entry_key`, the catalog's unit of identity: one row per
(game, platform).

EXCLUDE IS A STATE, NOT AN ABSENCE. "Everything SNES except these four" has to survive
re-evaluating "everything SNES", so a user's no is recorded rather than inferred from a
missing row. This is the same precedence the match index uses: an explicit decision
outranks a derived one.

MIGRATION IS LAZY AND LOSSLESS. Expanding a norm_key into its entries needs the catalog,
and the catalog may be mid-rebuild, may not have been built yet, or may genuinely not
contain a title someone queued months ago. So migrate() expands what it can, leaves what
it cannot, and REPORTS the remainder. device_wants is left in place as a read-only
legacy table until a later phase can prove nothing was lost — deleting a user's queue to
tidy a schema is not a trade worth making.

Phase 1 of the Publish design (docs/superpowers/specs/2026-08-13-publish-design.md).
Intent is keyed by device here; phase 3 introduces publish targets (a device can host
more than one frontend) and adds target_id alongside.
"""
import os
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
sys.path.insert(0, DIR)

DB = os.path.join(DATA, "connections.sqlite")      # lives with the device data
LIBRARY_DB = os.path.join(DATA, "game-library.sqlite")

INCLUDE, EXCLUDE = "include", "exclude"
STATES = (INCLUDE, EXCLUDE)


def _con():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS publish_intent(
        device_id INTEGER,
        entry_key TEXT,
        state     TEXT NOT NULL DEFAULT 'include',
        source    TEXT,          -- manual | migrated | rule:<id>
        added     REAL,
        note      TEXT,
        PRIMARY KEY(device_id, entry_key))""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_pi_entry ON publish_intent(entry_key)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_pi_dev ON publish_intent(device_id, state)")
    # Which (device, title) pairs migration has ALREADY expanded. Without this,
    # idempotence is only as good as the rows still being present — and a user who
    # migrates, then removes an entry they do not want, gets it back on the next run.
    # INSERT OR IGNORE cannot express "was here once"; this table can.
    con.execute("""CREATE TABLE IF NOT EXISTS publish_migrated(
        device_id INTEGER, norm_key TEXT, at REAL,
        PRIMARY KEY(device_id, norm_key))""")
    con.commit()
    return con


def _library(path=None):
    """Read-only catalog handle, or None when there is no catalog to read.

    None means "cannot answer", never "the answer is empty" — a caller that treats a
    missing catalog as an empty one silently drops intent."""
    p = path or LIBRARY_DB
    if not os.path.exists(p):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


# --- resolving a title to its entries --------------------------------------- #
def entries_for(norm_key, lib=None):
    """Every entry_key this title has in the catalog -> [{entry_key, platform, title}].

    Empty means the catalog has no entry for it, which is a real answer. A caller that
    needs to distinguish that from "no catalog" should check _library() itself."""
    own = lib is None
    lib = lib or _library()
    if lib is None:
        return []
    try:
        return [dict(r) for r in lib.execute(
            "SELECT entry_key, platform, canonical_title AS title FROM games "
            "WHERE norm_key=? AND entry_key IS NOT NULL AND entry_key!='' "
            "ORDER BY platform", (norm_key,))]
    except sqlite3.Error:
        return []
    finally:
        if own:
            lib.close()


def entry_rows(entry_keys, lib=None):
    """Catalog rows for entry_keys, for display. Missing keys are simply absent."""
    keys = [k for k in (entry_keys or []) if k]
    if not keys:
        return []
    own = lib is None
    lib = lib or _library()
    if lib is None:
        return []
    try:
        out = []
        for i in range(0, len(keys), 500):          # SQLite's variable limit
            chunk = keys[i:i + 500]
            q = ",".join("?" * len(chunk))
            out += [dict(r) for r in lib.execute(
                "SELECT entry_key, norm_key, platform, canonical_title AS title "
                "FROM games WHERE entry_key IN (%s)" % q, chunk)]
        return out
    except sqlite3.Error:
        return []
    finally:
        if own:
            lib.close()


# --- intent ------------------------------------------------------------------ #
def intent_set(device_id, entry_keys, state=INCLUDE, source="manual", note=None):
    """Mark entries for (or against) a device. Returns how many rows changed.

    An explicit call always wins over what is already recorded — including flipping an
    earlier exclude back to include — because this IS the user speaking."""
    if state not in STATES:
        raise ValueError("state must be one of %r" % (STATES,))
    keys = [k for k in (entry_keys or []) if k]
    if not keys:
        return 0
    con = _con()
    now = time.time()
    before = con.total_changes
    con.executemany(
        "INSERT INTO publish_intent(device_id,entry_key,state,source,added,note) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(device_id,entry_key) DO UPDATE SET "
        "state=excluded.state, source=excluded.source, added=excluded.added, "
        "note=excluded.note",
        [(int(device_id), k, state, source, now, note) for k in keys])
    n = con.total_changes - before
    con.commit()
    con.close()
    return n


def intent_clear(device_id, entry_keys):
    """Forget an opinion entirely — different from recording an exclude."""
    keys = [k for k in (entry_keys or []) if k]
    if not keys:
        return 0
    con = _con()
    before = con.total_changes
    con.executemany("DELETE FROM publish_intent WHERE device_id=? AND entry_key=?",
                    [(int(device_id), k) for k in keys])
    n = con.total_changes - before
    con.commit()
    con.close()
    return n


def intent_clear_device(device_id):
    con = _con()
    con.execute("DELETE FROM publish_intent WHERE device_id=?", (int(device_id),))
    con.commit()
    con.close()


def intent_list(device_id, state=INCLUDE, with_catalog=True):
    """Entries marked on a device, newest first, joined to the catalog for display."""
    con = _con()
    rows = [dict(r) for r in con.execute(
        "SELECT entry_key, state, source, added, note FROM publish_intent "
        "WHERE device_id=? AND state=? ORDER BY added DESC",
        (int(device_id), state))]
    con.close()
    if with_catalog and rows:
        meta = {r["entry_key"]: r for r in entry_rows([r["entry_key"] for r in rows])}
        for r in rows:
            m = meta.get(r["entry_key"])
            if m:
                r.update(title=m["title"], platform=m["platform"],
                         norm_key=m["norm_key"])
    return rows


def intent_keys(device_id, state=INCLUDE):
    con = _con()
    keys = [r["entry_key"] for r in con.execute(
        "SELECT entry_key FROM publish_intent WHERE device_id=? AND state=? "
        "ORDER BY added DESC", (int(device_id), state))]
    con.close()
    return keys


def intent_counts(state=INCLUDE):
    """device_id -> count, for badges."""
    con = _con()
    out = {r["device_id"]: r["n"] for r in con.execute(
        "SELECT device_id, COUNT(*) AS n FROM publish_intent WHERE state=? "
        "GROUP BY device_id", (state,))}
    con.close()
    return out


def intent_for_entry(entry_key, state=INCLUDE):
    """Device ids that want THIS entry — the per-platform question."""
    con = _con()
    ids = [r["device_id"] for r in con.execute(
        "SELECT device_id FROM publish_intent WHERE entry_key=? AND state=?",
        (entry_key, state))]
    con.close()
    return ids


def intent_for_title(norm_key, state=INCLUDE):
    """-> {device_id: [entry_key, …]} for every entry of this title.

    The game page knows a title and wants to show which devices want which platforms of
    it. Returning entries per device rather than a flat device list is the whole point:
    'on 2 devices' is a worse answer than 'PS1 on the Deck, Saturn on the cabinet'."""
    ents = [e["entry_key"] for e in entries_for(norm_key)]
    if not ents:
        return {}
    con = _con()
    out = {}
    for i in range(0, len(ents), 500):
        chunk = ents[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in con.execute(
                "SELECT device_id, entry_key FROM publish_intent "
                "WHERE state=? AND entry_key IN (%s)" % q, [state] + chunk):
            out.setdefault(r["device_id"], []).append(r["entry_key"])
    con.close()
    return out


# --- migration from device_wants --------------------------------------------- #
def migrate(dry_run=False, lib_path=None):
    """Expand every device_wants row into its catalog entries.

    Idempotent at the TITLE level, which is the only level where it can be. A pair that
    has been expanded once is recorded in publish_migrated and skipped forever after —
    because "already migrated" cannot be inferred from the rows being present: a user
    who migrates, then deletes the Steam entry their handheld cannot run, would get it
    back on the next run. Row-level INSERT OR IGNORE looks like it prevents that and
    does not.

    device_wants is NOT dropped. A title the catalog cannot resolve today may resolve
    after the next rebuild, and deleting a queue to tidy a schema is not a trade worth
    making."""
    lib = _library(lib_path)
    con = _con()
    have_wants = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='device_wants'"
    ).fetchone()[0]
    if not have_wants:
        con.close()
        if lib:
            lib.close()
        return {"skipped": "no device_wants table"}
    done = {(r["device_id"], r["norm_key"]) for r in
            con.execute("SELECT device_id, norm_key FROM publish_migrated")}
    wants = [dict(r) for r in con.execute(
        "SELECT device_id, norm_key, added FROM device_wants")
             if (r["device_id"], r["norm_key"]) not in done]
    if lib is None:
        con.close()
        return {"wants": len(wants), "expanded": 0, "entries": 0,
                "unresolved": len(wants), "blocked": "no catalog to expand against"}

    now = time.time()
    expanded = entries = 0
    unresolved = []
    rows, marks = [], []
    for w in wants:
        ents = entries_for(w["norm_key"], lib)
        if not ents:
            unresolved.append((w["device_id"], w["norm_key"]))
            continue
        expanded += 1
        marks.append((int(w["device_id"]), w["norm_key"], now))
        for e in ents:
            entries += 1
            rows.append((int(w["device_id"]), e["entry_key"], INCLUDE, "migrated",
                         w["added"] or now, None))
    if not dry_run and rows:
        # OR IGNORE so a decision the user has already changed by hand is not reverted;
        # the publish_migrated mark is what stops a SECOND run re-adding a deletion.
        con.executemany(
            "INSERT OR IGNORE INTO publish_intent"
            "(device_id,entry_key,state,source,added,note) VALUES(?,?,?,?,?,?)", rows)
        con.executemany(
            "INSERT OR IGNORE INTO publish_migrated(device_id,norm_key,at) "
            "VALUES(?,?,?)", marks)
        con.commit()
    total = con.execute("SELECT COUNT(*) FROM publish_intent").fetchone()[0]
    con.close()
    lib.close()
    return {"wants": len(wants), "titles_expanded": expanded,
            "entries_written": entries, "unresolved": len(unresolved),
            "unresolved_sample": [k for _d, k in unresolved[:10]],
            "intent_rows": total, "dry_run": bool(dry_run)}


def status():
    con = _con()
    q = lambda s, *a: con.execute(s, a).fetchone()[0]      # noqa: E731
    out = {"intent_rows": q("SELECT COUNT(*) FROM publish_intent"),
           "included": q("SELECT COUNT(*) FROM publish_intent WHERE state=?", INCLUDE),
           "excluded": q("SELECT COUNT(*) FROM publish_intent WHERE state=?", EXCLUDE),
           "devices": q("SELECT COUNT(DISTINCT device_id) FROM publish_intent")}
    if con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                   "AND name='device_wants'").fetchone()[0]:
        out["legacy_device_wants"] = q("SELECT COUNT(*) FROM device_wants")
    con.close()
    out["catalog"] = os.path.exists(LIBRARY_DB)
    return out


def main(argv):
    import json
    if "--migrate" in argv:
        print(json.dumps(migrate(dry_run="--dry-run" in argv), indent=2))
        return 0
    print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
