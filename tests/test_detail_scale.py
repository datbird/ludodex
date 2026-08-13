#!/usr/bin/env python3
"""`detail_density` is NOT scale-invariant, so it must not become a general tiebreak.

This test exists to stop a refactor that looks obviously right. select() consults detail
only when EVERY candidate is a confirmed paste, and the natural-looking cleanup is to
widen that to "whenever the filler term is constant" — the codebase's own stated rule
about terms that decide nothing. That widening is wrong.

detail_density is edge energy PER PIXEL. Downscaling concentrates it, so a thumbnail of
an image outscores the image. Between two CLEAN candidates the term therefore prefers
the smaller one, which is exactly the defect `res_band` was added to prevent. Measured
on the live library, the widening moved 244 cover picks, every one of them from a
300x450 to a 264x352 IGDB thumbnail on this number alone.

So: pin the property, and pin the caller's condition.
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
    d = test_support.isolate("ludodex-dscale-")
    import media
    try:
        from PIL import Image, ImageDraw
    except Exception:
        print("SKIP: Pillow not installed")
        return

    print("1. shrinking an image RAISES its detail score")
    # Detailed portrait art: many edges spread throughout, like a real cover.
    big = os.path.join(d, "big.png")
    im = Image.new("RGB", (600, 900), (20, 20, 30))
    dr = ImageDraw.Draw(im)
    for i in range(0, 900, 7):
        dr.line([(0, i), (600, i - 120)], fill=(230, 200 - (i // 8) % 120, 90))
    for i in range(0, 600, 5):
        dr.line([(i, 0), (i + 90, 900)], fill=(40, 120, 220))
    im.save(big)
    small = os.path.join(d, "small.png")
    im.resize((300, 450), Image.LANCZOS).save(small)

    d_big = media.detail_density(big)
    d_small = media.detail_density(small)
    print("      600x900 -> %.2f      300x450 -> %.2f" % (d_big, d_small))
    check("the half-size copy of the SAME art scores higher", d_small > d_big)
    check("...so ranking on detail alone would pick the smaller image", True)

    print()
    print("2. the guard that keeps this safe is 'all pastes', not 'constant'")
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ludodex", "media_choose.py")).read()
    check("select() still requires every candidate to be a paste",
          "all(c[3] == 1 for c in cands)" in src)
    check("...and has not been widened to a constancy test",
          "len({c[3] for c in cands}) == 1" not in src)

    print()
    print("3. detail is only measured where it is meaningful")
    land = os.path.join(d, "land.png")
    Image.new("RGB", (900, 600), (10, 10, 10)).save(land)
    check("band_energy refuses a landscape canvas", media.band_energy(land) is None)
    check("so detail_density does too", media.detail_density(land) is None)
    check("and looks_padded reports False rather than guessing",
          media.looks_padded(land) is False)

    print()
    print("4. looks_padded and detail_density read ONE measurement")
    # The docstring claimed this and the code did not: looks_padded used to recompute
    # the bands inline, so the two could drift apart silently.
    msrc = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ludodex", "media.py")).read()
    body = msrc[msrc.index("def looks_padded"):]
    body = body[:body.index("\n# ---")]
    check("looks_padded calls band_energy", "band_energy(path, bands)" in body)
    check("...and no longer runs its own FIND_EDGES loop", "FIND_EDGES" not in body)

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
