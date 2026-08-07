#!/usr/bin/env python3
"""The two halves of the acceptance rule, tested together because they fix each other.

One rule was failing in both directions at once:

  * TOO MUCH IN — an alias became the ACCEPTANCE key, so a candidate was judged against
    the alias instead of the game we own. `Deathmatch Classic` holds "DmC : Devil May
    Cry" because 'DMC' is one of its aliases; `Beyond Citadel` holds "The Citadel"
    through 'Citadel'. Live, and reproducible by a fresh ingest.
  * TOO MUCH OUT — a series number is treated as distinguishing even when the subtitle
    already distinguishes, so "Police Quest: Open Season" was refused for "Police Quest
    IV: Open Season".

They are one file because they were designed against each other: a strict alias rule
refuses 'Crash Bandicoot: Warped' for 'Crash Bandicoot 3: Warped', and only the numbering
rule makes that unnecessary by accepting it at the gate directly.

Both were then MEASURED against the live library, and the measurement cut the alias rule
down to one signal. Blocking truncations fixed ~25 bad binds and broke ~40 good ones,
because `Beyond Citadel` <- "The Citadel" (different game) and `Fallout 76 Public Test
Server` <- "Fallout 76" (same game) are indistinguishable by shape. Only the initialism
case survives, which is why the checks below assert that a truncation is still allowed —
that is a decision, not a gap.

Every DISASTER case in this project's history is pinned here, because the cost of these
rules being wrong is a wrong merge across the whole library.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matchgate                                        # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def accepts(owned, cand, year=None, cand_year=None):
    return matchgate.score([owned], cand, year, cand_year)[0]


def main():
    print("numbering variants — accepted (the subtitle does the distinguishing)")
    for owned, cand in [
            ("Police Quest IV: Open Season", "Police Quest: Open Season"),
            ("Quest for Glory IV: Shadows of Darkness",
             "Quest for Glory: Shadows of Darkness"),
            ("Crash Bandicoot 3: Warped", "Crash Bandicoot: Warped"),
            ("Mega Man X4", "Megaman X4"),
    ]:
        check("%r accepts %r" % (owned[:34], cand[:34]), accepts(owned, cand))

    print("numbering variants — REFUSED (nothing else distinguishes them)")
    for owned, cand in [
            # the disaster case: no subtitle, so the number is all there is
            ("Ys I", "Ys II"),
            ("Ys I: Ancient Ys Vanished", "Ys II: Ancient Ys Vanished"),
            # numbered on both sides and disagreeing, subtitle or not
            ("Police Quest IV: Open Season", "Police Quest II: Open Season"),
            # NB the reverse shape — owned "Police Quest" vs a candidate carrying EXTRA
            # words ("Police Quest II: The Vengeance") — is accepted, and always was:
            # `nc` is scored and never gated, because providers append edition and
            # region words we do not carry (see the module docstring). Ranking, not the
            # gate, is what prefers the exact record. Untouched here on purpose.
            # a bare 'x' is a character at least as often as it is ten
            ("Mega Man X Legacy Collection", "Mega Man Legacy Collection"),
            # different subtitles are different games, number or no number
            ("Final Fantasy IV: The After Years", "Final Fantasy IV: Interlude"),
            # a sequel is not its parent
            ("Boltgun 2", "Boltgun"),
    ]:
        check("%r REFUSES %r" % (owned[:34], cand[:34]), not accepts(owned, cand))

    print("the era rule still governs a remake")
    check("Resident Evil 4 (2023) still refuses the 2005 record",
          not accepts("Resident Evil 4", "Resident Evil 4", 2023, 2005))

    print("safe_aliases — degradations may not widen acceptance")
    dropped = matchgate.safe_aliases(
        "Deathmatch Classic",
        ["Half-Life: Deathmatch Classic", "Death Match Classic", "DMC"])
    check("the abbreviation 'DMC' is dropped", "DMC" not in dropped)
    check("the fuller name is kept", "Half-Life: Deathmatch Classic" in dropped)
    check("a respelling is kept", "Death Match Classic" in dropped)
    check("a provider's own contraction is KEPT ('Wolf3d' for 'Wolfenstein 3D')",
          "Wolf3d" in matchgate.safe_aliases("Wolfenstein 3D", ["Wolf3d"]))
    # A truncation is NOT filtered, and that is a measured decision rather than an
    # oversight: `Beyond Citadel` <- "The Citadel" (different game) and `Fallout 76
    # Public Test Server` <- "Fallout 76" (same game) are the identical shape, so the
    # rule would break ~40 correct matches to fix ~25 wrong ones. Adjudication, not
    # arithmetic — see docs/TASKS.md.
    check("a truncation is deliberately NOT filtered",
          "Citadel" in matchgate.safe_aliases("Beyond Citadel", ["Citadel"]))

    print("safe_aliases — a real alternate name survives")
    check("'Probotector' kept for 'Contra'",
          "Probotector" in matchgate.safe_aliases("Contra", ["Probotector"]))
    check("a romanised regional name is kept",
          "Akumajou Dracula: Circle of the Moon" in matchgate.safe_aliases(
              "Castlevania: Circle of the Moon",
              ["Akumajou Dracula: Circle of the Moon"]))
    check("'Rockman X4' kept for 'Mega Man X4'",
          "Rockman X4" in matchgate.safe_aliases("Mega Man X4", ["Rockman X4"]))

    print("the live binds this was built for")
    check("'DMC' can no longer accept DmC for Deathmatch Classic",
          not matchgate.safe_aliases("Deathmatch Classic", ["DMC"]))
    check("and the owned title never accepted it anyway",
          not accepts("Deathmatch Classic",
                      "DmC : Devil May Cry - Definitive Edition"))
    check("the owned title alone still refuses The Citadel",
          not accepts("Beyond Citadel", "The Citadel"))

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
