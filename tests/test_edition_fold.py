#!/usr/bin/env python3
"""The edition fold rule, against IGDB's real linkage shape.

IGDB splits the edition relationship over TWO columns and uses them inconsistently:
remasters and expanded games link by `parent_game`, editions link by `version_parent`,
and 6,877 plain type-0 games carry a `version_parent` too. A rule that reads only one
column, or that filters on game_type first, misses most editions. These are the rows
measured on the live mirror on 2026-08-25.
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
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "ludodex"))
    import cardkey

    # (game_type, version_parent, parent_game) — verified rows from igdb-catalog.sqlite
    graph = {
        2155:  (0, None, None),      # Dark Souls
        81085: (9, None, 2155),      # Dark Souls: Remastered        -> parent_game
        21040: (3, 2155, None),      # Dark Souls: Prepare to Die    -> version_parent
        2368:  (0, None, None),      # Dark Souls II
        8222:  (10, None, 2368),     # Scholar of the First Sin      -> parent_game
        11133: (0, None, None),      # Dark Souls III
        912:   (0, None, None),      # Tomb Raider (1996)
        1164:  (0, None, None),      # Tomb Raider (2013)
        43690: (0, 912, None),       # Tomb Raider: Collector's Edition (2012)
        74555: (0, 1164, None),      # Tomb Raider: Collector's Edition (2013)
        148227: (8, None, 2261),     # Gothic 1 Remake               -> REMAKE, never folds
        2261:  (0, None, None),      # Gothic
        8915:  (13, None, 4242),     # a pack, carries parent_game    -> never folds
        4242:  (0, None, None),
        70001: (1, None, 2155),      # a DLC                          -> never folds
        70002: (2, None, 2368),      # an expansion                   -> never folds
        90001: (11, None, 90002),    # a cycle
        90002: (11, None, 90001),
    }

    check("remaster folds by parent_game", cardkey.fold_root(81085, graph) == 2155)
    check("edition folds by version_parent", cardkey.fold_root(21040, graph) == 2155)
    check("expanded_game folds by parent_game", cardkey.fold_root(8222, graph) == 2368)
    check("a base game is its own root", cardkey.fold_root(2155, graph) == 2155)
    check("Dark Souls II stays apart from Dark Souls",
          cardkey.fold_root(8222, graph) != cardkey.fold_root(81085, graph))
    check("Dark Souls III stays its own root", cardkey.fold_root(11133, graph) == 11133)

    # type 0 with a version_parent is the COMMON edition shape, not an exception
    check("2012 Collector's Edition folds onto the 1996 game",
          cardkey.fold_root(43690, graph) == 912)
    check("2013 Collector's Edition folds onto the 2013 game",
          cardkey.fold_root(74555, graph) == 1164)
    check("the two Tomb Raiders stay apart",
          cardkey.fold_root(43690, graph) != cardkey.fold_root(74555, graph))

    check("a remake never folds", cardkey.fold_root(148227, graph) == 148227)
    check("a pack never folds", cardkey.fold_root(8915, graph) == 8915)
    check("a dlc never folds", cardkey.fold_root(70001, graph) == 70001)
    check("an expansion never folds", cardkey.fold_root(70002, graph) == 70002)
    check("an unknown id is its own root", cardkey.fold_root(999999, graph) == 999999)
    check("a cycle terminates", cardkey.fold_root(90001, graph) in (90001, 90002))

    check("card_key_for folds an igdb key",
          cardkey.card_key_for("igdb:81085", graph) == "igdb:2155")
    check("card_key_for leaves a title key alone",
          cardkey.card_key_for("title:some rom", graph) == "title:some rom")
    check("card_key_for survives a null game_key",
          cardkey.card_key_for(None, graph) is None)
    check("card_key_for survives a malformed key",
          cardkey.card_key_for("igdb:not-a-number", graph) == "igdb:not-a-number")

    check("strip_edition removes a known suffix",
          cardkey.strip_edition("Dark Souls: Remastered") == "Dark Souls")
    check("strip_edition removes Prepare To Die Edition",
          cardkey.strip_edition("DARK SOULS: Prepare To Die Edition") == "DARK SOULS")
    check("strip_edition removes GOTY",
          cardkey.strip_edition("Fallout 3: Game of the Year Edition") == "Fallout 3")
    check("strip_edition leaves an ordinary title alone",
          cardkey.strip_edition("Mega Man 2") == "Mega Man 2")
    check("strip_edition does not eat a whole title",
          cardkey.strip_edition("Remastered") == "Remastered")

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
