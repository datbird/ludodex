#!/usr/bin/env python3
"""A console entry must not bind to an IGDB record that console never had.

IGDB keeps a record per RELEASE, and a port carries the IDENTICAL title, so the exact
-name matcher cannot separate them. The era gate is what was supposed to, and it never
fired for these games: `_consoles_by_norm()` collects only `source='emulation'` rows,
and a Sega Genesis Classics or Mega Man Legacy member is owned on STEAM while sitting at
`games.platform='genesis'`. Real hardware, storefront source — so no console, no era
gate, and IGDB's top exact-title hit won.

What that produced live, 2026-08-07:

  entry            platform   IGDB record bound        its first_release
  gunstar heroes   genesis    248636 "Gunstar Heroes"  1995   (Genesis: 1993)
  mega man x       snes       88645  "Mega Man X"      2011   (SNES: 1993)
  contra           arcade     217544 "Contra"          2006   (arcade: 1987)

And it did not stop there. `matchgate.game_era` reads that record's date as the GAME's
era, so the bad year then made a wrong-SYSTEM ScreenScraper record look CONSISTENT — a
Game Gear Streets of Rage dated 1995 agreeing with a 1995 era — and I10 stayed green
over both. One wrong identity, propagated as ground truth.

Two fixes, both here:

1. A console `games.platform` counts as a console for the era gate, whoever sold it.
   Storefront labels ('pc', 'steam', a bare 'xbox' that spans generations) still do not,
   which is the distinction the emulation-only rule was protecting.

2. PLATFORM FIT, when IGDB says. IGDB is authoritative about which platforms a game
   released on — `entry_fits` already exists and says so — but the title-level matcher
   never asked, and did not even request the `platforms` field. A candidate IGDB lists
   without this entry's platform is a different release.

Refusal needs evidence on both sides, as everywhere else: a candidate with no platform
list, or an entry whose platform is not real hardware, is not refused.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-igdbfit-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    import igdb_enrich as ie

    GEN = {"id": 89, "name": "Gunstar Heroes", "slug": "gh-md",
           "first_release_date": 715000000,                  # 1992-ish
           "platforms": [{"name": "Sega Mega Drive/Genesis"}]}
    PORT = {"id": 248636, "name": "Gunstar Heroes", "slug": "gh-port",
            "first_release_date": 800000000,                 # 1995-ish
            "platforms": [{"name": "PC (Microsoft Windows)"}]}

    # 1. platform fit picks the record the console actually had
    iid, _ = ie._pick_era_aware([PORT, GEN], "gunstar heroes", [], platform="genesis")
    check("a genesis entry binds the Genesis record, not the PC port", iid == 89)

    iid, _ = ie._pick_era_aware([GEN, PORT], "gunstar heroes", [], platform="genesis")
    check("and the order IGDB returned them in does not decide it", iid == 89)

    # 2. no evidence -> no refusal
    NOPLAT = {"id": 7, "name": "Gunstar Heroes", "slug": "x",
              "first_release_date": 800000000}
    iid, _ = ie._pick_era_aware([NOPLAT], "gunstar heroes", [], platform="genesis")
    check("a candidate with no platform list is not refused", iid == 7)

    iid, _ = ie._pick_era_aware([PORT], "gunstar heroes", [], platform="pc")
    check("a pc entry is not platform-gated", iid == 248636)

    iid, _ = ie._pick_era_aware([PORT], "gunstar heroes", [])
    check("no platform given behaves exactly as before", iid == 248636)

    # 3. when NOTHING fits, the entry stays unmatched rather than taking a wrong release
    iid, _ = ie._pick_era_aware([PORT], "gunstar heroes", [], platform="genesis")
    check("a console entry with no fitting record stays unmatched", iid == 0)

    # 4. a console platform must count as a console for the era gate, whoever sold it
    # the era table had the SAME label-vs-canon hole, and a gate that answers "no era
    # known" refuses nothing — so every console entry the catalog labels canonically
    # went unguarded
    import console_eras
    check("the era table resolves the catalog's own label",
          console_eras.era("genesis") == console_eras.era("sega genesis"))
    check("...and for ps1/psx", console_eras.era("ps1") == console_eras.era("psx"))
    check("a canon several eras share stays unknown", console_eras.era("pc") is None)

    check("'genesis' is real hardware", ie.is_console_platform("genesis") is True)
    check("'snes' is real hardware", ie.is_console_platform("snes") is True)
    check("'pc' is not", ie.is_console_platform("pc") is False)
    check("a storefront label is not", ie.is_console_platform("steam") is False)

    print("\n  %d/%d passed" % (sum(1 for _, c in PASS if c), len(PASS)))


if __name__ == "__main__":
    main()
