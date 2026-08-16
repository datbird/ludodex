#!/usr/bin/env python3
"""A plural kind has no `chosen` row, and until now nothing ranked it either.

select() sets `chosen` for SCALAR kinds only, which is correct — a game has one cover
but many screenshots, and collapsing them would discard the rest. But the ORDER is
user-visible: the panel calls the first one #1 and leads with it. That order was row id,
i.e. whatever arrived first.

Live consequence: Genesis Sonic 2 led with a pillarboxed Game Gear screenshot. Six IGDB
candidates were all exactly 1280x720, so every resolution term was equal; the screenshot
priority list named gamelist/esde/screenscraper/launchbox while the actual rows were
Steam (24,867) and IGDB (16,858), so provider rank was constant too. With every term
constant the sort fell through to row id — and the wrong one had the lowest.

  * A CONSTANT TERM DECIDES NOTHING. That is this codebase's stated rule, and a priority
    list that omits 99.8% of the rows makes provider rank exactly that.
  * SCALAR KINDS MUST BE UNTOUCHED. They already have `chosen`; re-ordering them here
    would be a second, quietly different opinion about the same question.
  * A PIN OUTRANKS THE LOT. Ranking is a default; a pin is the user speaking.
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
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import media

    print("1. the screenshot priority names the providers that actually supply them")
    pri = media.priority("screenshot")
    check("steam is ranked: %s" % (pri[:3],), "steam" in pri)
    check("igdb is ranked", "igdb" in pri)
    check("igdb outranks screenscraper, as asked",
          pri.index("igdb") < pri.index("screenscraper"))
    check("and neither falls to the unranked default",
          media.priority("screenshot") is not media.DEFAULT_PRIORITY)

    print()
    print("2. THE SONIC CASE — equal-resolution candidates no longer tie on row id")
    # Six 1280x720 IGDB shots and one 320x224 ScreenScraper shot: exactly the live data.
    igdb = media.display_key("screenshot", "igdb", 1280, 720)
    ss = media.display_key("screenshot", "screenscraper", 320, 224)
    check("igdb sorts before screenscraper", igdb < ss)
    steam = media.display_key("screenshot", "steam", 1920, 1080)
    check("steam sorts before igdb", steam < igdb)
    check("the ordering is total, not a tie",
          len({steam, igdb, ss}) == 3)

    print()
    print("3. resolution still beats provider — a big SS shot beats a tiny IGDB one")
    # Provider preference must not become a trump card: the band term sits above it,
    # exactly as it does in select().
    big_ss = media.display_key("screenshot", "screenscraper", 1920, 1080)
    tiny_igdb = media.display_key("screenshot", "igdb", 160, 120)
    check("a large screenscraper shot outranks a tiny igdb one", big_ss < tiny_igdb)

    print()
    print("4. an asset of the wrong SHAPE for its kind goes last")
    # A portrait image filed as a screenshot is not a screenshot.
    tall = media.display_key("screenshot", "steam", 600, 1600)
    wide = media.display_key("screenshot", "launchbox", 640, 360)
    check("wrong shape loses even to the lowest-ranked provider", wide < tall)

    print()
    print("5. unknown dimensions are not penalised as if they were small")
    # UNKNOWN sits between LARGE and SMALL; a missing measurement is not evidence.
    unknown = media.display_key("screenshot", "igdb", None, None)
    small = media.display_key("screenshot", "igdb", 160, 120)
    large = media.display_key("screenshot", "igdb", 1920, 1080)
    check("large before unknown", large < unknown)
    check("unknown before small", unknown < small)

    print()
    print("6. scalar kinds keep their own priority lists, untouched")
    for kind in ("cover", "logo", "background", "hero", "header"):
        check("%s priority unchanged" % kind,
              media.priority(kind) and "steamgrid" in media.priority(kind))
    check("screenshot is still NOT scalar — it must not collapse to one",
          "screenshot" not in media.SCALAR_KINDS)

    print()
    print("7. every kind's priority now covers the providers it actually has")
    # The audit that found this bug, kept as a test so a new provider cannot silently
    # re-create it for some other kind.
    for kind in ("screenshot", "cover", "background", "logo", "hero", "header"):
        pri = media.priority(kind)
        check("%-11s names >= 4 providers" % kind, len(pri) >= 4)

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
