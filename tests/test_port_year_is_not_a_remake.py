#!/usr/bin/env python3
"""A port's release date is not evidence of a wrong record.

ScreenScraper files one record PER SYSTEM, and each carries THAT SYSTEM'S release date.
The Switch record for a 2019 PC game is dated when the Switch port shipped. The
acceptance gate compared that date to the game's era symmetrically, so a correct record
looked exactly like a remake wearing its original's art.

Measured on the live library 2026-08-26, the identity scrub would have refused these,
every one an identical title on its own system's record:

    APE OUT          <- Ape Out            (era 2019, Switch record 2021)
    Bayonetta 2      <- Bayonetta 2        (era 2014, Switch record 2018)
    Overwatch        <- Overwatch          (era 2016, Switch record 2019)
    Fallout Shelter  <- Fallout Shelter    (era 2015, Switch record 2018)
    Titan Quest      <- Titan Quest

Ten-odd correct matches, deleted by a rule meant to catch remakes.

THE RULE, AND THE HALF THAT MUST NOT MOVE. When the candidate is a record for the very
system we are matching for, a LATER year is that system's release date and disqualifies
nothing. An EARLIER year still does — that is the case this check was built for:
Resident Evil 4 (2023) taking ScreenScraper 4750, the 2005 GameCube game, and wearing its
box. A system agreeing does not excuse a record that PREDATES the game.

So the exemption is one-directional and is only ever granted by a caller that knows the
candidate's system fits. Everything else is judged exactly as before.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-portyear-")

import matchgate                                 # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    print("1. without the exemption, a port date still refuses (the old behaviour)")
    check("a 2021 Switch record for a 2019 game is refused by default",
          not matchgate.score(["Ape Out"], "APE OUT", 2019, 2021)[0])
    check("so is a 2018 record for a 2014 game",
          not matchgate.score(["Bayonetta 2"], "Bayonetta 2", 2014, 2018)[0])

    print("2. with it, a LATER record on the system we asked about is accepted")
    check("the Switch port's own date does not refuse it",
          matchgate.score(["Ape Out"], "APE OUT", 2019, 2021, later_ok=True)[0])
    check("Bayonetta 2 on Switch is accepted",
          matchgate.score(["Bayonetta 2"], "Bayonetta 2", 2014, 2018,
                          later_ok=True)[0])
    check("Overwatch on Switch is accepted",
          matchgate.score(["Overwatch"], "Overwatch", 2016, 2019, later_ok=True)[0])

    print("3. an EARLIER record is still refused, exemption or not")
    # The case the era check exists for. A remake shares its original's title exactly,
    # so nothing but the year separates them, and the original always comes FIRST.
    check("Resident Evil 4 (2023) is still refused the 2005 record",
          not matchgate.score(["Resident Evil 4"], "Resident Evil 4", 2023, 2005,
                              later_ok=True)[0])
    check("Fortnite (2020) is still refused a 2017 record",
          not matchgate.score(["Fortnite"], "Fortnite", 2020, 2017, later_ok=True)[0])

    print("4. the exemption never rescues a name that does not match")
    # It is an ERA exemption and nothing else. Coverage still governs.
    check("a different game is refused however the years line up",
          not matchgate.score(["Half-Life: Opposing Force"], "Half-Life", 1999, 2005,
                              later_ok=True)[0])
    check("a sequel is still not its original",
          not matchgate.score(["Boltgun"], "Boltgun 2", 2023, 2025, later_ok=True)[0])

    print("5. an absent year is not evidence, in either direction")
    check("no game year, no refusal",
          matchgate.score(["Ape Out"], "APE OUT", None, 2021)[0])
    check("no candidate year, no refusal",
          matchgate.score(["Ape Out"], "APE OUT", 2019, None)[0])

    print("6. the ScreenScraper caller grants it only on a STATED, FITTING system")
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "server", "app.py"), encoding="utf-8").read()
    check("_ss_match asks whether the candidate's own system fits",
          "_cand_system_fits" in src)
    check("and passes that as the era exemption, never a constant",
          "later_ok=_same_system" in src)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
