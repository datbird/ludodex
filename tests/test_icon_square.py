#!/usr/bin/env python3
"""An icon is a SQUARE, and until now there was no way to say so.

`KIND_ORIENT` had two values, portrait and landscape, and `shape_ok` TOLERATED square
everywhere as an escape hatch — so a kind whose shape genuinely is square had no shape
test at all. Live, that let 14 games serve a non-square asset as their icon: eleven
32x64 Genesis cartridge END-LABELS, Treasure's PUBLISHER wordmark at 600x259, and a
600x140 strip. Each was the only candidate in its bucket, so nothing was ever compared
and nothing ever objected.

This also unblocks the icon resolution band. A resolution band only means "better" once
shape is constrained: dry-run before this rule existed, giving icons their own line
promoted a 600x300 STRIP into an icon slot on size alone.
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
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import test_support
    test_support.isolate("ludodex-iconsq-")
    import media

    print("1. a square kind must actually be square")
    check("256x256 is fine", media.shape_ok("icon", 256, 256))
    check("128x128 is fine", media.shape_ok("icon", 128, 128))
    check("the 32x64 cartridge end-label is not", not media.shape_ok("icon", 32, 64))
    check("Treasure's 600x259 publisher wordmark is not",
          not media.shape_ok("icon", 600, 259))
    check("the 600x140 strip is not", not media.shape_ok("icon", 600, 140))
    check("the 600x300 pack tile is not", not media.shape_ok("icon", 600, 300))

    print()
    print("2. the tolerance is a band, not an equality test")
    check("240x256 counts as square", media.shape_ok("icon", 240, 256))
    check("256x240 counts as square", media.shape_ok("icon", 256, 240))
    check("a 2:1 strip never does", not media.shape_ok("icon", 512, 256))

    print()
    print("3. unmeasured is still never penalised")
    check("no dimensions", media.shape_ok("icon", None, None))
    check("zero height does not raise", media.shape_ok("icon", 64, 0))

    print()
    print("4. square is still TOLERATED for the kinds that merely allow it")
    check("a square cover is still acceptable", media.shape_ok("cover", 500, 500))
    check("a square background is still acceptable", media.shape_ok("background", 500, 500))
    check("a landscape cover is still rejected", not media.shape_ok("cover", 900, 600))

    print()
    print("5. the band is unblocked now that shape is constrained")
    check("icon has its own resolution line", "icon" in media.KIND_TARGET_PX)
    check("256x256 is LARGE for an icon", media.res_band(256, 256, "icon") == media.RES_LARGE)
    check("64x64 is SMALL for an icon", media.res_band(64, 64, "icon") == media.RES_SMALL)

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
