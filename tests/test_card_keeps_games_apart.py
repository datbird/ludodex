#!/usr/bin/env python3
"""The collapse must not undo the splits that took weeks to get right.

Three known-hard pairs, each a different mechanism:
  * Portal — a per-entry resolution override (entry_res) gives the 1986 Amiga text
    adventure its own igdb id, apart from Valve's 2007 game.
  * Uno — era separation gives the 1994 Game Boy game a title: key, apart from the
    identified Steam game.
  * Tomb Raider — the 1996 game and the 2013 reboot are different games, and each
    Collector's Edition is a different PRODUCT again, so all four stay apart.
"""
import os
import sys

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

    graph = {
        71: (0, None, None),          # Portal (Valve, 2007)
        14546: (0, None, None),       # Portal (1986)
        912: (0, None, None),         # Tomb Raider 1996
        1164: (0, None, None),        # Tomb Raider 2013
        43690: (0, 912, None),        # Collector's Edition 2012
        74555: (0, 1164, None),       # Collector's Edition 2013
    }

    got = cardkey.assign([
        ("portal@pc", "igdb:71", "Portal"),
        ("portal@amiga", "igdb:14546", "Portal"),
        ("uno@pc", "igdb:5555", "UNO"),
        ("uno@gameboy", "title:uno", "Uno"),
        ("tomb raider@psx", "igdb:912", "Tomb Raider"),
        ("tomb raider collectors@pc", "igdb:43690", "Tomb Raider: Collector's Edition"),
        ("tomb raider@ps3", "igdb:1164", "Tomb Raider"),
        ("tomb raider collectors 2013@pc", "igdb:74555",
         "Tomb Raider: Collector's Edition"),
    ], graph)

    check("the two Portals stay apart", got["portal@pc"] != got["portal@amiga"])
    check("Valve's Portal keeps its identity", got["portal@pc"] == "igdb:71")
    check("the 1986 Portal keeps its own", got["portal@amiga"] == "igdb:14546")

    check("the two Unos stay apart", got["uno@pc"] != got["uno@gameboy"])
    check("the era-separated Uno keeps its title key", got["uno@gameboy"] == "title:uno")

    # An edition is a product you bought, so it is its own card. It is not lost: it
    # appears under "Other versions" on the game it is an edition of.
    check("a Collector's Edition is its own card, not merged into the base game",
          got["tomb raider collectors@pc"] != got["tomb raider@psx"])
    check("and the 2013 one likewise",
          got["tomb raider collectors 2013@pc"] != got["tomb raider@ps3"])
    check("the two Tomb Raiders stay apart",
          got["tomb raider@psx"] != got["tomb raider@ps3"])
    check("all four are distinct cards",
          len({got["tomb raider@psx"], got["tomb raider@ps3"],
               got["tomb raider collectors@pc"],
               got["tomb raider collectors 2013@pc"]}) == 4)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
