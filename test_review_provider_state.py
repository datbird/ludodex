#!/usr/bin/env python3
"""The review page must say WHICH providers matched, not just "no match" (#36).

EVGA Precision X1 came out of the reset with a SteamGridDB id and recorded misses for
IGDB and ScreenScraper — the right answer, since neither catalogues a GPU utility. The
review card said only that no match was found, which invites a reviewer to go fix
something already correct.

Three buckets, because they are three different claims:

  matched      a provider returned an id
  missed       a provider was asked and returned nothing — a recorded MISS, retried
               after its TTL. "We looked" is not "it does not exist."
  unattempted  never asked at all

Collapsing those is the same mistake the negative-cache work was about, one layer up in
the UI instead of in the cache.

Offline.
"""
import os
import sqlite3
import sys

import test_support

PASS = []


def check(l, c):
    PASS.append(c); print("  %s   %s" % ("ok " if c else "FAIL", l))
    if not c:
        sys.exit("FAILED: " + l)


def main():
    d = test_support.isolate("ludodex-revprov-")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from server import app as srv

    mc = sqlite3.connect(os.path.join(d, "metadata-cache.sqlite"))
    mc.executescript("""
    CREATE TABLE ss_resolution(norm_key TEXT PRIMARY KEY, ss_id INT);
    CREATE TABLE sgdb_resolution(norm_key TEXT PRIMARY KEY, sgdb_id INT);
    CREATE TABLE igdb_resolution(norm_key TEXT PRIMARY KEY, igdb_id INT);
    """)
    # the live EVGA shape: SGDB has it, the two game databases correctly do not
    mc.execute("INSERT INTO sgdb_resolution VALUES('evga', 3580)")
    mc.execute("INSERT INTO ss_resolution VALUES('evga', 0)")
    mc.execute("INSERT INTO igdb_resolution VALUES('evga', 0)")
    # a game nobody has been asked about yet
    mc.execute("INSERT INTO ss_resolution VALUES('fresh', 0)")
    mc.commit(); mc.close()

    st = srv._provider_match_state("evga")
    check("a provider that returned an id is reported as matched",
          [m["provider"] for m in st["matched"]] == ["steamgriddb"])
    check("its id comes with it, so the card can link out",
          st["matched"][0]["id"] == "3580")
    check("providers that were asked and found nothing are listed apart",
          sorted(st["missed"]) == ["igdb", "screenscraper"])
    check("nothing is claimed unattempted when all three were asked",
          st["unattempted"] == [])

    st = srv._provider_match_state("fresh")
    check("a provider with NO row is unattempted, not a miss",
          sorted(st["unattempted"]) == ["igdb", "steamgriddb"]
          and st["missed"] == ["screenscraper"])

    st = srv._provider_match_state("never-heard-of-it")
    check("an unknown game is unattempted everywhere, never 'no match'",
          len(st["unattempted"]) == 3 and not st["matched"] and not st["missed"])

    # the UI must actually render both, and must not have kept the old blanket wording
    ui = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "web", "src", "App.tsx")).read()
    check("the card shows what already matched", "Already matched:" in ui)
    check("the card names what was searched without success",
          "Could not match against:" in ui)
    check("the matched chip carries the provider ids in its tooltip",
          "providerLabel(m.provider)}" in ui and "m.id" in ui)

    # --- the cap that hid all of this ------------------------------------------
    # The review response used to attach context only when `len(findings) <= 60`. The
    # first reset produced 68, so every card lost every fact — filename, platform,
    # folder, provenance, current match — at the exact moment a reviewer needed them,
    # because a large batch is harder to judge than a small one, not easier.
    srv_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "server", "app.py")).read()
    # the STATEMENT, not the substring — the fix's own comment quotes the old line, and
    # this is the third time that has tripped a guard here. Check for executable code.
    check("context is no longer withheld from batches over 60 findings",
          not any(l.strip().startswith("if len(findings) <=")
                  for l in srv_src.splitlines()))
    check("the bound is on distinct GAMES, which is where the cost actually is",
          "len(ctx_cache) < CONTEXT_GAME_CAP" in srv_src)
    check("the cap is generous enough for a real reset batch",
          srv.CONTEXT_GAME_CAP >= 200)
    check("and hitting it is REPORTED, not silent",
          '"context_truncated"' in srv_src)

    print("\n%d/%d passed" % (sum(PASS), len(PASS)))


main()
