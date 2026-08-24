#!/usr/bin/env python3
"""The framing store: pay for its schema once, and check what it is asked to store.

`_db()` ran CREATE TABLE, a PRAGMA table_info, up to seven ALTERs, a second CREATE, a
second PRAGMA and a commit on EVERY call — and `get_all`/`for_keys` call it per request.
The library grid calls `for_keys` for every page it draws, so drawing a shelf of covers
meant a write transaction against framing.sqlite each time, for a schema that has been
settled since the row was first written.

The heal is still needed (the backing-store sync can recreate the table from only the
columns the remote data happened to hold), so the answer is not to delete it — it is to
run it once per file per process and then stop.

`set_hero` had the other shape of the same carelessness: it stored any string at all.
The value is read back as a media KIND and used to choose what the detail hero displays,
so a typo — or an old kind name — is written happily and then silently does nothing,
which looks exactly like the feature being broken.

Offline. No network.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-framing-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import framing                                                 # noqa: E402
import media                                                   # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def ddl_count(fn):
    """How many schema statements one call issues against framing.sqlite."""
    seen = []
    real = sqlite3.connect

    def spy(*a, **k):
        con = real(*a, **k)
        con.set_trace_callback(
            lambda q: seen.append(q) if q.strip().split()[0].upper() in
            ("CREATE", "ALTER", "PRAGMA") else None)
        return con
    sqlite3.connect = spy
    try:
        fn()
    finally:
        sqlite3.connect = real
    return len(seen)


def main():
    print("1. the schema is settled once, not on every read")
    first = ddl_count(lambda: framing.get_all(DATA, "sonic"))
    check("the first open does the CREATE/heal work", first > 0)
    later = ddl_count(lambda: framing.get_all(DATA, "sonic"))
    check("the next read issues no schema statements (%d)" % later, later == 0)
    grid = ddl_count(lambda: framing.for_keys(DATA, ["a", "b", "c"], "cover"))
    check("nor does the library grid's for_keys (%d)" % grid, grid == 0)

    print("2. and the heal still happens for a table that needs it")
    other = os.path.join(DATA, "other")
    os.makedirs(other, exist_ok=True)
    # what the backing-store sync leaves behind: only the columns the remote data held
    con = sqlite3.connect(os.path.join(other, "framing.sqlite"))
    con.execute("CREATE TABLE framing(norm_key TEXT NOT NULL, kind TEXT NOT NULL, "
                "PRIMARY KEY(norm_key, kind))")
    con.commit()
    con.close()
    framing.set_frame(other, "sonic", "cover", top=-5, zoom=1.4)
    got = framing.get_all(other, "sonic")
    check("a stripped table is repaired and the frame lands",
          got.get("cover", {}).get("zoom") == 1.4)

    print("3. writes still work, and a cleared frame still disappears")
    framing.set_frame(DATA, "sonic", "cover", top=-10, zoom=2.0)
    check("the frame is stored", framing.get_all(DATA, "sonic")["cover"]["top"] == -10)
    framing.set_frame(DATA, "sonic", "cover")          # all defaults = unframed
    check("an all-default frame is removed", "cover" not in framing.get_all(DATA, "sonic"))

    print("4. set_hero stores a real media kind, or nothing")
    check("a real kind is accepted",
          framing.set_hero(DATA, "sonic", "marquee") == "marquee")
    check("and read back", framing.get_hero(DATA, "sonic") == "marquee")
    check("'auto' clears it", framing.set_hero(DATA, "sonic", "auto") is None)
    check("so does empty", framing.set_hero(DATA, "sonic", "") is None)
    for bad in ("backgrund", "COVER ART", "../../etc", "screenshots"):
        check("%r is refused rather than stored to do nothing" % bad,
              framing.set_hero(DATA, "sonic", bad) is None)
        check("  and nothing was written", framing.get_hero(DATA, "sonic") is None)
    check("every media kind is acceptable",
          all(framing.set_hero(DATA, "k", k) == k for k in media.KINDS))

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
