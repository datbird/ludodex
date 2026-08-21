#!/usr/bin/env python3
"""A stated year that matches no candidate is a REFUSAL, not a tiebreak.

Live case, 2026-08-21. The review queue proposed binding the owned Steam game
**Star Trek** (appid 203250) to **igdb:11485** — whose record is the 1971 mainframe
game. The AI had said 2013 and named Digital Extremes in the same finding, so identity
and attributes contradicted each other inside one payload.

Nothing upstream was wrong. IGDB's `external_ids` carries 173,641 Steam ids and 203250
is not one of them; no IGDB record named "Star Trek" carries a Steam id at all. The
appid path recorded a correct miss (`('star trek', 0, None, 'none')`, one of 52 out of
2,260) and the game went to the AI.

The defect is the tail of `_provider_match()`:

    best = sorted(cands.values(),
                  key=lambda h: (0 if (year and h.get("year") == year) else 1,
                                 h.get("year") or 9999))[0]

Six IGDB records are named exactly "Star Trek" — 1971, 1973, 1987, and three carrying no
year at all. None is 2013. So every candidate falls to tier 1 and the tiebreak takes the
earliest: 1971.

Four lines above it sits the rule this violates —

    if not cands:
        return None                 # no trustworthy IGDB entry — better none than wrong

— applied to the candidate SET but never to the YEAR. A stated year matching nothing is
evidence of a miss, and the code reads it as a starting point for a guess.

Undated candidates are why this cannot be fixed by tightening the year alone: three of
the six carry no year, so they can neither confirm nor refuse 2013. 23.8% of the IGDB
mirror (88,453 of 371,978 records) is undated, and 7,149 of those share a name with a
dated record. An absent year is not evidence, and a ranking must not convert it into one.

What must keep working: the Gradius and Contra cases the tiebreak was written for. IGDB's
relevance search buries the original ("Gradius" returns only its sequels) and ranks a
modern re-release first ("Contra" returns the 2006 remake), which is why the exact-name
index is merged in. Once merged, those resolve to a SINGLE exact-title record — so a lone
candidate must still bind, with or without a year.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
test_support.isolate("ludodex-pickyear-")

import matchgate                                 # noqa: E402


def check(label, cond):
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def c(iid, year=None, name="Star Trek"):
    return {"igdb_id": iid, "name": name, "year": year}


# The live candidate set for "Star Trek", read from the IGDB mirror on 2026-08-21.
STAR_TREK = [c(11485, 1971), c(326626, 1973), c(247203, 1987),
             c(80425, None), c(131474, None), c(218919, None)]


def main():
    check("no candidate carries the stated year -> None (the Star Trek defect)",
          matchgate.pick_by_year(STAR_TREK, 2013) is None)

    check("the 1971 record is not returned for a 2013 game",
          matchgate.pick_by_year(STAR_TREK, 2013) != STAR_TREK[0])

    check("exactly one candidate carries the stated year -> that candidate",
          matchgate.pick_by_year(STAR_TREK, 1987) == c(247203, 1987))

    check("a lone exact-title candidate binds with no year (Gradius / Contra)",
          matchgate.pick_by_year([c(4598, 1987, "Contra")], None)
          == c(4598, 1987, "Contra"))

    check("a lone exact-title candidate binds even when itself undated",
          matchgate.pick_by_year([c(80425, None)], None) == c(80425, None))

    check("no year stated and several candidates -> None, never an arbitrary pick",
          matchgate.pick_by_year(STAR_TREK, None) is None)

    check("two candidates share the stated year -> None (cannot be told apart)",
          matchgate.pick_by_year([c(1, 1993), c(2, 1993)], 1993) is None)

    check("an empty candidate set is a refusal",
          matchgate.pick_by_year([], 2013) is None)

    # An absent year is not evidence, in EITHER direction. `game_era()` established
    # that rule from 123 live false refusals, and it holds on the candidate side too:
    # a lone exact-title record binds even when IGDB never dated it, because its
    # undated-ness is not an argument against it. 23.8% of the mirror is undated —
    # refusing all of them would drop real matches wholesale.
    check("a lone undated candidate still binds against a stated year",
          matchgate.pick_by_year([c(80425, None)], 2013) == c(80425, None))

    # But it may never WIN a contest. Among several, an undated record cannot be the
    # one the year picked out, so it neither satisfies nor blocks the decision.
    check("among several, an undated candidate never satisfies a stated year",
          matchgate.pick_by_year([c(80425, None), c(131474, None)], 2013) is None)

    check("a dated candidate wins over an undated sibling on the stated year",
          matchgate.pick_by_year([c(80425, None), c(247203, 1987)], 1987)
          == c(247203, 1987))

    print("test_pick_by_year: all checks passed")


if __name__ == "__main__":
    main()
