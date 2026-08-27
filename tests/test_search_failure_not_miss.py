#!/usr/bin/env python3
""""We failed to look" must never be recorded as "it isn't there".

datbird: "Does this really not have a SS match?" — Mass Effect 2. It does. The index
already held 83 ScreenScraper media rows for that entry, and Mass Effect and Mass Effect 3
both matched (ss 15867 and 15742). Yet `ss_resolution` recorded `(0, 'none')` for it.

`_ss_match` returns None for three very different situations:

  1. searched, found nothing              — a real miss, worth recording
  2. every search attempt ERRORED         — we never looked
  3. the wall-clock budget ran out first  — we stopped looking part-way

Only the first is an answer. Two and three are non-answers, and writing them down as
misses is the #25 defect in a worse form: not "a recorded miss became permanent", but "a
FAILURE became a recorded miss". The sweep did that across the library.

The fix is that the searcher must distinguish them — raise when it could not complete —
so `provider_ids.resolve`, which already declines to record on an exception, leaves the
cache untouched and the game is retried.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-ssfail-")

import provider_ids                              # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    from server import app as srv
    import screenscraper as ss

    srv.config.set_("screenscraper_user", "u")
    srv.config.set_("screenscraper_pass", "p")
    srv.config.set_("screenscraper_devid", "d")
    srv.config.set_("screenscraper_devpassword", "x")
    if not srv.config.screenscraper_creds():
        print("  (skipping live-shaped cases: no creds configured in this env)")

    real = ss.jeu_recherche
    try:
        print("1. searched successfully, found nothing -> a real miss (returns None)")
        ss.jeu_recherche = lambda creds, q, systemeid=None, limit=8: []
        got = srv._ss_match(["Definitely Not A Real Game 99"], ["pc"])
        check("a genuine miss still returns None", got is None)

        print("2. every search ERRORS -> raises, so nothing is recorded as a miss")
        def boom(creds, q, systemeid=None, limit=8):
            raise RuntimeError("screenscraper timed out")
        ss.jeu_recherche = boom
        raised = False
        try:
            srv._ss_match(["Mass Effect 2"], ["pc"])
        except Exception:
            raised = True
        check("a total search failure raises rather than returning None", raised)

        print("3. resolve() does NOT record when the searcher raises")
        con = sqlite3.connect(":memory:")
        provider_ids.ensure_tables(con)

        def failing(title, systems):
            raise RuntimeError("provider unreachable")
        rc = provider_ids.resolve(con, "screenscraper", "mass effect 2",
                                  "Mass Effect 2", ["pc"], failing)
        check("it returns 0 for this run", rc == 0)
        check("but writes NO row, so the game is retried",
              provider_ids.cached(con, "screenscraper", "mass effect 2",
                                  platform="pc") is None)

        print("4. a genuine miss IS recorded (we must not re-search forever)")
        rc = provider_ids.resolve(con, "screenscraper", "nothing here",
                                  "Nothing Here", ["pc"], lambda t, s: None)
        check("returns 0", rc == 0)
        check("and records the miss",
              provider_ids.cached(con, "screenscraper", "nothing here",
                                  platform="pc") is not None)
    finally:
        ss.jeu_recherche = real

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
