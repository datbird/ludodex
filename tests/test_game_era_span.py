#!/usr/bin/env python3
"""A game's era is the SPAN of its releases, not IGDB's single `first_release_date`.

`game_era` returned exactly one number: the year IGDB puts in `first_release_date`. That
field names ONE release, and IGDB does not always pick the earliest one. Golf With Your
Friends reads 2020 there while the same record's `release_dates` list its 2016 early-access
launch on PC, Mac and Linux — so ScreenScraper's correct 2016 record was reported as an era
disagreement by invariant I10, and the acceptance gate would have refused it on a re-match.

The record already carried the answer. `release_dates.y` has been in `GAME_FIELDS` all
along; nothing read it. So the fix is to read what IGDB already said rather than to weaken
the rule.

WHY A SPAN AND NOT A MINIMUM. Taking only the earliest year would make every later port
look wrong: Syberia 2 spans 2004 to 2023, and its 2017 record would be judged against 2004.
The honest statement is the set of years IGDB records for that game, and a candidate is in
era when it falls inside that set's range.

THE SAFETY PROPERTY, MEASURED. On the live library `first_release_date` falls outside its
own `release_dates` span in 0 of 2,364 payloads, so the span can only ever WIDEN what the
scalar accepted. Replaying all 1,396 judged provider rows: 40 newly pass, 0 newly fail.

WHAT A SPAN CANNOT FIX, AND WHY IT MUST NOT TRY. Akalabeth: World of Doom is a 1979 Apple II
game. IGDB's record spans 1998 to 2014 and has no 1979 date at all, so ScreenScraper's
correct 1979 record stays refused. The tempting fix — let ScreenScraper's own year lower the
era — is circular, and it is exactly what would let Resident Evil 4 (2023) validate its wrong
2005 GameCube record. A provider's year can never be the evidence that its own year is right.

So the floor is lowered only by a PERSON, through the `release_year` attribute override that
already exists. `set_by='user'` is the whole test: an override the wand's consensus pass
wrote automatically is just another provider's guess wearing a different hat, and it is
refused for the same reason. A user's statement widens the span downward and can never
narrow it, so adjudicating one game can never start refusing another.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-erasspan-")

import matchgate                                 # noqa: E402
import overrides                                 # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _ts(year):
    """A UTC epoch inside `year`, the shape IGDB uses for first_release_date."""
    import calendar
    import datetime
    return calendar.timegm(datetime.datetime(year, 7, 1).timetuple())


def cache_with(rows):
    """`rows` = {norm_key: (igdb_id, first_release_year, [release_years])}."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE igdb_resolution(norm_key TEXT PRIMARY KEY, "
                "igdb_id INTEGER, name TEXT, matched_by TEXT, year INTEGER)")
    con.execute("CREATE TABLE igdb_meta(igdb_id INTEGER PRIMARY KEY, "
                "payload_json TEXT, fetched_at INTEGER)")
    for nk, (gid, frd, years) in rows.items():
        con.execute("INSERT INTO igdb_resolution(norm_key,igdb_id) VALUES(?,?)",
                    (nk, gid))
        payload = {"id": gid, "name": nk}
        if frd:
            payload["first_release_date"] = _ts(frd)
        if years is not None:
            payload["release_dates"] = [{"y": y} for y in years]
        con.execute("INSERT INTO igdb_meta(igdb_id,payload_json) VALUES(?,?)",
                    (gid, json.dumps(payload)))
    con.commit()
    return con


