#!/usr/bin/env python3
"""ONE selector decides an IGDB identity. Not two, and not two-and-a-half.

`_provider_match()` forked on `if consoles:` — emulation platforms went to
`igdb_enrich._pick_era_aware()`, and store/PC titles went to a second, weaker inline
matcher. Its own docstring already claimed that fork was closed ("the wand now identifies
exactly as build_library would, instead of via a second, weaker matcher"), but the claim
only ever covered the consoles branch.

The store branch is what bound the owned 2013 Steam **Star Trek** (appid 203250) to
igdb:11485, the 1971 mainframe record. And `_pick_era_aware` already knew better — its
`require_unique` docstring names this exact game:

    refuse when 2+ same-name games remain, because a generic store title is a coin-flip
    — IGDB ranks the 1973 mainframe "Star Trek" above the 2013 game you actually own.

The catalog sync passes `require_unique=bool(appid)` (igdb_enrich.py:895) and correctly
refused, which is why `igdb_resolution` holds `matched_by='none'` for it. Only the wand
path had the weak copy.

Commit 7249399 fixed the BEHAVIOUR but added `matchgate.pick_by_year()` as a THIRD
selector rather than removing the fork. This test pins the fork closed: one call, both
branches, and the year disambiguation folded into the selector that already existed.

Nothing is lost by unifying. `_igdb_raw_hits()` already queries BOTH `search "..."` and
`where name ~ "..."`, which is the candidate-set merge the legacy branch existed to do —
and it additionally carries `alternative_names`, so the unified path matches MORE titles
(a romanized ROM name resolving to its real entry), not fewer.
"""
import os
import re
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))
import test_support                              # noqa: E402
test_support.isolate("ludodex-onesel-")

import matchgate                                 # noqa: E402


def check(label, cond):
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def body_of(src, name):
    """Source text of one top-level def, up to the next top-level def."""
    m = re.search(r"^def %s\(.*?(?=^def )" % re.escape(name), src, re.S | re.M)
    return m.group(0) if m else ""


def main():
    app = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read()
    enr = open(os.path.join(DIR, "ludodex", "igdb_enrich.py"), encoding="utf-8").read()
    pm = body_of(app, "_provider_match")
    check("_provider_match exists", bool(pm))

    # 1. the fork is gone — no branch choosing between two matchers
    check("no `if consoles:` fork in _provider_match",
          not re.search(r"^\s{4}if consoles:\s*$", pm, re.M))

    # 2. exactly one selector call, and it is the shared one
    check("calls _pick_era_aware exactly once",
          pm.count("_pick_era_aware(") == 1)
    check("does not run its own candidate ranking",
          "sorted(" not in pm and "9999" not in pm)
    check("does not build its own candidate dict",
          "cands" not in pm)

    # 3. the store case is the one require_unique was written for
    check("passes require_unique so an ambiguous store title refuses",
          "require_unique" in pm)

    # 4. the year reaches the selector rather than being applied beside it
    check("_provider_match hands the stated year to the selector",
          "year=year" in pm)
    check("_pick_era_aware accepts a year",
          re.search(r"^def _pick_era_aware\([^)]*year", enr, re.M | re.S) is not None)

    # 5. the year rule has ONE home, and the selector calls it there
    check("_pick_era_aware defers to matchgate.pick_by_year",
          "pick_by_year(" in body_of(enr, "_pick_era_aware"))
    check("matchgate still owns the rule", hasattr(matchgate, "pick_by_year"))

    print("test_one_selector: all checks passed")


if __name__ == "__main__":
    main()
