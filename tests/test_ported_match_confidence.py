#!/usr/bin/env python3
"""Match confidence is a JUDGEMENT about identity, and nothing else asserted it.

`matchconf.match_confidence` / `ss_match_confidence` decide the number that drives the
`confidence:low` library facet, the wand's gray zone, and the `match_confidence` game
attribute written by the surgical apply. They are pure functions over three signals —
the match SOURCE, the TITLE anchor (`igdb_enrich._name_anchor_class`), and PLATFORM fit —
and the whole point of the scorer is the SHAPE of the drop:

  * a manual pin is ground truth and outranks every other signal, including a platform
    the game demonstrably never shipped on;
  * a store-ID match is certain regardless of platform (a Jaguar Doom sharing a Steam
    appid is still Doom), so it is never platform-penalised;
  * an INTERIOR title match is the pre-2026-07-15 fuzzy-matcher's failure mode
    ('journey' bound to 'The Sims 4: Journey to Batuu') and must fall below the
    threshold, while an ANCHORED subtitle variant ('1943' -> '1943: The Battle of
    Midway') is a legitimate variant and must stay above it. A scorer that cannot tell
    those apart either keeps every bad match or throws away every good one;
  * a platform IGDB does not list is a soft dip when the era is plausible and a hard
    one when the hardware predates the game — the difference between "IGDB's platform
    list is thin" and "this is the wrong game".

`_name_anchor_class` is asserted here directly because it is the input those rules are
built on, and because the same four classes gate the legacy fuzzy-match scrub.

Offline. Pure functions only, no database and no network.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

test_support.isolate("ludodex-ported-matchconf-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import igdb_enrich                                             # noqa: E402
import matchconf                                               # noqa: E402
import platmap                                                 # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


# IGDB record fixtures: {name, platforms:[{name}]} — the shape the resolver hands over.
SIMS4 = {"name": "The Sims 4: Journey to Batuu",
         "platforms": [{"name": "PC (Microsoft Windows)"}, {"name": "PlayStation 4"}]}
TR_1943 = {"name": "1943: The Battle of Midway",
           "platforms": [{"name": "Arcade"}, {"name": "NES"}]}
CONTRA = {"name": "Contra", "platforms": [{"name": "NES"}, {"name": "Arcade"}]}
CONTRA_PS1_ONLY = {"name": "Contra", "platforms": [{"name": "PlayStation"}]}
THIN = {"name": "Contra"}                       # IGDB knows the name, lists no platforms

THRESHOLD = 60                                  # the `confidence:low` facet's cut


def score(*a):
    return matchconf.match_confidence(*a)[0]


def reason(*a):
    return matchconf.match_confidence(*a)[1]


def main():
    print("match confidence scores identity, and the shape of the drop is the point")

    print()
    print("1. a manual pin is ground truth — no signal can pull it down")
    # 'journey' inside 'The Sims 4' on ARCADE is the worst case the scorer has: an
    # interior title AND hardware the game could never have shipped on. Pinned, it is
    # still 100, because the user said so.
    check("manual survives an interior title on impossible hardware: %d"
          % score("manual", "journey", SIMS4, "arcade"),
          score("manual", "journey", SIMS4, "arcade") == 100)
    check("and says why", reason("manual", "journey", SIMS4, "arcade") == "pinned by hand")

    print()
    print("2. a store-ID match is certain regardless of platform")
    check("steam_appid with no metadata at all: %d" % score("steam_appid", "whatever", {}, "pc"),
          score("steam_appid", "whatever", {}, "pc") == 96)
    check("steam_appid is NOT platform-penalised (a Jaguar Doom is still Doom)",
          score("steam_appid", "journey", SIMS4, "arcade")
          == score("steam_appid", "whatever", {}, "pc"))

    print()
    print("3. an exact title on a listed platform is the clean case")
    check("name + exact + fits: %d" % score("name", "contra", CONTRA, "nes"),
          score("name", "contra", CONTRA, "nes") == 85)
    check("and it is above the low-confidence cut",
          score("name", "contra", CONTRA, "nes") >= THRESHOLD)

    print()
    print("4. the 'journey' class — an interior title match — falls below the cut")
    s, r = matchconf.match_confidence("name", "journey", SIMS4, "arcade")
    check("interior + impossible platform scores low: %d" % s, s < THRESHOLD)
    check("and the reason names BOTH factors, not just one: %r" % r,
          "interior" in r and "impossible" in r)
    sa = score("ai_name", "journey", SIMS4, "arcade")
    check("an AI-proposed interior match is low too: %d" % sa, sa < THRESHOLD)

    print()
    print("5. an ANCHORED subtitle variant is legitimate and stays above the cut")
    # This is the check that stops the scorer from being a title-length filter: '1943'
    # really is '1943: The Battle of Midway', and arcade really is one of its platforms.
    s = score("name", "1943", TR_1943, "arcade")
    check("'1943' -> '1943: The Battle of Midway' on arcade: %d" % s, s >= THRESHOLD)
    check("but it is scored BELOW an exact title, not equal to one",
          s < score("name", "contra", CONTRA, "nes"))

    print()
    print("6. platform-not-listed: a soft dip when plausible, a hard one when impossible")
    soft = score("name", "contra", CONTRA_PS1_ONLY, "sega saturn")   # both gen 5
    hard = score("name", "contra", CONTRA_PS1_ONLY, "atari 2600")    # gen 2 << gen 5
    check("same-generation no-fit dips but survives: %d" % soft,
          THRESHOLD <= soft < 85)
    check("and says the platform is merely not listed",
          "not listed" in reason("name", "contra", CONTRA_PS1_ONLY, "sega saturn"))
    check("hardware older than the game's debut is penalised harder: %d < %d"
          % (hard, soft), hard < soft)
    check("and says so", "impossible" in reason("name", "contra", CONTRA_PS1_ONLY,
                                                "atari 2600"))
    # The generation gap is what separates the two, so the ontology has to carry it.
    check("the generation gap is real, not assumed",
          platmap.GEN[platmap.canon("atari 2600")] < platmap.GEN[platmap.canon("playstation")])

    print()
    print("7. thin metadata is not punished")
    check("no platforms listed -> no platform penalty: %d"
          % score("name", "contra", THIN, "nes"),
          score("name", "contra", THIN, "nes") == 85)
    check("a None candidate does not raise, and is not punished for it",
          score("name", "contra", None, "nes") == 85)
    check("an unknown/legacy match source falls back to a middling base: %d"
          % score("legacy", "contra", CONTRA, "nes"),
          score("legacy", "contra", CONTRA, "nes") == 65)

    print()
    print("8. ScreenScraper identity is scored on the same rules, SS-shaped")
    SS = matchconf.ss_match_confidence
    check("a pin is a pin", SS("manual", "journey", ["Whatever"], {"nes"}, "arcade")[0] == 100)
    check("an SS id beats an SS name match: %d > %d"
          % (SS("ss_id", "contra", ["Contra"], {"nes"}, "nes")[0],
             SS("ss_name", "contra", ["Contra"], {"nes"}, "nes")[0]),
          SS("ss_id", "contra", ["Contra"], {"nes"}, "nes")[0]
          > SS("ss_name", "contra", ["Contra"], {"nes"}, "nes")[0])
    si, sr = SS("ss_name", "journey", ["The Sims 4: Journey to Batuu"], {"pc"}, "pc")
    check("the interior-title collapse applies to SS too: %d" % si, si < THRESHOLD)
    check("and names it: %r" % sr, "interior" in sr)
    check("a platform SS does not list costs 22",
          SS("ss_id", "contra", ["Contra"], {"nes"}, "amiga")[0]
          == SS("ss_id", "contra", ["Contra"], {"nes"}, "nes")[0] - 22)
    check("and the reason credits ScreenScraper, not IGDB",
          "ScreenScraper" in SS("ss_id", "contra", ["Contra"], {"nes"}, "amiga")[1])
    check("no names supplied -> no anchor penalty (SS records vary)",
          SS("ss_id", "contra", [], {"nes"}, "nes")[0]
          == SS("ss_id", "contra", ["Contra"], {"nes"}, "nes")[0])
    check("an unknown SS match source still scores something usable: %d"
          % SS("weird", "contra", ["Contra"], {"nes"}, "nes")[0],
          SS("weird", "contra", ["Contra"], {"nes"}, "nes")[0] == 70)

    print()
    print("9. every score is clamped to 0..100")
    for args in (("name", "journey", SIMS4, "atari 2600"),
                 ("ai_name", "ball", {"name": "Dragon Ball Z: Super Butouden 3",
                                      "platforms": [{"name": "Super Nintendo "
                                                             "Entertainment System"}]},
                  "atari 2600"),
                 ("manual", "x", None, None)):
        s = matchconf.match_confidence(*args)[0]
        check("%-10s -> %d is in range" % (args[0], s), 0 <= s <= 100)

    print()
    print("10. the title-anchor classifier the scorer is built on")
    # Four classes, best-across-all-names wins. These are the same classes that gate the
    # legacy fuzzy-match scrub, so getting them wrong either keeps every bad legacy
    # match or throws away every legitimate subtitle variant.
    C = igdb_enrich._name_anchor_class
    check("exact: a name normalizes to the key", C("journey", ["Journey"]) == "exact")
    for nk, names, why in (
            ("1943", ["1943: The Battle of Midway"], "prefix subtitle"),
            ("abadox", ["Abadox: The Deadly Inner War"], "prefix subtitle"),
            ("007 agent under fire", ["James Bond 007: Agent Under Fire"],
             "franchise prefix, key anchored at the END")):
        check("anchored (%s): %r" % (why, nk), C(nk, names) == "anchored")
    for nk, names in (("journey", ["The Sims 4: Journey to Batuu"]),
                      ("ball", ["Dragon Ball Z: Super Butouden 3"]),
                      ("chess", ["Fritz & Chesster: Learn to Play Chess Vol. 1"])):
        check("interior (a common word buried mid-title): %r" % nk,
              C(nk, names) == "interior")
    for nk, names, why in (("75 bingo", ["Bingo 75"], "reordered tokens"),
                           ("17 plus 4", ["Blackjack"], "semantic, no overlap")):
        check("norun (%s): %r" % (why, nk), C(nk, names) == "norun")
    check("the BEST class across alternates wins — an alt name that anchors saves it",
          C("rondo of blood", ["Akumajou Dracula X: Chi no Rondo",
                               "Castlevania: Rondo of Blood"]) == "anchored")
    # And the classifier's verdict is what actually moves the score.
    check("interior costs more than anchored, in the score itself",
          score("name", "journey", SIMS4, "pc") < score("name", "1943", TR_1943, "arcade"))

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
