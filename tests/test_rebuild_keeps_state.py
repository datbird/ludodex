#!/usr/bin/env python3
"""A rebuild must carry a source row's STATE, not just its identity.

`sources.state` is what separates "I own the Switch ROM" from "I want the Switch ROM".
The carry-over pass that re-seeds source rows from the previous catalog read five
columns and called `add()` without the sixth, so every carried row took `add()`'s
`state="have"` default. Two things then conspired:

  * carry-over runs BEFORE the ownership merge, so the false `have` lands first, and
  * the dedupe rule inside `add()` is "have wins over want", written for a genuine
    merge of two source rows and correct there.

So the real `want` arriving afterwards was refused as a downgrade, and every per-format
want in the library silently became ownership on the next rebuild. There is no path back:
the next rebuild carries the `have` forward as fact.

`via_collection` was dropped the same way. A copy credited to an owned compilation
(DESIGN §13) came back as a direct purchase, losing the provenance the whole collection
feature is built on.

Offline. No network. Drives the real build_library twice over a fixture data dir, which
is the only way to exercise the carry-over branch at all — it only runs when a previous
catalog exists.
"""
import os
import sqlite3
import subprocess
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-rebuild-state-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import ownership                                               # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def build(n):
    env = dict(os.environ, LUDODEX_DATA=DATA)
    p = subprocess.run([sys.executable, os.path.join(DIR, "ludodex", "build_library.py")],
                       cwd=DIR, env=env, capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        sys.exit("build %d failed (rc=%d):\n%s" % (n, p.returncode, p.stderr[-2000:]))
    return p


def rows():
    con = sqlite3.connect(os.path.join(DATA, "game-library.sqlite"))
    try:
        return {(nk, src, plat): (state, via) for nk, src, plat, state, via in con.execute(
            "SELECT g.norm_key, s.source, s.platform, s.state, s.via_collection "
            "FROM games g JOIN sources s ON s.game_id=g.id")}
    finally:
        con.close()


def main():
    print("a rebuild keeps per-format state")

    # One title owned on a disc, the same title WANTED as a Switch ROM. Exactly the
    # shape the ownership store exists for: a want that coexists with what you own.
    ownership.set_fact(DATA, "sonic mania", "Sonic Mania", "physical", "switch", "have")
    ownership.set_fact(DATA, "sonic mania", "Sonic Mania", "rom", "switch", "want")

    build(1)
    first = rows()
    wants = {k: v for k, v in first.items() if v[0] == "want"}
    check("the first build records the per-format want", len(wants) == 1)
    check("and records the physical copy as owned",
          any(v[0] == "have" for v in first.values()))

    build(2)                       # the carry-over branch only exists on a REBUILD
    second = rows()
    check("the rebuild keeps the same source rows", set(second) == set(first))
    still = {k: v for k, v in second.items() if v[0] == "want"}
    check("the want is still a want after a rebuild", still == wants)
    check("nothing turned into ownership",
          sum(1 for v in second.values() if v[0] == "have")
          == sum(1 for v in first.values() if v[0] == "have"))

    build(3)                       # and it must not drift on the one after that
    check("a second rebuild does not flip it either",
          {k: v for k, v in rows().items() if v[0] == "want"} == wants)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
