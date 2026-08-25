#!/usr/bin/env python3
"""The platform decides WHICH game a title is, in two places, and neither was asserted.

BEFORE the match: `platmap.platform_from_title`. ROM sets bake the hardware into the
filename — "Diggers CD32", "Doom 32X" — and IGDB has no such game. The entry stays
unidentified, gets no cover, and the wand (with no resolution to source art from) reports
"already has art" for a game that has none. The fix is a CURATED token list, and curated
is the whole safety property: the value has to be a pure hardware designator that is
never a real title word. Bare "cd" is deliberately absent, because "Sonic CD" is a game,
not a Sega CD release of "Sonic". A token added carelessly here silently re-platforms
every title containing that word.

The same set is what `titlenorm` pops off the dedupe key, so "Doom 32X" on the 32X and
"Doom" unify — but only when the entry's platform actually IS that hardware.

AFTER the match: `igdb_enrich.per_entry_resolve`. One title, several owned platforms, and
IGDB holding several games with that exact name. Which release does THIS entry mean?
Platform membership answers it deterministically most of the time — PS1 "Tomb Raider" is
the 1996 game, PS3 "Tomb Raider" is the 2013 reboot, and nobody had to be asked. What
matters is the two NO-FIT cases being told apart:

  * hardware OLDER than every candidate's earliest generation is an impossible backport
    (an Atari 2600 "Star Fox") -> detach-worthy;
  * a generation-compatible no-fit is probably a port IGDB does not list (a Saturn
    release of a PS1-only record) -> never auto-separated. That is the OVER-SEPARATION
    GUARD, and `combine_verdict` enforces it again: with no AI verdict, or one below
    the confidence threshold, the answer is always "keep".

Offline. Pure functions, a stubbed adjudicator, no database and no network.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

test_support.isolate("ludodex-ported-platident-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import igdb_enrich                                             # noqa: E402
import platmap                                                 # noqa: E402
import titlenorm                                               # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


TR_1996 = {"id": 1164, "name": "Tomb Raider", "year": 1996,
           "platforms": [{"name": "PlayStation"}, {"name": "Sega Saturn"},
                         {"name": "PC (Microsoft Windows)"}]}
TR_2013 = {"id": 2013, "name": "Tomb Raider", "year": 2013,
           "platforms": [{"name": "PlayStation 3"}, {"name": "Xbox 360"},
                         {"name": "PC (Microsoft Windows)"}]}
TR_GBC = {"id": 5555, "name": "Tomb Raider", "year": 2000,
          "platforms": [{"name": "Game Boy Color"}]}
SF_SNES = {"id": 700, "name": "Star Fox", "year": 1993,
           "platforms": [{"name": "Super Nintendo Entertainment System"}]}
PS1_ONLY = {"id": 800, "name": "X", "year": 1996,
            "platforms": [{"name": "PlayStation"}]}
TRS = [TR_1996, TR_2013, TR_GBC]


def main():
    print("the platform decides which game this is — before the match and after it")

    print()
    print("1. an explicit hardware token in the title names the platform")
    check("'Diggers CD32' -> the Amiga CD32: %r"
          % platmap.platform_from_title("Diggers CD32"),
          platmap.platform_from_title("Diggers CD32") == "amigacd32")
    check("'Doom 32X' -> the Sega 32X (the case this rule was built from)",
          platmap.platform_from_title("Doom 32X") == "sega 32x")
    check("punctuation and extensions do not hide the token",
          platmap.platform_from_title("Diggers (CD32).adf") == "amigacd32")
    check("the token need not be trailing",
          platmap.platform_from_title("cd32 diggers") == "amigacd32")

    print()
    print("2. it is CURATED, and the curation is the safety property")
    # A coincidental word must never re-platform a game. Bare "cd" is the standing
    # example: "Sonic CD" is a title, and yanking it to the Sega CD loses the game.
    check("bare 'cd' is not a hardware token", "cd" not in platmap.TITLE_PLATFORM)
    for title in ("Sonic CD", "Sonic the Hedgehog", "Final Fantasy VII",
                  "Command & Conquer", "Tomb Raider"):
        check("%-22s does not re-platform: %r"
              % (title, platmap.platform_from_title(title)),
              platmap.platform_from_title(title) is None)
    check("an empty title is None, not a crash", platmap.platform_from_title("") is None)
    check("and so is None", platmap.platform_from_title(None) is None)
    # A label the ontology does not know would move an entry onto a platform nothing
    # else can reason about — the gate would then never separate on it.
    for tok, label in platmap.TITLE_PLATFORM.items():
        check("%-6s -> %-10s is a platform the ontology knows" % (tok, label),
              platmap.canon(label) in platmap.KNOWN)
        check("  and the token round-trips through platform_from_title",
              platmap.platform_from_title("Some Game %s" % tok) == label)

    print()
    print("3. the same token is popped off the dedupe key — but only on that hardware")
    check("'Diggers CD32' on the CD32 keys as 'diggers'",
          titlenorm.norm("Diggers CD32", "amigacd32") == "diggers")
    check("'Doom 32X' on the 32X keys as 'doom', so it unifies with plain Doom",
          titlenorm.norm("Doom 32X", "sega 32x") == "doom")
    check("a multi-word title keeps the rest of itself",
          titlenorm.norm("Chaos Engine CD32", "amigacd32") == "chaos engine")
    check("the SOLE token is never stripped (a bare 'CD32' is not a title)",
          titlenorm.norm("CD32", "amigacd32") == "cd32")
    check("and the token stays when the entry is on OTHER hardware",
          titlenorm.norm("Doom 32X", "snes") == "doom 32x")
    check("'Sonic CD' on the Sega CD keeps its name",
          titlenorm.norm("Sonic CD", "segacd") == "sonic cd")

    print()
    print("4. per-entry resolve: platform membership answers it, no AI spent")
    R = igdb_enrich.per_entry_resolve
    check("PS1 Tomb Raider -> the 1996 game", R(TRS, "psx", 1164)["igdb_id"] == 1164)
    check("PS3 Tomb Raider -> the 2013 reboot", R(TRS, "ps3", 1164)["igdb_id"] == 2013)
    check("GBC Tomb Raider -> the Game Boy game", R(TRS, "gameboy color", 1164)["igdb_id"] == 5555)
    for plat in ("psx", "ps3", "gameboy color"):
        check("  %-14s is reported as a deterministic unique" % plat,
              R(TRS, plat, 1164)["kind"] == "unique")

    print()
    print("5. two candidates list the same platform -> ambiguous, not a coin flip")
    amb = R(TRS, "pc", 1164)
    check("PC lists BOTH releases: kind=%r" % amb["kind"], amb["kind"] == "ambiguous")
    check("and it hands over both ids for Phase 2: %s" % sorted(amb["fit_ids"]),
          set(amb["fit_ids"]) == {1164, 2013})
    check("without picking one itself", amb["igdb_id"] is None)

    print()
    print("6. the two NO-FIT cases are told apart by hardware GENERATION")
    imp = R([SF_SNES], "atari 2600", 700)
    check("an Atari 2600 'Star Fox' is impossible (gen 2 << gen 4): %r" % imp["kind"],
          imp["kind"] == "none_impossible")
    check("and it names no game", imp["igdb_id"] is None)
    unc = R([PS1_ONLY], "sega saturn", 800)
    check("a Saturn entry against a PS1-only record is merely uncertain: %r" % unc["kind"],
          unc["kind"] == "none_uncertain")
    # The year buffer would not catch Star Fox (1993 is one year past the 2600's era
    # end); the generation gap does. That is why this rule is generation-based.
    check("the generation gap is what separates them",
          platmap.generation("atari 2600") < platmap.generation("snes")
          and platmap.generation("sega saturn") == platmap.generation("psx"))
    check("a listed platform still resolves normally", R([SF_SNES], "snes", 700)["igdb_id"] == 700)

    print()
    print("7. combine_verdict: keep is the default whenever we are not sure")
    C = igdb_enrich.combine_verdict
    check("a deterministic unique applies with NO AI at all",
          C(R(TRS, "ps3", 1164), None, 1164) == {"action": "set", "igdb_id": 2013})
    confident = {"same_as_group": False, "correct_igdb_id": 2013, "detach": False,
                 "confidence": 0.9}
    low = dict(confident, confidence=0.4)
    check("an ambiguous entry takes a confident AI pick",
          C(amb, confident, 1164)["igdb_id"] == 2013)
    check("a LOW-confidence pick is refused, and the group identity is kept",
          C(amb, low, 1164) == {"action": "keep", "igdb_id": 1164})
    check("an ambiguous entry with no AI keeps too",
          C(amb, None, 1164) == {"action": "keep", "igdb_id": 1164})
    detach = {"same_as_group": False, "correct_igdb_id": None, "detach": True,
              "confidence": 0.95}
    check("impossible + a confident AI confirmation detaches",
          C(imp, detach, 700) == {"action": "detach", "igdb_id": None})
    check("impossible WITHOUT an AI verdict does NOT detach — the guard",
          C(imp, None, 700) == {"action": "keep", "igdb_id": 700})
    same = {"same_as_group": True, "correct_igdb_id": None, "detach": False,
            "confidence": 0.9}
    check("'it is the same game' on an uncertain no-fit keeps the port",
          C(unc, same, 800) == {"action": "keep", "igdb_id": 800})
    check("a verdict that asserts nothing keeps as well",
          C(amb, {"confidence": 0.99}, 1164) == {"action": "keep", "igdb_id": 1164})

    print()
    print("8. plan_title orchestrates a whole title, and only PAYS for the hard ones")
    P = igdb_enrich.plan_title
    asked = []

    def adjudicate_pc(items):
        asked.append([it["platform"] for it in items])
        return [{"n": it["n"], "same_as_group": False, "correct_igdb_id": 1164,
                 "detach": False, "confidence": 0.9} for it in items]

    entries = [{"platform": "psx"}, {"platform": "ps3"}, {"platform": "pc"},
               {"platform": "gameboy color"}]
    plan = {p["platform"]: p for p in P("tomb raider", 1164, entries, TRS, adjudicate_pc)}
    check("PS1 -> 1996, deterministically",
          plan["psx"]["igdb_id"] == 1164 and plan["psx"]["kind"] == "unique")
    check("PS3 -> 2013, set without AI",
          plan["ps3"]["igdb_id"] == 2013 and plan["ps3"]["action"] == "set")
    check("GBC -> the Game Boy game", plan["gameboy color"]["igdb_id"] == 5555)
    check("PC was the ambiguous one and the AI resolved it to 1996",
          plan["pc"]["kind"] == "ambiguous" and plan["pc"]["igdb_id"] == 1164)
    check("the adjudicator was called ONCE, batched: %s" % asked, len(asked) == 1)
    check("and ONLY the non-unique entry was sent to it: %s" % asked[0],
          asked[0] == ["pc"])

    def adjudicate_detach(items):
        return [{"n": it["n"], "same_as_group": False, "correct_igdb_id": None,
                 "detach": True, "confidence": 0.95} for it in items]

    sf = {p["platform"]: p for p in
          P("star fox", 700, [{"platform": "snes"}, {"platform": "atari 2600"}],
            [SF_SNES], adjudicate_detach)}
    check("the SNES entry is kept and set", sf["snes"]["action"] == "set"
          and sf["snes"]["igdb_id"] == 700)
    check("the 2600 entry is detached", sf["atari 2600"]["action"] == "detach")

    # No adjudicator at all: every non-unique entry falls back to keep, nothing detaches.
    sf_noai = {p["platform"]: p for p in
               P("star fox", 700, [{"platform": "snes"}, {"platform": "atari 2600"}],
                 [SF_SNES], None)}
    check("with NO adjudicator nothing is separated — the guard holds end to end",
          [p["action"] for p in sf_noai.values()] == ["set", "keep"])

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
