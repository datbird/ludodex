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

    # --- a distinguishing word is not optional -----------------------------------
    # 0.8 coverage tolerates one dropped token, which is right for a long title losing
    # an article and wrong when the dropped token is the ONLY thing telling two products
    # apart. Live leftovers after the first pass, all of which passed at exactly 0.8:
    check("an X in the series name is distinguishing",
          ok(["Mega Man X Legacy Collection"], "Mega Man Legacy Collection")[0] is False)
    check("a sequel number is distinguishing",
          ok(["Warhammer 40,000: Boltgun 2"], "Warhammer 40,000: Boltgun")[0] is False)
    check("an expansion name is distinguishing",
          ok(["Sid Meier's Civilization IV: Warlords"],
             "Sid Meier's Civilization IV")[0] is False)
    check("a VR edition is a different product",
          ok(["Arcade Paradise VR"], "Arcade Paradise")[0] is False)
    check("a DLC pack is not the base game",
          ok(["Cult of the Lamb: Heretic Pack"], "Cult of the Lamb")[0] is False)
    check("a differently-named sibling is not the same game",
          ok(["Ninja Gaiden II Black"], "Ninja Gaiden Sigma 2")[0] is False)
    check("Heroes of X is not X",
          ok(["Heroes of Hammerwatch II"], "Hammerwatch II")[0] is False)

    # ...while the words that carry no identity still must not block a match
    check("our own disambiguating year suffix is not a distinguishing word",
          ok(["Mass Effect 2 (2021)"], "Mass Effect 2")[0] is True)
    check("an edition word the provider omits is tolerated",
          ok(["DOOM Eternal: Deluxe Edition"], "DOOM Eternal")[0] is True)
    check("a trailing remaster word is tolerated",
          ok(["Shadow of the Colossus Remastered"], "Shadow of the Colossus")[0] is True)
    check("articles are not distinguishing",
          ok(["The Last of Us"], "Last of Us")[0] is True)

    # --- an era is distinguishing when both sides know their year --------------
    # The NOISE rule strips a 4-digit year so our own disambiguated "Mass Effect 2
    # (2021)" matches the provider's "Mass Effect 2". For an original and its remake
    # that is exactly backwards: the title is IDENTICAL and the year is the only thing
    # that separates them. Live, Resident Evil 4 (2023) was bound to ScreenScraper 4750
    # — the 2005 GameCube original — and then wore its GameCube box.
    check("a remake does not take its original's record",
          ok(["Resident Evil 4"], "Resident Evil 4", year=2023,
             cand_year="2005")[0] is False)
    check("...nor the original its remake's",
          ok(["Resident Evil 4"], "Resident Evil 4", year=2005,
             cand_year="2023")[0] is False)
    check("the same game in the same year still matches",
          ok(["Resident Evil 4"], "Resident Evil 4", year=2023,
             cand_year="2023")[0] is True)
    check("a one-year regional release difference is tolerated",
          ok(["Chrono Trigger"], "Chrono Trigger", year=1995,
             cand_year="1996")[0] is True)
    check("an unknown candidate year cannot refuse a match",
          ok(["Resident Evil 4"], "Resident Evil 4", year=2023)[0] is True)
    check("an unknown owned year cannot refuse a match",
          ok(["Resident Evil 4"], "Resident Evil 4", cand_year="2005")[0] is True)
    check("a non-numeric year is treated as unknown, never as a mismatch",
          ok(["Resident Evil 4"], "Resident Evil 4", year=2023,
             cand_year="n/a")[0] is True)

    # ...and the identity matcher has to actually PASS the year it knows
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "server", "app.py")).read()
    mp = src[src.index("def _match_providers"):]
    mp = mp[:mp.index("\ndef ", 10)]
    check("_match_providers gives ScreenScraper the release year",
          "_ss_match([q], s, year)" in mp)

    # --- ONE gate, every provider ------------------------------------------------
    # Each provider got this wrong differently: ScreenScraper judged against the variant
    # it searched, SteamGridDB did not judge at all (`return items[0].get("id")`).
    # A second copy of the rule is how they drifted apart in the first place.
    import matchgate
    import media_fetch
    check("the server's SS gate delegates to the shared one",
          srv._ss_candidate_score(["Doom"], "Doom") == matchgate.score(["Doom"], "Doom"))
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "media_fetch.py")).read()
    # match the STATEMENT, not the substring — the fix's own comment quotes the old
    # line, and a check that cannot tell code from a comment about code is not a check.
    check("SteamGridDB no longer takes the first autocomplete row unchecked",
          not any(l.strip().startswith("return items[0]") for l in src.splitlines()))
    check("SteamGridDB uses the shared gate", "matchgate.score" in src)
    check("media_fetch imports it rather than reimplementing it",
          "import matchgate" in src)

    print("\n%d/%d passed" % (sum(1 for _, ok_ in PASS if ok_), len(PASS)))


if __name__ == "__main__":
    main()
