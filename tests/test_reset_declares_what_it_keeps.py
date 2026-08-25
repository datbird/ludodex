#!/usr/bin/env python3
"""Every database a reset leaves behind has to be a DECLARED decision.

`reset.py` sorts the data directory into four named lists: IMPORT_DBS (regenerable),
CURATION_DBS (what the user decided), CONFIG_DBS (how to reach the outside world) and
KEEP_ALWAYS (the way back in). `plan()["kept"]` renders KEEP_ALWAYS into the confirmation
dialog, which is the user's only account of what survives.

Seven provider mirrors appeared in none of the four:

    igdb-catalog  match-index  moby-catalog  mobygames-state
    ss-catalog    tgdb-catalog thegamesdb-state

So a `factory` reset silently left them, up to about 1.7 GB, and the dialog did not
mention them. Keeping them is almost certainly RIGHT: they are bought with provider
quota, not with local CPU. ScreenScraper allows 100,000 requests a day and TheGamesDB
12,000 a MONTH, so a mirror thrown away on a whim is a mirror that takes weeks to come
back, and `backups.py` already classes three of them as DERIVED for that reason.

The defect was never the outcome. It was that the outcome was an accident of omission
rather than a decision anyone wrote down, and that the dialog reporting what survives
did not report them. A user who resets to factory and finds 1.7 GB still on disk has
been told something untrue.

Offline. No network. Builds its own fixture data dir; never touches a real one.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-reset-declared-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import reset                                                   # noqa: E402

PASS = []
MIRRORS = ["igdb-catalog.sqlite", "match-index.sqlite", "moby-catalog.sqlite",
           "mobygames-state.sqlite", "ss-catalog.sqlite", "tgdb-catalog.sqlite",
           "thegamesdb-state.sqlite"]


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def seed():
    """A data dir holding one of everything the four lists name, plus the mirrors."""
    names = (list(reset.IMPORT_DBS) + list(reset.CURATION_DBS) + list(reset.CONFIG_DBS)
             + sorted(reset.KEEP_ALWAYS) + MIRRORS)
    for n in names:
        p = os.path.join(DATA, n)
        if not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(b"")
    return names


def main():
    print("a reset declares everything it keeps")
    seed()

    # ---- no database is unaccounted for -------------------------------------- #
    declared = (set(reset.IMPORT_DBS) | set(reset.CURATION_DBS)
                | set(reset.CONFIG_DBS) | set(reset.KEEP_ALWAYS))
    on_disk = {f for f in os.listdir(DATA) if f.endswith(".sqlite")
               and not f.startswith("roms-index")}
    check("every database in the data dir is named by some list",
          not (on_disk - declared))

    # ---- the mirrors are kept, deliberately ---------------------------------- #
    for m in MIRRORS:
        check("%s is declared, not merely overlooked" % m, m in declared)
    check("and they are kept rather than deleted",
          all(m not in reset._dbs_for("factory") for m in MIRRORS))

    # ---- the dialog says so --------------------------------------------------- #
    p = reset.plan("factory")
    kept = set(p.get("kept") or [])
    check("the plan reports what survives a factory reset",
          all(m in kept for m in MIRRORS))
    check("and still reports the way back in", "auth.sqlite" in kept)
    check("while the plan's delete list does not name them",
          not any(m in (p.get("databases") or []) for m in MIRRORS))

    # ---- the scopes stay strict supersets ------------------------------------ #
    imp, cur, fac = (set(reset._dbs_for(s)) for s in ("import", "curation", "factory"))
    check("curation still deletes everything import does", imp <= cur)
    check("and factory everything curation does", cur <= fac)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
