#!/usr/bin/env python3
"""ROM grouping must not move the game count in either direction.

572,951 files group to about 33,760 games. Every rule in `romtags` either holds that
number or breaks it, and six of them broke it:

  * A MULTI-DISC FOLDER BECAME ONE GAME PER DISC. `psx/Final Fantasy VIII/` holding
    `FF8.m3u`, `Disc 1.chd` and `Disc 2.chd` produced three games — `FF8`, `Disc 1` and
    `Disc 2` — because every direct ROM child of a folder is read as a collection member.
    A disc is not a game, and a folder holding disc parts is a game folder, not a
    collection: nobody files "Disc 1.chd" loose beside other people's games.
  * A GAME FOLDER NESTED INSIDE A COLLECTION VANISHED. Its files resolved to the
    COLLECTION's name, which the collection rule then blanked as "a non-member file",
    so the game was not counted at all. The game is the folder one level down, not the
    wrapper it happens to sit in.
  * `parse_name` STRIPPED AN EXTENSION THAT WAS NOT ONE. `rsplit(".", 1)` on
    "Dr. Mario 64" yields "Dr", and `compute_game_keys` runs it over FOLDER names, where
    there is no extension to strip at all. "Dr. Mario 64" and "Dr. Robotnik's Mean Bean
    Machine" collapsed into one game called "Dr". Same for "Mr. Driller",
    "S.T.A.L.K.E.R." and "Ep. 1".
  * THE GoodTools COMBO REGEX ATE ORDINARY WORDS. `^[UEJWFGISKCABRDH]{2,6}$` matches
    "(SEGA)" — decoded as "Spain, Europe, Germany, Australia" — and "(CD)" as "China,
    Netherlands". "(HACK)" decoded as four regions AND the `continue` that followed
    swallowed the Hack flag entirely. The comment says the uppercase-only rule avoids
    "Sega"; it does not avoid "SEGA".
  * FLAG DECODING WAS CASE-SENSITIVE while the module comment says decoding is
    case-insensitive. `(Beta)` produced a flag and `(beta)` produced nothing; there were
    `_REGIONS_CI`, `_LANGS_CI` and `_GOOD_CI` and no `_FLAGS_CI`.
  * "Japan, USA" sat in REGIONS unreachably: `toks()` splits on commas before the lookup.

And in `homebrew.py`, the translation regex `\\(t[+\\-]?[a-z]{2,4}\\)` — the FIRST rule, so
whatever it claims masks every rule below it — matched "(Taito)", "(Tape)", "(Trial)" and
"(Test)". A GoodTools translation tag always carries its +/- sign; making the sign
optional is what turned a publisher's name into a fan translation and hid a real (Hack).

Offline. Pure functions only, no database and no network.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

test_support.isolate("ludodex-ident-romgroup-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import homebrew                                                # noqa: E402
import romtags                                                 # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def keys(rows):
    """compute_game_keys over (system, relpath) pairs -> {relpath: game}."""
    numbered = [(i, sysname, rel, rel.rsplit(".", 1)[-1].lower() if "." in rel else "")
                for i, (sysname, rel) in enumerate(rows)]
    out = romtags.compute_game_keys(numbered)
    return {rel: out[i] for i, _s, rel, _e in numbered}


def games(mapping):
    return {g for g in mapping.values() if g}


def main():
    print("rom grouping holds the game count")

    print()
    print("1. a multi-disc folder is ONE game, not one game per disc")
    m = keys([
        ("psx", "psx/Final Fantasy VIII/FF8.m3u"),
        ("psx", "psx/Final Fantasy VIII/Disc 1.chd"),
        ("psx", "psx/Final Fantasy VIII/Disc 2.chd"),
        ("psx", "psx/Final Fantasy VIII/Disc 3.chd"),
    ])
    check("four files, one game: %r" % sorted(games(m)), len(games(m)) == 1)
    check("and no disc is a game of its own",
          not any(str(g).lower().startswith("disc") for g in games(m)))
    check("every file belongs to that game (none dropped)",
          all(m.values()))

    print()
    print("2. a REAL collection still counts one game per member")
    m = keys([
        ("gba", "gba/All Releases/Advance Wars.gba"),
        ("gba", "gba/All Releases/Golden Sun.gba"),
        ("gba", "gba/All Releases/Metroid Fusion.gba"),
    ])
    check("three members, three games: %r" % sorted(games(m)), len(games(m)) == 3)

    print()
    print("3. a game folder NESTED in a collection is still a game")
    m = keys([
        ("psp", "psp/All Releases/Daxter.iso"),
        ("psp", "psp/All Releases/Patapon/PSP_GAME/USRDIR/eboot.bin"),
        ("psp", "psp/All Releases/Patapon/PSP_GAME/PARAM.SFO"),
    ])
    check("the loose member is a game", "Daxter" in games(m))
    check("and the nested folder game did not vanish: %r" % sorted(games(m)),
          "Patapon" in games(m))
    check("two games in total", len(games(m)) == 2)

    print()
    print("4. a non-member file loose in a collection is still not a game")
    m = keys([
        ("gba", "gba/All Releases/Advance Wars.gba"),
        ("gba", "gba/All Releases/gamelist.xml"),
    ])
    check("only the member counts: %r" % sorted(games(m)), games(m) == {"Advance Wars"})

    print()
    print("5. a dot in a TITLE is not an extension")
    for folder in ("Dr. Mario 64", "Mr. Driller", "S.T.A.L.K.E.R.", "Ep. 1"):
        got = romtags.parse_name(folder)[0]
        check("%-18s keeps its name: %r" % (folder, got), got == folder)
    check("a real extension is still stripped",
          romtags.parse_name("Sonic the Hedgehog 2 (USA).zip")[0]
          == "Sonic the Hedgehog 2")
    # The count consequence: two folder games whose names share a first word must not
    # collapse into one game called "Dr".
    m = keys([
        ("n64", "n64/Dr. Mario 64/data/rom.z64"),
        ("n64", "n64/Dr. Robotnik/data/rom.z64"),
    ])
    check("two Dr. games stay two: %r" % sorted(games(m)), len(games(m)) == 2)

    print()
    print("6. an uppercase word is not a GoodTools region combo")
    for tag, why in (("SEGA", "S,E,G,A = Spain, Europe, Germany, Australia"),
                     ("CD", "C,D = China, Netherlands")):
        region = romtags.parse_name("Some Game (%s).bin" % tag)[1]
        check("(%s) is not a region (%s): %r" % (tag, why, region), not region)
    check("a real combo still decodes",
          romtags.parse_name("Some Game (UE).bin")[1] == "USA, Europe")
    check("and a single code still decodes",
          romtags.parse_name("Some Game (J).bin")[1] == "Japan")

    print()
    print("7. (HACK) is a FLAG, and a flag is never swallowed by the region rule")
    flags = romtags.parse_name("Some Game (HACK).bin")[6]
    check("Hack was recorded: %r" % flags, "hack" in flags.lower())
    check("and it was not read as a region",
          not romtags.parse_name("Some Game (HACK).bin")[1])

    print()
    print("8. flag decoding is case-insensitive, like every other decode here")
    for tag in ("Beta", "beta", "BETA"):
        got = romtags.parse_name("Some Game (%s).bin" % tag)[6]
        check("(%s) yields the Beta flag: %r" % (tag, got), "beta" in got.lower())
    check("(demo) too",
          "demo" in romtags.parse_name("Some Game (demo).bin")[6].lower())
    check("there IS a case-insensitive flag table", hasattr(romtags, "_FLAGS_CI"))

    print()
    print("9. no unreachable entry pretends to do work")
    # toks() splits on commas before the set lookup, so a comma-joined member can never
    # be tested against REGIONS. The two halves already resolve on their own.
    check("'Japan, USA' is gone from REGIONS", "Japan, USA" not in romtags.REGIONS)
    check("and the comma form still decodes through toks()",
          romtags.parse_name("Some Game (Japan, USA).bin")[1] == "Japan, USA")

    print()
    print("10. a publisher's name is not a fan translation")
    for tag in ("Taito", "Tape", "Trial", "Test"):
        got = homebrew.classify("Some Game (%s).bin" % tag)
        check("(%s) is not a Translation: %r" % (tag, got), got != "Translation")
    check("a real GoodTools translation tag still classifies",
          homebrew.classify("Some Game [T+Eng].bin") == "Translation")
    check("and the parenthesised form with its sign",
          homebrew.classify("Some Game (T-Eng).bin") == "Translation")
    # Translation is the FIRST rule, so anything it wrongly claims hides every rule below.
    check("a Hack tagged by a publisher name is still a Hack",
          homebrew.classify("Some Game (Taito) (Hack).bin") == "Hack")
    check("and a Demo is still a Demo",
          homebrew.classify("Some Game (Tape) (Demo).bin") == "Demo")

    print()
    print("11. build_romdb parses a DIRECTORY component as a directory")
    src = open(os.path.join(DIR, "ludodex", "build_romdb.py")).read()
    check("it does not hand a folder name to the filename parser unchanged",
          "parse_name(c)" not in src)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
