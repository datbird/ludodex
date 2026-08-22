#!/usr/bin/env python3
"""A sequel is not its original. The gate said so and did not do it.

`score()`'s own docstring already claims this case is refused:

    The gate is not fractional: EVERY distinguishing word of the owned title has to
    appear in the candidate. "Boltgun" is not "Boltgun 2" …

It was half true. `_covers()` measures the OWNED title's significant words inside the
CANDIDATE, so it catches "Boltgun 2" <- "Boltgun" and is blind to the mirror image:
a sequel CONTAINS its original's whole title, so coverage is perfect, and the extra
numeral was only ever scored (`nc`), never gated. Live, `matchgate.score(["Portal"],
"Portal 2")` returned accept=True.

Found 2026-08-22 by invariant I9 after the provider sweep: `evoland` took ScreenScraper
296456, which is "Evoland II", the id its real sequel already held. Two owned titles, one
provider id, and the loser inherits the winner's art. The correct record for `evoland` is
296454 ("Evoland", 2015).

Damage was limited because an exact-title candidate scores 2.00 and a sequel 1.50, so the
right record wins whenever it is in the candidate set. Evoland collided because SS's
search for "Evoland" did not return 296454 at all. That is 1 collision in 1,612 matches,
which is why this survived until a sweep made the sample big enough to show it.

THE VALUE-1 EXEMPTION, and why it is not a fudge. The first entry in a series is what an
unnumbered owned title already means, so a candidate numbered 1 is the same product.
Measured against all 934 name-derived identities in the live library:

  without it   9 new refusals, 2 of them WRONG:
                 "Being a DIK"      <- "Being a DIK: Season 1"
                 "The Walking Dead" <- "The Walking Dead : Saison 1"
  with it      7 new refusals, all 7 correct, zero collateral.

It still refuses "The Walking Dead: Saints & Sinners" <- "… - Chapter 2", which IS a
different product. Numerals compare by value and not notation, so "II" and "2" behave
alike, matching `numbering_variant`.

The 7 it refuses: Devil May Cry <- Devil May Cry 4; Evoland <- Evoland II; Final Fantasy
<- Final Fantasy 5; Little Nightmares <- Little Nightmares III; Mount & Blade <- Mount &
Blade II: Bannerlord; Saints & Sinners <- Chapter 2; and Final Fantasy Tactics: The
Ivalice Chronicles <- the "Nintendo Switch 2 Edition" record, which is a wrong-PLATFORM
match that happens to carry a numeral. Right outcome, partly by luck, and worth saying so.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))
import test_support                              # noqa: E402
test_support.isolate("ludodex-sequel-")

import matchgate                                 # noqa: E402


def check(label, cond):
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def accepts(owned, cand):
    return matchgate.score([owned], cand)[0]


REFUSE = [
    ("Evoland", "Evoland II"),                       # the live I9 collision
    ("Portal", "Portal 2"),
    ("Contra", "Contra III"),
    ("Devil May Cry", "Devil May Cry 4"),
    ("Final Fantasy", "Final Fantasy 5"),
    ("Little Nightmares", "Little Nightmares III"),
    ("Mount & Blade", "Mount & Blade II: Bannerlord"),
    ("The Walking Dead: Saints & Sinners",
     "The Walking Dead: Saints & Sinners - Chapter 2"),
]

KEEP = [
    ("Evoland", "Evoland"),                          # exact
    ("Evoland 2", "Evoland II"),                     # value, not notation
    ("Being a DIK", "Being a DIK: Season 1"),        # value-1 exemption
    ("The Walking Dead", "The Walking Dead : Saison 1"),
    ("Foo", "Foo 1"),                                # retro-numbered original
    ("Mega Man X", "Mega Man X"),                    # a bare x is never a numeral
    ("Quake Mission Pack 1", "Quake Mission Pack No. I"),
    ("Police Quest IV: Open Season", "Police Quest: Open Season"),
    ("Mass Effect 2 (2021)", "Mass Effect 2"),       # our own year suffix is noise
]


def main():
    for owned, cand in REFUSE:
        check("refuses %-36r <- %r" % (owned, cand), not accepts(owned, cand))
    for owned, cand in KEEP:
        check("keeps   %-36r <- %r" % (owned, cand), accepts(owned, cand))
    print("test_sequel_numeral: all checks passed")


if __name__ == "__main__":
    main()
