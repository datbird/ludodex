#!/usr/bin/env python3
"""What folds onto one card, and what does not.

THE RULE, settled 2026-08-26 after looking at the result of the first one: **the only
axis that folds is PLATFORM.** A product is a card.

  * "Dark Souls: Prepare To Die Edition" is a product. Own it on Xbox, PlayStation and
    Steam and it is ONE card listing three platforms.
  * "Dark Souls: Remastered" is a DIFFERENT product, so it is its own card, listing the
    platforms you own IT on.
  * So is "Dark Souls II", and so is "Scholar of the First Sin".

The first version folded editions, remasters and expanded games onto the original. That
was wrong, and wrong in a way only looking could show: it hid Remastered inside Dark
Souls. What replaced it is narrow on purpose.

IGDB's `port` type is the one thing that still folds, because a port IS the same product
with its own record. That is what collapses "DOOM 2" and "DOOM II", or "Into The Breach"
and "Into the Breach", which are one game listed twice.

Everything else the graph offers is a different product and stays visible. The links are
not thrown away: they become the "Other versions" and "Series" sections instead.
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
        81085: (9, None, 2155),      # Dark Souls: Remastered        REMASTER
        21040: (3, 2155, None),      # Dark Souls: Prepare to Die    EDITION
        2368:  (0, None, None),      # Dark Souls II
        8222:  (10, None, 2368),     # Scholar of the First Sin      EXPANDED
        11133: (0, None, None),      # Dark Souls III
        912:   (0, None, None),      # Tomb Raider (1996)
        43690: (0, 912, None),       # Tomb Raider: Collector's Edition
        148227: (8, None, 2261),     # Gothic 1 Remake               REMAKE
        2261:  (0, None, None),      # Gothic
        1968:  (11, None, 193359),   # Rayman Legends                PORT
        193359: (0, None, None),     # Rayman Legends
        1654:  (11, None, 151541),   # Streets of Rage 3             PORT of the JP release
        151541: (0, None, None),     # Bare Knuckle III
        8915:  (13, None, 4242),     # a pack
        4242:  (0, None, None),
        70001: (1, None, 2155),      # a DLC
        70002: (2, None, 2368),      # an expansion
        90001: (11, None, 90002),    # a cycle
        90002: (11, None, 90001),
    }

    # --- the ONE thing that folds -------------------------------------------------
    check("a port folds onto the product it is a port of",
          cardkey.fold_root(1968, graph) == 193359)
    check("including a regional port", cardkey.fold_root(1654, graph) == 151541)
    check("a cycle in the port graph terminates",
          cardkey.fold_root(90001, graph) in (90001, 90002))

    # --- everything else is its own product ---------------------------------------
    check("a REMASTER is its own card", cardkey.fold_root(81085, graph) == 81085)
    check("an EDITION is its own card", cardkey.fold_root(21040, graph) == 21040)
    check("an EXPANDED game is its own card", cardkey.fold_root(8222, graph) == 8222)
    check("a REMAKE is its own card", cardkey.fold_root(148227, graph) == 148227)
    check("a collector's edition is its own card",
          cardkey.fold_root(43690, graph) == 43690)
    check("a pack is its own card", cardkey.fold_root(8915, graph) == 8915)
    check("a dlc never folds", cardkey.fold_root(70001, graph) == 70001)
    check("an expansion never folds", cardkey.fold_root(70002, graph) == 70002)
    check("a base game is its own root", cardkey.fold_root(2155, graph) == 2155)

    # the shelf this produces: five Dark Souls products, five cards
    shelf = {cardkey.fold_root(i, graph) for i in (2155, 81085, 21040, 2368, 8222, 11133)}
    check("the Dark Souls shelf keeps every product visible", len(shelf) == 6, )

    # --- key plumbing --------------------------------------------------------------
    check("card_key_for folds a port key",
          cardkey.card_key_for("igdb:1968", graph) == "igdb:193359")
    check("card_key_for leaves a remaster alone",
          cardkey.card_key_for("igdb:81085", graph) == "igdb:81085")
    check("card_key_for leaves a title key alone",
          cardkey.card_key_for("title:some rom", graph) == "title:some rom")
    check("card_key_for survives a null game_key",
          cardkey.card_key_for(None, graph) is None)
    check("card_key_for survives a malformed key",
          cardkey.card_key_for("igdb:not-a-number", graph) == "igdb:not-a-number")
    check("an unknown id is its own root", cardkey.fold_root(999999, graph) == 999999)

    # --- an UNMATCHED entry never folds by its title ------------------------------
    # The first rule stripped "Edition" off a title and looked the remainder up, which is
    # how "Prepare To Die Edition" ended up inside Dark Souls. Editions are products now,
    # so that path is gone: no provider id, no fold.
    check("an unmatched entry keeps its own card",
          cardkey.card_key_for_entry("dark souls prepare to die@pc",
                                     "title:dark souls prepare to die", graph)
          == "title:dark souls prepare to die")

    # --- the pin still wins --------------------------------------------------------
    check("a pinned entry keeps its own card even when it would fold",
          cardkey.card_key_for_entry("rayman legends@pc", "igdb:1968", graph,
                                     {"rayman legends@pc"}) == "igdb:1968")

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
