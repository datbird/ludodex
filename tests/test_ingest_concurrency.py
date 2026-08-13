#!/usr/bin/env python3
"""The ingest must use the concurrency it already proved safe (#32).

The standalone "match providers" job has run on a thread pool for a long time. The
IMPORT called `_match_providers(all_keys)` in ONE sequential pass, and `_ai_art_pass`
looped serially over every game. Measured on this library: a 209-title re-match took 40
minutes at four workers, so 2,257 games single-threaded is about seven hours on the one
phase everything downstream waits for; the vision pass took 4h22m at ~7s per game,
nearly all of it waiting on the model.

Same work, same order per game, same verdicts — only the waiting overlaps. Two copies of
a concurrency policy is how one of them stayed sequential without anyone noticing, so
there is now one.

Offline: source-level, because the defect is a call site that never learned.
"""
import os
import sys

import test_support

PASS = []


def check(l, c):
    PASS.append(c); print("  %s   %s" % ("ok " if c else "FAIL", l))
    if not c:
        sys.exit("FAILED: " + l)


def main():
    test_support.isolate("ludodex-conc-")
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ludodex")
    sys.path.insert(0, d)
    src = open(os.path.join(os.path.dirname(d), "server", "app.py")).read()

    check("there is one shared parallel matcher", "def _parallel_match(" in src)
    check("the IMPORT uses it rather than a sequential sweep",
          "_parallel_match(_keys" in src)
    check("the worker count comes from ScreenScraper's advertised budget",
          "def _ss_workers(" in src and "maxthreads" in src)
    # count the LOOKUP, not the word — the surrounding comments mention it too, and a
    # check that cannot tell code from prose about code is not a check.
    # ONE function reads it. `_ss_workers` itself checks both shapes of the block,
    # which is two lookups in one place — the thing being forbidden is a SECOND place.
    i = src.index("def _ss_workers(")
    j = src.index("\ndef ", i + 10)
    check("the standalone job shares that policy, not its own copy",
          src.count('.get("maxthreads")') == src[i:j].count('.get("maxthreads")'))
    check("the matcher reports progress while it runs",
          "Matching providers %d/%d" in src)

    check("the vision pass is concurrent",
          "ThreadPoolExecutor(max_workers=AI_ART_WORKERS)" in src)
    check("its concurrency is bounded for the spend cap's sake",
          "AI_ART_WORKERS = " in src)
    check("the budget check can still STOP the pass",
          "stop.set()" in src and "check_limit" in src)
    check("the vision pass reports progress while it runs",
          "AI art%s — judged %d/%d game(s)" in src)

    sys.path.insert(0, os.path.dirname(d))   # `server` lives beside the package
    from server import app as srv
    w = srv._ss_workers()
    check("workers stay within what the provider advertises (got %d)" % w,
          1 <= w <= 6)
    # the shape bug: user_info returns ssuser ALREADY unwrapped, and reading it as if
    # nested returned None — so this quietly ran at the 2-worker fallback while
    # ScreenScraper advertised 6.
    check("a flat ssuser block is read, not just a nested one",
          '_info.get("maxthreads")' in src)
    check("vision concurrency is small enough that a cap overshoots by little",
          srv.AI_ART_WORKERS <= 6)

    print("\n%d/%d passed" % (sum(PASS), len(PASS)))


main()
