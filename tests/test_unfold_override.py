#!/usr/bin/env python3
"""The manual reverse for a fold the user disagrees with.

IGDB's `expanded_game` (type 10) is the loosest link in the fold set, and it is the one
that carries Dark Souls II: Scholar of the First Sin, so it cannot be dropped. It also
pulls in arguable pairs measured in the live mirror: Bit Blaster XL with Super Bit
Blaster XL, Arcade Paradise with Arcade Paradise VR. The answer is a per-entry pin that
a rebuild never overwrites.
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
    import unfold

    con = sqlite3.connect(":memory:")
    unfold.ensure(con)
    check("an empty store unfolds nothing", unfold.load(con) == set())

    unfold.set_unfold(con, "super bit blaster xl@pc")
    check("a pin is stored", unfold.load(con) == {"super bit blaster xl@pc"})
    unfold.set_unfold(con, "super bit blaster xl@pc")
    check("pinning twice is idempotent", len(unfold.load(con)) == 1)

    graph = {33733: (0, None, None), 33734: (10, None, 33733)}
    entries = [("bit blaster xl@pc", "igdb:33733", "Bit Blaster XL"),
               ("super bit blaster xl@pc", "igdb:33734", "Super Bit Blaster XL")]

    folded = cardkey.assign(entries, graph)
    check("without the pin they share a card",
          folded["bit blaster xl@pc"] == folded["super bit blaster xl@pc"])

    pinned = cardkey.assign(entries, graph, unfolded=unfold.load(con))
    check("with the pin they do not",
          pinned["bit blaster xl@pc"] != pinned["super bit blaster xl@pc"])
    check("the pinned entry keeps its own identity",
          pinned["super bit blaster xl@pc"] == "igdb:33734")
    check("the other entry is unaffected", pinned["bit blaster xl@pc"] == "igdb:33733")

    unfold.clear_unfold(con, "super bit blaster xl@pc")
    check("clearing removes the pin", unfold.load(con) == set())
    refolded = cardkey.assign(entries, graph, unfolded=unfold.load(con))
    check("and they share a card again",
          refolded["bit blaster xl@pc"] == refolded["super bit blaster xl@pc"])

    # build_library must actually consult the pins, or the reverse never reaches a
    # rebuild. Read as source: build_library runs its whole build at module scope.
    bl = open(os.path.join(root, "ludodex", "build_library.py"), encoding="utf-8").read()
    check("build_library imports unfold", "import unfold" in bl)
    check("build_library loads the pins", "unfold.load(" in bl)
    check("build_library still honours _unfolded at the insert", "_ekey in _unfolded" in bl)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
