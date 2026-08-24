#!/usr/bin/env python3
"""The language filter must not act on a preference the user never expressed.

`preferred()` deliberately returns [] when nothing is set, and the module says why in as
many words: the hide/ban FILTER "must never act on a preference the user did not
express. Choosing between two assets is not the same act as deleting one."

The filter then tested `lang not in prefs` against that empty list, which is true for
every asset that can be pinned to any language at all. Turning the mode on without first
picking a language therefore hid, or in `ban` mode DELETED AND PERMANENTLY BANNED, every
region-tagged asset in the index. Nothing in the settings couples the two: the mode and
the language list are separate config keys, and the sync worker calls apply_filter() with
no arguments on a schedule.

A ban is durable. `mediaflags.ban` is what stops an asset being fetched again, so this is
not a mistake a re-sync repairs.

Offline. No network.
"""
import json
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-langfilter-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import medialang                                               # noqa: E402
import mediaflags                                              # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


ASSETS = [
    (1, "sonic mania", "cover", "screenscraper", "ss://1", {"region": "us"}),
    (2, "sonic mania", "cover", "screenscraper", "ss://2", {"region": "jp"}),
    (3, "sonic mania", "cover", "screenscraper", "ss://3", {"region": "fr"}),
    (4, "sonic mania", "cover", "steam", "steam://4", {"appid": 1}),   # unpinnable
]


def seed():
    if os.path.exists(medialang.INDEX_DB):
        os.remove(medialang.INDEX_DB)
    con = sqlite3.connect(medialang.INDEX_DB)
    con.execute("CREATE TABLE media(id INTEGER PRIMARY KEY, norm_key TEXT, kind TEXT, "
                "provider TEXT, ref TEXT, meta TEXT, chosen INTEGER DEFAULT 0, "
                "hidden INTEGER DEFAULT 0)")
    con.executemany("INSERT INTO media(id,norm_key,kind,provider,ref,meta,chosen) "
                    "VALUES(?,?,?,?,?,?,1)",
                    [(i, nk, k, p, r, json.dumps(m)) for i, nk, k, p, r, m in ASSETS])
    con.commit()
    con.close()


def surviving():
    con = sqlite3.connect(medialang.INDEX_DB)
    try:
        return {i: h for i, h in con.execute("SELECT id, hidden FROM media")}
    finally:
        con.close()


def main():
    print("the language filter needs a language")

    # ---- ban mode with nothing picked ---------------------------------------- #
    seed()
    res = medialang.apply_filter("ban", prefs=[])
    check("nothing is banned when no language was chosen", res["banned"] == 0)
    check("every asset is still in the index", set(surviving()) == {1, 2, 3, 4})
    check("and nothing was written to the durable ban list",
          mediaflags.banned_set() == set())
    check("the result says why it did nothing", bool(res.get("skipped")))

    # ---- hide mode with nothing picked --------------------------------------- #
    seed()
    res = medialang.apply_filter("hide", prefs=[])
    check("nothing is hidden either", res["hidden"] == 0)
    check("no asset is flagged", not any(surviving().values()))

    # ---- clearing the preference must give the art back ----------------------- #
    con = sqlite3.connect(medialang.INDEX_DB)
    con.execute("UPDATE media SET hidden=1 WHERE id IN (2,3)")   # an earlier filtered run
    con.commit()
    con.close()
    medialang.apply_filter("hide", prefs=[])
    check("a stale hidden flag is cleared once the preference is gone",
          not any(surviving().values()))

    # ---- with a preference, the filter does its job --------------------------- #
    seed()
    res = medialang.apply_filter("ban", prefs=["English"])
    check("off-language assets are banned when a language IS chosen", res["banned"] == 2)
    check("the preferred and the unpinnable ones stay", set(surviving()) == {1, 4})
    check("and the ban is durable",
          ("sonic mania", "cover", "screenscraper", "ss://2") in mediaflags.banned_set())

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
