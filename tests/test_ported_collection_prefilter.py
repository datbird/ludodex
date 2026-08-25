#!/usr/bin/env python3
"""The pre-filter that decides which titles are worth an AI call about being a bundle.

`_looks_like_collection` is the first gate of the compilation-detection pass. Everything
downstream — the batched model call, the confidence gate, the recorded collection, the
MATERIALIZED member entries that a bundle turns into — starts with a title this function
said yes to. It is a pure string rule, and both of its failure directions are expensive:

  * A FALSE POSITIVE spends a model call on a single game, and if the model plays along
    it can mint member entries for games nobody owns. "Tomb Raider: Anniversary" is one
    game; so is "Castlevania Anniversary Collection" minus the last word. The word list
    has to fire on the compilation and not on the edition — which is why every entry in
    it is matched on WORD BOUNDARIES, not as a substring. Without `\\b` anchoring,
    "classics" would fire on nothing useful but "bundle"/"trilogy" style words would
    start matching inside longer words, and a numeric rule without them would read the
    "1" of a version number as "in 1".
  * A FALSE NEGATIVE means a real multi-game product is never nominated, so it stays one
    catalog entry that claims to be a game and the games inside it are invisible.

The numeric family is its own rule because compilation carts name themselves by count
rather than by word: "6 in 1", "150-in-1", "76 in 1". Those are the ROM-set titles the
word list can never cover.

Offline. A pure function, no database and no network — nothing here can reach a model.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-ported-collpre-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


LOOKS = app._looks_like_collection


def main():
    print("which titles are worth asking a model about")
    test_support.assert_isolated()

    print()
    print("1. real compilations are nominated")
    for t in ("Sonic Mega Collection", "Sega Genesis Classics",
              "Mega Man Legacy Collection", "Castlevania Anniversary Collection",
              "Super Mario All-Stars", "Metroid Prime Trilogy",
              "Final Fantasy Anthology", "Namco Museum Arcade Classics",
              "The Orange Box Bundle", "Devil May Cry HD Collection",
              "Double Pack: Sonic Advance", "Halo: The Master Chief Collection"):
        check("hits:  %s" % t, LOOKS(t))

    print()
    print("2. ONE game is never mistaken for a bundle")
    # The expensive direction. Editions, remasters and sequels are the near misses:
    # 'Anniversary' is a compilation word only when 'Collection' follows it.
    for t in ("Tomb Raider: Anniversary", "Dark Souls Remastered", "Sonic CD", "Doom",
              "The Witcher 3: Game of the Year Edition", "Resident Evil 4 HD",
              "F1 2019", "Halo 3", "Portal 2", "Final Fantasy VII",
              "Prince of Persia: The Sands of Time", "Star Wars Jedi: Fallen Order"):
        check("skips: %s" % t, not LOOKS(t))

    print()
    print("3. the compilation words match on WORD BOUNDARIES, not as substrings")
    # A substring rule would nominate any title containing the letters, and the
    # false-positive cost is a model call plus, potentially, phantom member entries.
    for t in ("Recollection", "Collectible Card Game", "Bundlebee",
              "Classicsville", "Trilogybound"):
        check("a longer word containing one is NOT a bundle: %s" % t, not LOOKS(t))
    check("but the word standing alone still is", LOOKS("Some Recollection Collection"))

    print()
    print("4. the numeric family — compilation carts name themselves by count")
    for t in ("6 in 1", "150-in-1", "76 in 1", "4 IN 1", "Super 12 in 1",
              "2-in-1 Game Pack", "31 in 1 Mega Cart"):
        check("hits:  %s" % t, LOOKS(t))
    for t in ("Final Fantasy 1", "Formula 1", "Colin McRae Rally 2.0",
              "The Sims 4", "Portal 1"):
        check("skips: %s" % t, not LOOKS(t))

    print()
    print("5. it is case-insensitive and survives ordinary punctuation")
    for t in ("SONIC MEGA COLLECTION", "sonic mega collection",
              "Sonic Mega Collection (USA)", "Sonic Mega Collection.iso",
              "Sonic Mega Collection [!]"):
        check("hits:  %s" % t, LOOKS(t))
    check("'All-Stars' and 'All Stars' are both spellings people use",
          LOOKS("Super Mario All-Stars") and LOOKS("Super Mario All Stars"))

    print()
    print("6. nothing in, nothing out")
    check("an empty title is not a bundle", not LOOKS(""))
    check("None is not a bundle, and does not raise", not LOOKS(None))
    check("whitespace is not a bundle", not LOOKS("   "))

    print()
    print("7. every curated word actually fires, so the list has no dead entries")
    # A word in the list that no longer matches is a compilation family silently no
    # longer nominated — invisible unless something asserts it.
    for w in app._COLL_WORDS:
        check("the curated word %-22r nominates a title using it" % w,
              LOOKS("Some Game %s" % w))
    check("and the list is non-trivial", len(app._COLL_WORDS) >= 10)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
