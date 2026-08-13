#!/usr/bin/env python3
"""A storefront's disambiguating year is not part of the game's name.

Steam names remakes to tell them apart — "Resident Evil 4 (2023)", "Mass Effect 2
(2021)", "Age of Empires II (2013)" — and 13 entries carry that shape live. The
resolver searched IGDB with the title EXACTLY as the store wrote it, parenthetical and
all, and IGDB's `search` has no such name: for "Mass Effect 2 (2021)" it returned zero
candidates, so a game IGDB knows perfectly well resolved to nothing. It had been
identified before (245113, the 2021 re-release) and a full `--all` re-resolve dropped
it.

`norm()` already strips the suffix — which is why `_title_matches` compares fine once
candidates exist. Only the query string carried it, so the failure was invisible
anywhere except a from-scratch resolve.

The year is not discarded, it is REPORTED: it says which release the store means, and
that is exactly what separates a remake from its original. Returning it lets the caller
use it as an era hint instead of throwing away the one fact that disambiguates.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-yearsuffix-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    import igdb_enrich as ie

    check("a trailing store year is stripped and reported",
          ie.split_store_year("Mass Effect 2 (2021)") == ("Mass Effect 2", 2021))
    check("...with the registered-trademark noise stores add",
          ie.split_store_year("Age of Empires® III (2007)")
          == ("Age of Empires® III", 2007))
    check("a title with no suffix is untouched",
          ie.split_store_year("Portal 2") == ("Portal 2", None))
    check("a year INSIDE the name is not a suffix",
          ie.split_store_year("Football Manager 2024") == ("Football Manager 2024", None))
    check("a parenthetical that is not a year is left alone",
          ie.split_store_year("Doom (Classic)") == ("Doom (Classic)", None))
    check("an implausible year is not treated as one",
          ie.split_store_year("Thing (1234)") == ("Thing (1234)", None))
    check("blank input does not explode", ie.split_store_year("") == ("", None))

    print("\n  %d/%d passed" % (sum(1 for _, c in PASS if c), len(PASS)))


if __name__ == "__main__":
    main()
