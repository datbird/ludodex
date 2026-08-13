#!/usr/bin/env python3
"""A full refresh may redo a derivation. It must not redo a DECISION.

`--all` ignores the caches and re-resolves every identity from scratch. That is correct
for anything we worked out and wrong for anything that was decided: it spared hand pins
(and the code says why — "a routine full sync silently reverts every manual pin, and can
even leave one UNMATCHED if the name search now misses") but not AI-confirmed matches.

Those are the worst possible ones to drop. An identity gets `ai_name` precisely BECAUSE
deterministic matching refused it, so a full refresh re-runs the search that already
failed and discards the answer when it fails again. Live, all 8 were games name-matching
had given up on — Crash Bandicoot 3, Phantasy Star IV, Civilization IV among them — and
zero manual pins existed, so the protection that did exist was guarding an empty set.

Losing an identity is not cosmetic: it drives `game_key`, so the entry loses its IGDB
metadata, reverts to its raw parsed title, and can no longer see its own neutral art
(matched on `game_key`). Three were dropped in one observed run and restored by hand.
"""
import os
import sqlite3
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import test_support
    test_support.isolate("ludodex-alldec-")
    import igdb_enrich

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE igdb_resolution(norm_key TEXT PRIMARY KEY, igdb_id INTEGER,"
                " slug TEXT, matched_by TEXT, resolved_at INTEGER)")

    def add(nk, iid, by):
        con.execute("INSERT INTO igdb_resolution(norm_key,igdb_id,matched_by,resolved_at)"
                    " VALUES(?,?,?,0)", (nk, iid, by))

    add("hand pinned", 111, "manual")
    add("hand pinned as nothing", 0, "manual")       # a deliberate "matches nothing"
    add("ai confirmed", 1187, "ai_name")             # crash bandicoot 3 warped, live
    add("ai found nothing", 0, "ai_name")            # an attempt, not a decision
    add("by steam appid", 222, "steam_appid")
    add("by name search", 333, "name")
    add("negative cache", 0, "none")

    kept = igdb_enrich.decided_identities(con)

    print("1. decisions survive a full refresh")
    check("a hand pin is kept", "hand pinned" in kept)
    check("a deliberate 'matches nothing' pin is kept too",
          "hand pinned as nothing" in kept)
    check("an AI-confirmed identity is kept — THE BUG", "ai confirmed" in kept)

    print()
    print("2. derivations are still redone, or the flag would mean nothing")
    check("a steam appid match is re-derived", "by steam appid" not in kept)
    check("a name-search match is re-derived", "by name search" not in kept)
    check("the negative cache is retried", "negative cache" not in kept)

    print()
    print("3. a failed AI attempt is not a decision")
    # #25's lesson: a decision is respected, the ABSENCE of one is retried. An AI row
    # with no id found nothing — freezing it would make that miss permanent.
    check("an AI row with no id stays retryable", "ai found nothing" not in kept)

    print()
    print("4. the rule lives in one place")
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "igdb_enrich.py")).read()
    check("the resolver consults decided_identities()", "decided_identities(con)" in src)
    check("...and no longer hardcodes the manual-only test there",
          "WHERE matched_by='manual'\")}" not in src)
    check("a new AI provenance joins by editing one tuple",
          "AI_DECIDED = (" in src)

    print()
    print("5. it holds for the live shape")
    con2 = sqlite3.connect(":memory:")
    con2.execute("CREATE TABLE igdb_resolution(norm_key TEXT PRIMARY KEY, igdb_id INTEGER,"
                 " slug TEXT, matched_by TEXT, resolved_at INTEGER)")
    live = [("steam_appid", 2070, 1), ("name", 132, 1), ("none", 48, 0), ("ai_name", 8, 1)]
    for by, n, real in live:
        for i in range(n):
            con2.execute("INSERT INTO igdb_resolution(norm_key,igdb_id,matched_by,"
                         "resolved_at) VALUES(?,?,?,0)", ("%s-%d" % (by, i), i + 1 if real else 0, by))
    k2 = igdb_enrich.decided_identities(con2)
    check("exactly the 8 AI identities are spared (got %d)" % len(k2), len(k2) == 8)

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
