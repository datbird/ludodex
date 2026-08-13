#!/usr/bin/env python3
""""Big enough" is meaningless without saying big enough FOR WHAT.

`res_band` sits above provider priority so the image can outrank the supplier. It did
that with ONE 250,000-pixel line for all 23 kinds, and 460x215 is a perfectly good
header while being a hopeless full-screen background. Measured on the live library that
line was CONSTANT for 8 of 13 scalar kinds — 100% LARGE for background/hero/bezel/mix/
box_back, 100% SMALL for header, 97% SMALL for logo.

A term that is constant across every candidate has decided nothing. `background` also
has no filler verdict (band_energy is undefined for a landscape canvas) and no useful
shape test, so with the band dead there was NOTHING above provider order: 1,808 of its
slots were settled with a larger candidate sitting unused and no evidence against it.

These cases pin the parts that make a per-kind line safe rather than merely different:
the fraction reproduces the one hand-picked line with a track record, an unlisted kind
keeps the global default instead of getting an invented number, and UNKNOWN still sits
in the middle so an unmeasured asset is never punished for being unmeasured.
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
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "ludodex"))
    import test_support
    test_support.isolate("ludodex-resband-")
    import media

    print("1. the fraction reproduces the line that already worked")
    # 250,000 was hand-picked for covers and is known good. Half of a 600x900 cover is
    # 270,000. If the formula did not land on that, it would be a new rule wearing the
    # old one's clothes.
    check("the cover line stays within 10%% of the hand-picked 250,000 (%d)"
          % media.res_min_px("cover"),
          abs(media.res_min_px("cover") - 250_000) / 250_000 < 0.10)
    check("300x450 is still SMALL for a cover",
          media.res_band(300, 450, "cover") == media.RES_SMALL)
    check("600x900 is still LARGE for a cover",
          media.res_band(600, 900, "cover") == media.RES_LARGE)

    print()
    print("2. the same pixels mean different things for different kinds")
    # 460x215 = 98,900 px. A real Steam header, and nowhere near a background.
    check("a 460x215 header is LARGE for a header",
          media.res_band(460, 215, "header") == media.RES_LARGE)
    check("...and the same image is SMALL for a background",
          media.res_band(460, 215, "background") == media.RES_SMALL)
    check("a 1280x720 background is SMALL (it cannot fill the screen)",
          media.res_band(1280, 720, "background") == media.RES_SMALL)
    check("a 1920x1080 background is LARGE",
          media.res_band(1920, 1080, "background") == media.RES_LARGE)

    print()
    print("3. an unlisted kind keeps the global line, not an invented one")
    check("title_screen falls back to the global default",
          media.res_min_px("title_screen") == media.res_min_px(None) == 250_000)
    check("an unknown kind name does too", media.res_min_px("nonsense") == 250_000)
    check("no kind, no argument — unchanged behaviour for old callers",
          media.res_band(600, 900) == media.RES_LARGE
          and media.res_band(264, 352) == media.RES_SMALL)

    print()
    print("4. a size line is only safe for a kind whose SHAPE is settled")
    # "Bigger is better" is only true once you know what the thing should look like.
    # `icon` was held out of this table until KIND_ORIENT could require square: dry-run
    # without that rule, an icon line promoted a 600x300 STRIP into an icon slot on size
    # alone. A kind may be exempt only by having NO canonical shape to violate, which
    # has to be a decision someone made rather than a gap nobody noticed — so new
    # entries fail here until they are classified.
    SHAPE_FREE = {
        # A clear-logo is a wordmark: wide for "SUPER METROID", near-square for an
        # emblem. There is no shape to enforce, so there is none to violate, and
        # nothing wrong-shaped can be promoted by preferring the larger one.
        "logo",
    }
    for k in media.KIND_TARGET_PX:
        check("%s: shape rule, or a recorded reason it needs none" % k,
              k in media.KIND_ORIENT or k in SHAPE_FREE)
    for k in SHAPE_FREE:
        check("%s really is absent from KIND_ORIENT (the exemption is real)" % k,
              k not in media.KIND_ORIENT)
    check("a 600x300 strip is disqualified as an icon before size is ever consulted",
          not media.shape_ok("icon", 600, 300))

    print()
    print("5. UNKNOWN still sits in the middle, for every kind")
    for k in list(media.KIND_TARGET_PX) + ["title_screen", None]:
        assert media.res_band(None, None, k) == media.RES_UNKNOWN
        assert media.res_band(1, 1, k) > media.res_band(None, None, k)
        assert media.res_band(4000, 4000, k) < media.res_band(None, None, k)
    check("unmeasured beats demonstrably small and loses to demonstrably large, "
          "for every listed kind", True)
    check("zero and non-numeric are still UNKNOWN",
          media.res_band(0, 0, "background") == media.RES_UNKNOWN
          and media.res_band("x", "y", "background") == media.RES_UNKNOWN)

    print()
    print("6. every listed target is a real surface, not a round number")
    for k, px in media.KIND_TARGET_PX.items():
        check("%s's target (%d px) is at least a 128x128 surface" % (k, px), px >= 16384)

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
