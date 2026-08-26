#!/usr/bin/env python3
"""A card's KEY comes from the fold root. Its TITLE does not.

The fold root is frequently the regional original: Mega Man 2 folds onto "Rockman 2:
Dr. Wily no Nazo", Streets of Rage 3 onto "Bare Knuckle III". Taking the display title
from the root renamed 53 cards in the live library, several into Japanese. So the title
comes from the owned copies, and an edition suffix is stripped only when the stripped
form is the root's own name.
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

    graph = {
        2155:  (0, None, None),
        81085: (9, None, 2155),
        21040: (3, 2155, None),
        1715:  (10, None, 170742),      # Mega Man 2 -> Rockman 2
        170742: (0, None, None),
    }
    names = {2155: "Dark Souls", 81085: "Dark Souls: Remastered",
             21040: "Dark Souls: Prepare to Die Edition",
             1715: "Mega Man 2", 170742: "Rockman 2: Dr. Wily no Nazo"}

    # --- the UNMATCHED edition, which has no id to walk from ---
    # This is the live Prepare To Die row: matchgate refused it, so its game_key is a
    # title bucket. The card still has to be Dark Souls, and finding that must not bind
    # an identity.
    tindex = {"dark souls": 2155, "mega man 2": 1715}
    check("an unmatched edition folds by its stripped title",
          cardkey.card_key_for_title(
              "title:dark souls prepare to die",
              "DARK SOULS: Prepare To Die Edition", tindex, graph) == "igdb:2155")
    check("an unmatched title with no hit stays a title card",
          cardkey.card_key_for_title(
              "title:some rom", "Some ROM", tindex, graph) == "title:some rom")
    check("an unmatched title that needs no strip still resolves",
          cardkey.card_key_for_title(
              "title:mega man 2", "Mega Man 2", tindex, graph) == "igdb:170742")
    check("an already-identified key is not re-derived from its title",
          cardkey.card_key_for_title(
              "igdb:81085", "Dark Souls: Remastered", tindex, graph) == "igdb:2155")

    # --- assign ---
    entries = [("dark souls@pc", "igdb:81085", "DARK SOULS: REMASTERED"),
               ("dark souls@switch", "igdb:81085", "DARK SOULS: REMASTERED"),
               ("dark souls prepare to die@pc", "title:dark souls prepare to die",
                "DARK SOULS: Prepare To Die Edition"),
               ("mega man 2@nes", "igdb:1715", "Mega Man 2"),
               ("some rom@snes", "title:some rom", "Some ROM")]
    got = cardkey.assign(entries, graph, title_index=tindex)
    check("both Dark Souls platforms land on one card",
          got["dark souls@pc"] == got["dark souls@switch"] == "igdb:2155")
    check("the unmatched edition lands on the same card",
          got["dark souls prepare to die@pc"] == "igdb:2155")
    check("Mega Man 2 folds onto the Rockman root", got["mega man 2@nes"] == "igdb:170742")
    check("an unidentified entry keeps its title key",
          got["some rom@snes"] == "title:some rom")
    check("assign works with no title index at all",
          cardkey.assign(entries, graph)["dark souls@pc"] == "igdb:2155")

    # --- unfold override ---
    got2 = cardkey.assign(entries, graph, unfolded={"dark souls prepare to die@pc"},
                          title_index=tindex)
    check("an unfolded entry keeps its own card",
          got2["dark souls prepare to die@pc"] == "title:dark souls prepare to die")
    check("unfolding one entry does not disturb the others",
          got2["dark souls@pc"] == "igdb:2155")

    # --- card_title ---
    check("the suffix is stripped when it lands on the root",
          cardkey.card_title("igdb:2155", ["DARK SOULS: REMASTERED"], names) == "DARK SOULS")
    check("a regional root never renames the card",
          cardkey.card_title("igdb:170742", ["Mega Man 2"], names) == "Mega Man 2")
    check("the first copy wins when nothing strips",
          cardkey.card_title("igdb:2155", ["Mega Man 2", "Other"], names) == "Mega Man 2")
    check("a title card uses its copy",
          cardkey.card_title("title:some rom", ["Some ROM"], names) == "Some ROM")
    check("no copies falls back to the root name",
          cardkey.card_title("igdb:2155", [], names) == "Dark Souls")
    check("no copies and no root name yields an empty title",
          cardkey.card_title("igdb:999999", [], names) == "")

    # --- a copy that IS the game outranks a copy that is an expansion of it ---
    # Live defect 2026-08-26: the Dark Souls II card wore "Scholar of the First Sin",
    # because that copy sorted first and "Scholar of the First Sin" is not a strippable
    # edition marker. The base game was sitting right there on the same card.
    names2 = {2368: "Dark Souls II"}
    check("a copy matching the root wins over one that does not",
          cardkey.card_title("igdb:2368",
                             ["DARK SOULS II: Scholar of the First Sin", "Dark Souls II"],
                             names2) == "Dark Souls II")
    check("order does not matter for that",
          cardkey.card_title("igdb:2368",
                             ["Dark Souls II", "DARK SOULS II: Scholar of the First Sin"],
                             names2) == "Dark Souls II")
    check("a trademark symbol does not defeat the match",
          cardkey.card_title("igdb:2368",
                             ["DARK SOULS II: Scholar of the First Sin", "DARK SOULS\u2122 II"],
                             names2) == "DARK SOULS\u2122 II")
    check("a strippable copy still wins when no copy matches outright",
          cardkey.card_title("igdb:2155",
                             ["DARK SOULS: Prepare To Die Edition", "DARK SOULS: REMASTERED"],
                             names) == "DARK SOULS")
    check("and the first copy still wins when nothing matches or strips",
          cardkey.card_title("igdb:170742", ["Mega Man 2", "Mega Man II"], names)
          == "Mega Man 2")

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