def lib_with(entries):
    """`entries` = {norm_key: (has_store_source, release_year_attr)}."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT)")
    con.execute("CREATE TABLE sources(game_id INTEGER, source TEXT)")
    con.execute("CREATE TABLE game_attributes(game_id INTEGER, kind TEXT, value TEXT)")
    for i, (nk, (store, yr)) in enumerate(entries.items(), start=1):
        con.execute("INSERT INTO games(id,norm_key) VALUES(?,?)", (i, nk))
        con.execute("INSERT INTO sources(game_id,source) VALUES(?,?)",
                    (i, "steam" if store else "emulation"))
        if yr:
            con.execute("INSERT INTO game_attributes(game_id,kind,value) "
                        "VALUES(?,?,?)", (i, "release_year", str(yr)))
    con.commit()
    return con


def main():
    # ------------------------------------------------------------------ the span itself
    print("1. game_era reads the years IGDB actually recorded")
    mc = cache_with({
        "golf with your friends": (24985, 2020, [2020, 2020, 2022, 2016, 2020,
                                                 2020, 2016, 2020, 2020, 2016]),
        "akalabeth world of doom": (131719, 1998, [1998, 2010, 2014]),
        "resident evil 4":        (106987, 2023, [2023]),
        "no dates at all":        (1, 2001, None),
        "no igdb record":         (0, None, None),
    })
    lc = lib_with({
        "golf with your friends": (True, None),
        "akalabeth world of doom": (True, None),
        "resident evil 4": (True, None),
        "no dates at all": (True, None),
        "no igdb record": (True, None),
    })

    check("Golf's span starts at its 2016 early access, not its 2020 first_release_date",
          matchgate.game_era(lc, mc, "golf with your friends") == (2016, 2020, 2022))
    check("Akalabeth reports only what IGDB knows — no 1979 anywhere",
          matchgate.game_era(lc, mc, "akalabeth world of doom") == (1998, 2010, 2014))
    check("a record with no release_dates falls back to first_release_date",
          matchgate.game_era(lc, mc, "no dates at all") == (2001,))
    check("distinct years only, and sorted",
          matchgate.game_era(lc, mc, "golf with your friends")
          == tuple(sorted(set(matchgate.game_era(lc, mc, "golf with your friends")))))

    print("2. no statement is still no statement")
    # The rule 123 live false refusals bought: an absent year is not evidence, and must
    # never refuse anything. A store entry's release_year is a LISTING date, so it is
    # deliberately not read as the game's era.
    check("a store entry with no IGDB record states nothing",
          matchgate.game_era(lc, mc, "no igdb record") is None)
    check("an unknown game states nothing", matchgate.game_era(lc, mc, "nobody") is None)
    rom = lib_with({"some rom": (False, 1992)})
    check("a ROM-only entry's release_year IS its era",
          matchgate.game_era(rom, cache_with({}), "some rom") == (1992,))
    store = lib_with({"some store game": (True, 2016)})
    check("a store entry's release_year is a listing date and is not read",
          matchgate.game_era(store, cache_with({}), "some store game") is None)

    # ------------------------------------------------------------------- the gate reads it
    print("3. the gate accepts a candidate inside the span")
    golf = matchgate.game_era(lc, mc, "golf with your friends")
    check("ScreenScraper's 2016 record is accepted — the live I10 case",
          matchgate.score(["Golf With Your Friends"], "Golf With Your Friends",
                          golf, 2016)[0])
    check("the 2020 console release is accepted too",
          matchgate.score(["Golf With Your Friends"], "Golf With Your Friends",
                          golf, 2020)[0])
    check("so is the 2022 Stadia one, at the top of the span",
          matchgate.score(["Golf With Your Friends"], "Golf With Your Friends",
                          golf, 2022)[0])
    check("tolerance still applies at each end",
          matchgate.score(["Golf With Your Friends"], "Golf With Your Friends",
                          golf, 2015)[0])
    check("but a record well outside the span is still refused",
          not matchgate.score(["Golf With Your Friends"], "Golf With Your Friends",
                              golf, 2009)[0])

    print("4. THE HALF THAT MUST NOT MOVE — a remake still cannot wear its original")
    re4 = matchgate.game_era(lc, mc, "resident evil 4")
    check("Resident Evil 4 (2023) is still refused the 2005 GameCube record",
          not matchgate.score(["Resident Evil 4"], "Resident Evil 4", re4, 2005)[0])
    check("and the port exemption does not rescue it either",
          not matchgate.score(["Resident Evil 4"], "Resident Evil 4", re4, 2005,
                              later_ok=True)[0])
    check("Akalabeth's 1979 record is refused until a PERSON says otherwise",
          not matchgate.score(["Akalabeth: World of Doom"], "Akalabeth: World of Doom",
                              matchgate.game_era(lc, mc, "akalabeth world of doom"),
                              1979)[0])

    print("5. a scalar year still works, unchanged")
    # Every existing caller and test passes an int. The span is an addition, not a
    # replacement, and the old shape has to keep behaving exactly as it did.
    check("an int era accepts its own year",
          matchgate.score(["Ape Out"], "APE OUT", 2019, 2019)[0])
    check("an int era refuses a 2021 record",
          not matchgate.score(["Ape Out"], "APE OUT", 2019, 2021)[0])
    check("unless the port exemption is granted",
          matchgate.score(["Ape Out"], "APE OUT", 2019, 2021, later_ok=True)[0])
    check("no era refuses nothing",
          matchgate.score(["Ape Out"], "APE OUT", None, 2021)[0])
    check("no candidate year refuses nothing",
          matchgate.score(["Ape Out"], "APE OUT", (2019, 2020), None)[0])

    print("6. the exemption measures against the TOP of the span, not the bottom")
    # A later record is a port date. The span already contains the ports IGDB knows
    # about, so the exemption only has to cover the ones it does not.
    check("a 2025 record for a game spanning 2016-2022 is refused by default",
          not matchgate.score(["Golf With Your Friends"], "Golf With Your Friends",
                              golf, 2025)[0])
    check("and accepted when the candidate's own system is the one we asked for",
          matchgate.score(["Golf With Your Friends"], "Golf With Your Friends",
                          golf, 2025, later_ok=True)[0])
    check("an EARLIER record is never rescued by it",
          not matchgate.score(["Golf With Your Friends"], "Golf With Your Friends",
                              golf, 1999, later_ok=True)[0])

    # --------------------------------------------------------------- the manual override
    print("7. a PERSON can lower the floor; a machine cannot")
    ak = "akalabeth world of doom"
    overrides.set_override(ak, "release_year", "1979", origin="manual", by="user")
    check("the user's 1979 joins the span",
          matchgate.game_era(lc, mc, ak) == (1979, 1998, 2010, 2014))
    check("and ScreenScraper's 1979 record is now accepted",
          matchgate.score(["Akalabeth: World of Doom"], "Akalabeth: World of Doom",
                          matchgate.game_era(lc, mc, ak), 1979)[0])
    check("while IGDB's own 1998 record still is too — widening refuses nothing",
          matchgate.score(["Akalabeth: World of Doom"], "Akalabeth: World of Doom",
                          matchgate.game_era(lc, mc, ak), 1998)[0])

    overrides.clear_override(ak, "release_year")
    check("clearing it puts the span back",
          matchgate.game_era(lc, mc, ak) == (1998, 2010, 2014))

    print("8. an automatic override is just another provider's guess")
    # The circularity this whole design exists to refuse. If the wand's consensus pass
    # could lower the floor, a wrong provider year would license itself.
    overrides.set_override("resident evil 4", "release_year", "2005",
                           origin="screenscraper", by="auto")
    check("an auto-written release_year does not move the span",
          matchgate.game_era(lc, mc, "resident evil 4") == (2023,))
    check("so the 2005 record is still refused",
          not matchgate.score(["Resident Evil 4"], "Resident Evil 4",
                              matchgate.game_era(lc, mc, "resident evil 4"), 2005)[0])

    print("9. an override can only WIDEN")
    # A narrowing override would start refusing records that pass today, which is how a
    # single adjudication turns into a library-wide regression.
    overrides.set_override("golf with your friends", "release_year", "2020",
                           origin="manual", by="user")
    check("a floor inside the span leaves it alone",
          matchgate.game_era(lc, mc, "golf with your friends") == (2016, 2020, 2022))
    overrides.set_override("golf with your friends", "release_year", "2030",
                           origin="manual", by="user")
    check("a later year cannot raise the floor or drop the 2016 record",
          matchgate.score(["Golf With Your Friends"], "Golf With Your Friends",
                          matchgate.game_era(lc, mc, "golf with your friends"), 2016)[0])

    print("10. a user override alone is a statement, when nothing else is")
    bare = lib_with({"undated store game": (True, None)})
    check("with no override it states nothing",
          matchgate.game_era(bare, cache_with({}), "undated store game") is None)
    overrides.set_override("undated store game", "release_year", "1994",
                           origin="manual", by="user")
    check("with one it is the era",
          matchgate.game_era(bare, cache_with({}), "undated store game") == (1994,))

    print("11. a junk override is ignored rather than trusted")
    overrides.set_override("no dates at all", "release_year", "not a year",
                           origin="manual", by="user")
    check("an unparseable year leaves the span alone",
          matchgate.game_era(lc, mc, "no dates at all") == (2001,))

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
