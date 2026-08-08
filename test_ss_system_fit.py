#!/usr/bin/env python3
"""ScreenScraper keeps one record PER SYSTEM, so the system is part of the identity.

Live I10 violations, 2026-08-07:

  entry                platform   matched record         that record's system
  Crash Bandicoot 3    ps1        25279 (2008)           PlayStation 3
  Phantasy Star IV     genesis    9316  (2008)           Wii

Both are the right GAME and the wrong RELEASE — the 2008 PSN port and the 2008 Wii
Virtual Console edition — so they carry that release's art, metadata and date. The
invariant caught them by their year, but the year is a symptom; nothing ever compared
the candidate's system to the game's.

Two causes, and the fix needs both:

1. THE PER-SYSTEM SEARCH NEVER RAN. `SYSTEM_ID` is keyed on labels like 'psx' and
   'sega genesis' while the catalog stores the CANONICAL 'ps1' and 'genesis', so
   `systeme_id()` returned None for 27 of the 56 non-PC games and every one of them
   fell through to the cross-system pass. The correct PS1 record (19262, 1998) existed
   and was never asked for. Deriving the lookup from platmap.canon means the two lists
   cannot drift apart again.

2. NOTHING REJECTED A WRONG-SYSTEM CANDIDATE, and the result reported
   `"system": systems[0]` — the system we ASKED for, not the one we got. A match that
   describes itself by the question rather than the answer cannot be audited, which is
   why this stayed invisible.

Refusal only where there is evidence, per the rule game_era already follows: if the
entry's platform has no ScreenScraper system (PC has none, by design) or the candidate
does not say what system it is, nothing is refused.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-sysfit-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    import screenscraper as ss

    # 1. the catalog's own platform labels must resolve
    check("'ps1' resolves (catalog label, table said 'psx')", ss.systeme_id("ps1") == 57)
    check("'genesis' resolves (table said 'sega genesis')",
          ss.systeme_id("genesis") == 1)
    check("'gb' resolves (table said 'gameboy')", ss.systeme_id("gb") == 9)
    check("the original table keys still work", ss.systeme_id("psx") == 57)
    check("pc still has no system id, by design", ss.systeme_id("pc") is None)
    check("an unknown platform is still unknown",
          ss.systeme_id("not a console") is None)

    # 2. the system a candidate declares, and whether it fits the entry
    j_ps3 = {"id": 25279, "systeme": {"id": 59, "text": "Playstation 3"}}
    j_ps1 = {"id": 19262, "systeme": {"id": 57, "text": "Playstation"}}
    j_bare = {"id": 1, "systeme": {}}

    check("a candidate's own system is readable", ss.jeu_system_id(j_ps1) == 57)
    check("a candidate that does not say is None", ss.jeu_system_id(j_bare) is None)

    check("the PS1 record fits a ps1 entry", ss.system_fits("ps1", j_ps1) is True)
    check("the PS3 re-release does NOT fit a ps1 entry",
          ss.system_fits("ps1", j_ps3) is False)
    check("the Wii VC record does not fit a genesis entry",
          ss.system_fits("genesis", {"systeme": {"id": 16, "text": "Wii"}}) is False)

    # refusal needs evidence on BOTH sides — absence never refuses
    check("a pc entry refuses nothing (no ScreenScraper system exists)",
          ss.system_fits("pc", j_ps3) is True)
    check("a candidate that does not declare a system refuses nothing",
          ss.system_fits("ps1", j_bare) is True)
    check("an unmapped platform refuses nothing",
          ss.system_fits("not a console", j_ps3) is True)

    print("\n  %d/%d passed" % (sum(1 for _, c in PASS if c), len(PASS)))


if __name__ == "__main__":
    main()
