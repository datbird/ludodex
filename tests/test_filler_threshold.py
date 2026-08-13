#!/usr/bin/env python3
"""A bright wordmark must not make the rest of a cover look like padding.

`looks_padded()` finds Steam's letterboxed auto-`portrait.png` by looking for a
contiguous run of dead-edge-energy bands. It set the "dead" line RELATIVE to the
brightest band: `dead = max(4.0, peak * 0.25)`. `peak` is an outlier — one band
holding a high-contrast title wordmark is enough to drag the line above the rest of
the artwork, and dark box art then reads as padding end to end.

Live, 2026-08-07 (Arx Fatalis): all THREE cover candidates came back filler=1 — the
genuine padded paste AND the two authored covers. With the term constant across every
candidate it stops discriminating, ranking falls through to the resolution band, and
Steam's 600x900 letterboxed paste beats a 300x450 authored cover on size. The padded
one was caught only by accident: it is so uniformly blurred that its peak is low and
the threshold hit its 4.0 floor.

  #1 padded    peak  8.8 -> line  4.0 -> 5 dead bands  (correct)
  #2 authored  peak 49.0 -> line 12.3 -> 7 dead bands  (false)
  #3 authored  peak 61.2 -> line 15.3 -> 7 dead bands  (false)

Measured over all 5,245 cover files: the relative line flags 1,205 of which only 1,080
are Steam auto-portraits, so ~125 authored covers are libelled; 31 games were serving a
Steam auto-portrait while a real cover sat unused. A flat line keeps every known padded
paste and clears every known authored cover anywhere in 4.5..7.0 — a wide plateau, so
the value is not fitted to the samples.

The distinction these pin: padding is BLURRED, so its absolute high-frequency energy is
near zero. That is a property of the padding itself, not of how bright the rest of the
image happens to be — so the line must not move when the artwork gets more contrasty.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-filler-")

try:
    from PIL import Image, ImageDraw, ImageFilter     # noqa: F401
except Exception:                                     # noqa: BLE001
    sys.exit("SKIPPED: Pillow not installed")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _noise(dr, box, step, a, b):
    """Fill `box` with alternating horizontal lines — edge energy scales with |a-b|
    and inversely with `step`. Deterministic: no RNG."""
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        dr.line([(x0, y), (x1, y)], fill=(a if (y // step) % 2 == 0 else b))


def padded_paste(path, w=600, h=900, pad_tone=118, pad_alt=120):
    """Steam's shape: a near-flat blurred field with the header pasted across a strip.
    `pad_*` set how much residual energy the blur leaves — the real ones measure ~1-4."""
    im = Image.new("L", (w, h), pad_tone)
    dr = ImageDraw.Draw(im)
    _noise(dr, (0, 0, w, h), 6, pad_tone, pad_alt)          # faint blur residue
    _noise(dr, (0, int(h * 0.15), w, int(h * 0.42)), 2, 10, 245)   # the pasted strip
    im.save(path)
    return path


def authored_cover(path, w=300, h=450):
    """Dark authored box art: modest detail throughout, plus ONE very high-contrast
    band — the metallic title wordmark that inflated `peak` on the live covers.

    Calibrated to the live measurements, because the bug only appears at the real
    ratio: the dark scene lands ~10 per band against a ~125 wordmark peak, matching
    Arx Fatalis's authored cover (scene 4.7-12.1, peak 49.0). A scene at 30% of peak
    — which an uncalibrated fixture happily produces — sits above the relative line
    and hides the defect entirely."""
    im = Image.new("L", (w, h), 12)
    dr = ImageDraw.Draw(im)
    _noise(dr, (0, 0, w, h), 3, 12, 22)                     # the dark scene  (~10)
    _noise(dr, (0, int(h * 0.11), w, int(h * 0.22)), 1, 0, 255)    # wordmark  (~125)
    im.save(path)
    return path


def main():
    import media

    pad = padded_paste(os.path.join(D, "padded.png"))
    light = padded_paste(os.path.join(D, "padded_light.png"),
                         pad_tone=110, pad_alt=116)   # a lighter blur, more residue
    art = authored_cover(os.path.join(D, "authored.png"))

    check("a letterboxed paste is still detected", media.looks_padded(pad) is True)
    check("a paste with a lighter blur is detected too",
          media.looks_padded(light) is True)
    check("authored art with a bright wordmark is NOT called padded",
          media.looks_padded(art) is False)

    # the actual regression: the verdict must not depend on how contrasty the rest of
    # the image is. Same artwork, brighter wordmark -> same answer.
    im = Image.open(art).convert("L")
    dr = ImageDraw.Draw(im)
    _noise(dr, (0, int(450 * 0.11), 300, int(450 * 0.22)), 1, 0, 255)
    hot = os.path.join(D, "authored_hotter.png")
    im.save(hot)
    check("a brighter wordmark does not change the verdict",
          media.looks_padded(hot) == media.looks_padded(art))

    # landscape is out of scope by construction — the rule is about portrait canvases
    wide = Image.new("L", (600, 300), 128)
    wp = os.path.join(D, "wide.png")
    wide.save(wp)
    check("a landscape image is never called padded", media.looks_padded(wp) is False)

    print("\n  %d/%d passed" % (sum(1 for _, c in PASS if c), len(PASS)))


if __name__ == "__main__":
    main()
