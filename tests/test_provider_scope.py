#!/usr/bin/env python3
"""Per-provider scope: on/off, and per SOURCE and per PLATFORM (datbird, 2026-08-04).

The reason this exists is cost, and the cost is wildly uneven: ScreenScraper answers in
~10s for a game it has and takes ~2 MINUTES for one it does not — its search is by name,
and it has no `pc` system id, so PC titles fall to the slow cross-system path. On a
2000-game PC library that is hours of wall clock for a provider that mostly covers
consoles. "ScreenScraper: consoles only" turns that into minutes without giving up the
coverage it is genuinely good at.

Everything is ON by default and only EXCLUSIONS are stored, so a newly imported store or
platform is automatically included rather than silently skipped — the failure mode where a
feature quietly stops covering new things is exactly what this project keeps hitting.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_support                              # noqa: E402
test_support.isolate("ludodex-scope-")

import config                                    # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    print("1. everything is on by default, with nothing stored")
    # deliberately NOT set — the default must be on, including for steamgriddb, whose
    # media_ flag defaults OFF. Matching is not taking art.
    check("a provider with nothing configured is on",
          config.provider_allowed("steamgriddb", "pc", {"steam"}))
    check("no platform excluded", config.provider_off_platforms("screenscraper") == set())
    check("no source excluded", config.provider_off_sources("screenscraper") == set())
    check("a pc/steam game is allowed",
          config.provider_allowed("screenscraper", "pc", {"steam"}))
    check("a genesis/emulation game is allowed",
          config.provider_allowed("screenscraper", "genesis", {"emulation"}))

    print("2. the master switch overrides everything")
    config.set_("provider_screenscraper_enabled", "0")
    check("off means off", not config.provider_allowed("screenscraper", "genesis", {"x"}))
    config.set_("provider_screenscraper_enabled", "1")

    print("3. per-PLATFORM exclusion — the 'consoles only' case")
    config.set_provider_scope("screenscraper", off_platforms=["pc"])
    check("pc is skipped", not config.provider_allowed("screenscraper", "pc", {"steam"}))
    check("genesis still runs",
          config.provider_allowed("screenscraper", "genesis", {"emulation"}))
    check("a platform added LATER is included, not silently skipped",
          config.provider_allowed("screenscraper", "dreamcast", {"emulation"}))

    print("4. per-SOURCE exclusion")
    config.set_provider_scope("screenscraper", off_platforms=[], off_sources=["steam"])
    check("a steam-only game is skipped",
          not config.provider_allowed("screenscraper", "pc", {"steam"}))
    # "every source", not "any": a game owned on Steam AND as a ROM is still a ROM, and
    # turning Steam off must not remove it from a provider that covers the ROM side.
    check("a game owned on steam AND as a rom still runs",
          config.provider_allowed("screenscraper", "genesis", {"steam", "emulation"}))
    check("a game with no source recorded still runs",
          config.provider_allowed("screenscraper", "pc", set()))

    print("5. scope is per provider, not global")
    config.set_("provider_steamgriddb_enabled", "1")
    check("steamgriddb is unaffected by screenscraper's exclusions",
          config.provider_allowed("steamgriddb", "pc", {"steam"}))

    print("6. exclusions round-trip and can be cleared")
    config.set_provider_scope("screenscraper", off_sources=[], off_platforms=["pc", "ps2"])
    check("stored", config.provider_off_platforms("screenscraper") == {"pc", "ps2"})
    config.set_provider_scope("screenscraper", off_platforms=[])
    check("cleared", config.provider_off_platforms("screenscraper") == set())
    check("and everything is allowed again",
          config.provider_allowed("screenscraper", "pc", {"steam"}))

    print("7. every provider advertises its per-game cost")
    from server import app as srv
    for p in ("igdb", "steam", "screenscraper", "steamgriddb"):
        check("%s has a cost description" % p, bool(srv.PROVIDER_COST.get(p)))
    check("screenscraper's names the expensive case",
          "2min" in srv.PROVIDER_COST["screenscraper"])

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
