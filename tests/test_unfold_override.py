#!/usr/bin/env python3
"""The manual reverse for a fold the user disagrees with.

Only ports fold, and a port is usually right: "DOOM 2" and "DOOM II" are one game listed
twice. But IGDB also types a VR edition as a PORT of the flat game, and a VR game is a
different thing to own. Measured in the live library: Fallout 4 VR (27922) is a port of
Fallout 4 (9630), and Arcade Paradise VR of Arcade Paradise.

A rule that reads a provider will always have a tail the user disagrees with, so the user
gets a reverse: a per-entry pin that a rebuild never overwrites.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import cardkey
    import reset
    import unfold

    # a pin is a DECISION, so a library reset must keep it
    dbname = os.path.basename(unfold.DB)
    check("the pins are a curation store", dbname in reset.CURATION_DBS)
    check("and never an import cache", dbname not in reset.IMPORT_DBS)

    con = sqlite3.connect(":memory:")
    unfold.ensure(con)
    check("an empty store unfolds nothing", unfold.load(con) == set())

    unfold.set_unfold(con, "fallout 4 vr@pc")
    check("a pin is stored", unfold.load(con) == {"fallout 4 vr@pc"})
    unfold.set_unfold(con, "fallout 4 vr@pc")
    check("pinning twice is idempotent", len(unfold.load(con)) == 1)

    # the real rows: IGDB types Fallout 4 VR as a PORT of Fallout 4
    graph = {9630: (0, None, None), 27922: (11, None, 9630)}
    entries = [("fallout 4@pc", "igdb:9630", "Fallout 4"),
               ("fallout 4 vr@pc", "igdb:27922", "Fallout 4 VR")]

    folded = cardkey.assign(entries, graph)
    check("without the pin the port folds in",
          folded["fallout 4@pc"] == folded["fallout 4 vr@pc"])

    pinned = cardkey.assign(entries, graph, unfolded=unfold.load(con))
    check("with the pin it does not",
          pinned["fallout 4@pc"] != pinned["fallout 4 vr@pc"])
    check("the pinned entry keeps its own identity",
          pinned["fallout 4 vr@pc"] == "igdb:27922")
    check("the other entry is unaffected", pinned["fallout 4@pc"] == "igdb:9630")

    unfold.clear_unfold(con, "fallout 4 vr@pc")
    check("clearing removes the pin", unfold.load(con) == set())
    refolded = cardkey.assign(entries, graph, unfolded=unfold.load(con))
    check("and it folds again",
          refolded["fallout 4@pc"] == refolded["fallout 4 vr@pc"])

    # build_library must actually consult the pins, or the reverse never reaches a rebuild
    bl = open(os.path.join(root, "ludodex", "build_library.py"), encoding="utf-8").read()
    check("build_library imports unfold", "import unfold" in bl)
    check("build_library loads the pins through the raising loader",
          "unfold.load_all()" in bl)
    check("build_library decides the card in ONE place",
          bl.count("cardkey.card_key_for_entry(") == 3)
    check("and no insert site inlines the pin test itself",
          "in _unfolded" not in bl.split("_unfolded = unfold.load_all()")[-1])

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
