#!/usr/bin/env python3
"""The IMAGE wins, then the provider (#26, reopened).

Shape and filler already ranked above provider identity, but RESOLUTION did not: it sat
below `provider_priority`, so PRIORITY's ordering decided before size ever got a vote.
Live consequence — Phantasy Star IV rendered IGDB's 264x352 cover while a SteamGridDB
600x900 of the same game, correctly shaped and correctly keyed, sat unused. More than
five times the area, beaten on provider order alone.

Banded rather than raw pixels on purpose: an UNMEASURED asset has no pixel count and
would lose to every measured one, which silently re-privileges whichever provider
happens to be measurable — the same trap shape_ok's docstring warns about.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import media                                            # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    print("1. bands: large beats unknown beats small")
    check("600x900 is LARGE", media.res_band(600, 900) == media.RES_LARGE)
    check("264x352 is SMALL", media.res_band(264, 352) == media.RES_SMALL)
    check("unmeasured is UNKNOWN", media.res_band(None, None) == media.RES_UNKNOWN)
    check("ordering is large < unknown < small",
          media.RES_LARGE < media.RES_UNKNOWN < media.RES_SMALL)

    print("2. unmeasured is never last — it beats a demonstrably small image")
    check("unknown outranks small",
          media.res_band(None, None) < media.res_band(264, 352))
    check("but loses to a large one",
          media.res_band(None, None) > media.res_band(600, 900))

    print("3. junk dimensions degrade to unknown, never to a band they didn't earn")
    check("zero is unknown", media.res_band(0, 0) == media.RES_UNKNOWN)
    check("non-numeric is unknown", media.res_band("x", "y") == media.RES_UNKNOWN)

    print("4. the live case: the big cover now outranks the thumbnail")
    sgdb = (media.res_band(600, 900), 6)      # steamgriddb sits at PRIORITY index 6
    igdb = (media.res_band(264, 352), 5)      # igdb at 5 — it USED to win on this alone
    check("SteamGridDB 600x900 sorts ahead of IGDB 264x352", sgdb < igdb)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
