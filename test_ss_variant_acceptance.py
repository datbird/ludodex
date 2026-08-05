#!/usr/bin/env python3
"""A query variant may widen the SEARCH; it must never widen the ACCEPTANCE (#29).

`_ss_match` searches several cleaned variants of a title — that is what finds "Mega Man
X4" when ScreenScraper files it as "Megaman X4". But it then scored the candidate
against the VARIANT it happened to search with, so a subtitle-stripped variant matched
its own parent game exactly and was accepted as the child's identity:

    Half-Life: Opposing Force      -> variant "Half-Life"      -> SS 13493 (Half-Life)
    Sid Meier's Civilization IV:
      Beyond the Sword             -> variant "Civilization IV"-> SS 19187 (Civ IV)
    Police Quest II: The Vengeance -> variant "Police Quest"   -> SS 31435 (shared w/ PQ1)

Live this bound 191 titles onto 86 ScreenScraper ids. The loser of each collision then
inherits the winner's art and metadata and looks merely mediocre rather than broken —
which is how Police Quest: In Pursuit of the Death Angel came to display Police Quest
II's cover.

The rule: score every candidate against the OWNED title. Variants exist to find rows
ScreenScraper spells differently, not to redefine what game we asked for.

Offline. No network.
"""
import os
import sys

import test_support

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    test_support.isolate("ludodex-ssvar-")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from server import app as srv

    ok = srv._ss_candidate_score

    # --- the collisions this exists to stop -------------------------------------
    check("a parent game is NOT accepted for its expansion",
          ok(["Half-Life: Opposing Force"], "Half-Life")[0] is False)
    check("a base game is NOT accepted for its expansion pack",
          ok(["Sid Meier's Civilization IV: Beyond the Sword"],
             "Sid Meier's Civilization IV")[0] is False)
    check("a series root is NOT accepted for a numbered entry",
          ok(["Police Quest II: The Vengeance"], "Police Quest")[0] is False)
    check("a sequel is NOT accepted for the first game",
          ok(["Police Quest: In Pursuit of the Death Angel"],
             "Police Quest II: The Vengeance")[0] is False)
    check("a collection is NOT accepted for a single entry",
          ok(["Forgotten Realms: The Archives - Collection One"],
             "Forgotten Realms: The Archives")[0] is False)

    # --- what must still match --------------------------------------------------
    check("an exact title matches", ok(["Half-Life"], "Half-Life")[0] is True)
    check("word-break differences still match (the reason variants exist)",
          ok(["Mega Man X4"], "Megaman X4")[0] is True)
    check("punctuation differences still match",
          ok(["Sid Meier's Civilization IV"], "Sid Meiers Civilization IV")[0] is True)
    check("a provider's extra edition words are tolerated",
          ok(["Mega Man X4"], "Mega Man X4 (Rockman X4)")[0] is True)
    check("the OWNED title is what counts, not the variant that found it",
          ok(["Castlevania: Lords of Shadow - Mirror of Fate HD"],
             "Castlevania: Lords of Shadow - Mirror of Fate HD")[0] is True)
    check("any one of several owned spellings is enough",
          ok(["Rockman X4", "Mega Man X4"], "Mega Man X4")[0] is True)

    # --- scoring still ranks ----------------------------------------------------
    exact = ok(["Half-Life"], "Half-Life")[1]
    loose = ok(["Half-Life"], "Half-Life: Blue Shift")[1]
    check("an exact name outranks a longer one", exact > loose)
    y_hit = ok(["Doom"], "Doom", year=1993, cand_year="1993")[1]
    y_miss = ok(["Doom"], "Doom", year=1993, cand_year="2016")[1]
    check("a matching year still adds to the score", y_hit > y_miss)

    # --- degenerate input -------------------------------------------------------
    check("an empty candidate name never matches", ok(["Doom"], "")[0] is False)
    check("an empty owned title never matches", ok([""], "Doom")[0] is False)
    check("no owned titles never matches", ok([], "Doom")[0] is False)

    print("\n%d/%d passed" % (sum(1 for _, ok_ in PASS if ok_), len(PASS)))


if __name__ == "__main__":
    main()
