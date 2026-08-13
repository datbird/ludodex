#!/usr/bin/env python3
"""A MATCH IS NOT AN INGEST — provider identity is recorded on its own (#21b, #21c).

datbird's decision, 2026-08-01: every configured provider is matched for every game,
whether or not any metadata or media is ever taken from it. The match is what makes a
later on-demand pull possible, and it is what the Matched-providers menu shows.

The code did the opposite. Both providers HAD a working matcher, and both threw the
answer away:

  * ScreenScraper — `_ss_match` runs inside `_pull_ss_media`, so an id exists only as a
    side effect of pulling art. Live: 151 of 2255 entries linked.
  * SteamGridDB — `_sgdb_game_id` runs inside `fetch_steamgriddb_targets`, whose `todo`
    list SKIPS any game that already has a cover/hero/logo. So a game with IGDB art never
    gets an SGDB id at all. Live: 0 links, against 177 entries holding SGDB media.

System Shock: Classic showed IGDB + Steam and nothing else, and a fresh ingest would have
produced exactly that again.

This pins the identity layer: resolve once, cache it, and let the media pull CONSUME the
identity rather than re-derive it.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-provid-")

import provider_ids                              # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    con = sqlite3.connect(":memory:")
    provider_ids.ensure_tables(con)

    print("1. a match is recorded and the searcher is not called twice")
    calls = []

    def ss_search(title, systems):
        calls.append(title)
        return {"ss_id": 4242, "name": "System Shock"}

    got = provider_ids.resolve(con, "screenscraper", "system shock classic",
                               "System Shock: Classic", ["pc"], ss_search)
    check("the id comes back", got == 4242)
    check("it was searched once", len(calls) == 1)
    again = provider_ids.resolve(con, "screenscraper", "system shock classic",
                                 "System Shock: Classic", ["pc"], ss_search)
    check("the second call is served from cache", again == 4242 and len(calls) == 1)

    print("2. a MISS is remembered, but is not a decision")
    misses = []

    def no_match(title, systems):
        misses.append(title)
        return None

    got = provider_ids.resolve(con, "screenscraper", "obscure thing", "Obscure Thing",
                               ["pc"], no_match)
    check("a miss returns 0, not None-as-error", got == 0)
    check("the miss was recorded", provider_ids.cached(con, "screenscraper",
                                                       "obscure thing") is not None)
    provider_ids.resolve(con, "screenscraper", "obscure thing", "Obscure Thing",
                         ["pc"], no_match)
    check("a fresh miss is not re-searched immediately", len(misses) == 1)
    # ...but it must not be permanent. That is the #25 lesson: a recorded miss is the
    # ABSENCE of a decision, so a later, better-informed pass may try again.
    con.execute("UPDATE ss_resolution SET resolved_at=0 WHERE norm_key='obscure thing'")
    provider_ids.resolve(con, "screenscraper", "obscure thing", "Obscure Thing",
                         ["pc"], no_match)
    check("a STALE miss is retried", len(misses) == 2)

    print("3. a manual decision is never overwritten by a search")
    provider_ids.record(con, "screenscraper", "hand picked", 7, "Chosen", "manual")
    picked = []
    provider_ids.resolve(con, "screenscraper", "hand picked", "Hand Picked", ["pc"],
                         lambda t, s: picked.append(t) or {"ss_id": 999, "name": "X"})
    check("no search was made", picked == [])
    check("the manual id stands",
          provider_ids.cached(con, "screenscraper", "hand picked")[0] == 7)

    print("4. steamgriddb is the same layer, its own table")
    got = provider_ids.resolve(con, "steamgriddb", "system shock classic",
                               "System Shock: Classic", None,
                               lambda t, s: {"sgdb_id": 55, "name": "System Shock"})
    check("sgdb id recorded", got == 55)
    check("it did not land in the screenscraper table",
          provider_ids.cached(con, "screenscraper", "system shock classic")[0] == 4242)

    print("5. an unknown provider is refused rather than silently ignored")
    try:
        provider_ids.resolve(con, "nintendo", "x", "X", None, lambda t, s: None)
        bad = False
    except ValueError:
        bad = True
    check("an unsupported provider raises", bad)

    print("6. a falsy id from a searcher is a MISS, never an identity")
    # The igdb:0 lesson: writing a falsy id as a real one makes every entry that shares
    # it share an identity.
    got = provider_ids.resolve(con, "steamgriddb", "zero id", "Zero Id", None,
                               lambda t, s: {"sgdb_id": 0, "name": "nope"})
    check("a zero id is stored as a miss", got == 0)
    check("and nothing claims it is identified",
          not provider_ids.is_identified(con, "steamgriddb", "zero id"))

    print("7. is_identified distinguishes a real match from a recorded miss")
    check("real match is identified",
          provider_ids.is_identified(con, "screenscraper", "system shock classic"))
    check("miss is not identified",
          not provider_ids.is_identified(con, "screenscraper", "obscure thing"))

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
