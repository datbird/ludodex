#!/usr/bin/env python3
"""A provider that files one record PER SYSTEM needs one identity row per platform.

`ss_resolution` was keyed on `norm_key` ALONE. ScreenScraper keeps a separate record for
every system a game was released on, so one row cannot hold the answer for a game owned
on more than one platform: the Switch entry and the PC entry share whichever record the
search happened to return, and the loser wears the other release's art, year and
metadata. Measured on the live library 2026-08-26: 61 norm_keys span more than one
platform and 57 of them shared a single ScreenScraper record.

IGDB already solved exactly this shape with per-entry identity. This is the same answer
in the shared layer: `PLATFORM_KEYED` names the providers whose records are per-system,
and their tables are keyed `(norm_key, platform)`. Every other provider files one record
per GAME, keeps one row, and is completely unaffected — its platform component is the
empty string.

The rule that makes this safe: a per-system provider must be resolved ONE PLATFORM AT A
TIME. Handing `resolve` several platforms at once is the original bug written down, so
it raises rather than picking one.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-platkey-")

import provider_ids                              # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    con = sqlite3.connect(":memory:")
    provider_ids.ensure_tables(con)

    print("1. which providers are per-system")
    check("screenscraper files one record per system",
          provider_ids.is_platform_keyed("screenscraper"))
    check("igdb files one record per game",
          not provider_ids.is_platform_keyed("igdb"))
    check("steamgriddb files one record per game",
          not provider_ids.is_platform_keyed("steamgriddb"))

    print("2. one game, two platforms, two different records")
    provider_ids.record(con, "screenscraper", "celeste", 195244, "Celeste",
                        "search", year=2018, system=225, platform="switch")
    provider_ids.record(con, "screenscraper", "celeste", 307016, "Celeste",
                        "search", year=2018, system=138, platform="pc")
    check("the switch row holds the switch record",
          provider_ids.cached(con, "screenscraper", "celeste",
                              platform="switch")[0] == 195244)
    check("the pc row holds the pc record",
          provider_ids.cached(con, "screenscraper", "celeste",
                              platform="pc")[0] == 307016)
    check("a platform with no row is unrecorded, not somebody else's answer",
          provider_ids.cached(con, "screenscraper", "celeste",
                              platform="ps2") is None)
    check("both platforms are listed",
          sorted(provider_ids.platforms_for(con, "screenscraper", "celeste"))
          == ["pc", "switch"])

    print("3. a per-game provider is untouched")
    provider_ids.record(con, "igdb", "celeste", 26226, "Celeste", "search")
    check("igdb takes no platform and answers without one",
          provider_ids.cached(con, "igdb", "celeste")[0] == 26226)
    check("asking igdb for a platform still answers, because it has only one row",
          provider_ids.cached(con, "igdb", "celeste", platform="switch")[0] == 26226)

    print("4. a per-system provider is resolved one platform at a time")
    def never(_t, _s):
        raise AssertionError("the search must not run")

    raised = False
    try:
        provider_ids.resolve(con, "screenscraper", "celeste", "Celeste",
                             ["pc", "switch"], never)
    except ValueError as e:
        raised = "one platform at a time" in str(e)
    check("resolving a per-system provider for several platforms at once raises",
          raised)

    print("5. the platform comes from the single system handed to resolve")
    seen = []

    def one(title, systems):
        seen.append((title, tuple(systems)))
        return {"ss_id": 999, "name": "Hollow Knight", "system": 225}

    got = provider_ids.resolve(con, "screenscraper", "hollow knight",
                               "Hollow Knight", ["switch"], one)
    check("the search ran for that platform alone", seen == [("Hollow Knight",
                                                              ("switch",))])
    check("the id comes back", got == 999)
    check("it landed on the switch row",
          provider_ids.cached(con, "screenscraper", "hollow knight",
                              platform="switch")[0] == 999)
    check("the pc row is still unrecorded",
          provider_ids.cached(con, "screenscraper", "hollow knight",
                              platform="pc") is None)

    print("6. the collision guard still holds ACROSS platforms")
    # One ScreenScraper record is one release of one game. Two different titles reaching
    # it means one of them is wrong no matter which platforms they were searched for, so
    # the guard is not scoped to a platform. The same game holding it on two platforms
    # is not a collision, because the guard excludes our own norm_key.
    provider_ids.record(con, "screenscraper", "ninja gaiden sigma 2", 25266,
                        "Ninja Gaiden Sigma 2", "search", platform="ps3")
    got = provider_ids.record(con, "screenscraper", "ninja gaiden 2 black", 25266,
                              "Ninja Gaiden II Black", "search", platform="xbox360")
    check("a searched id another title already holds is refused", got == 0)
    check("the refusal is recorded as a collision, on its own platform row",
          provider_ids.cached(con, "screenscraper", "ninja gaiden 2 black",
                              platform="xbox360")[1] == "collision")

    print("7. unlinked lists the work per (game, platform)")
    todo = provider_ids.unlinked(con, "screenscraper", ["celeste", "hollow knight"],
                                 platforms={"celeste": ["pc", "switch"],
                                            "hollow knight": ["pc", "switch"]})
    check("only the platform with no row is outstanding",
          sorted(todo) == [("hollow knight", "pc")])
    check("a per-game provider still returns bare keys",
          provider_ids.unlinked(con, "igdb", ["celeste", "hollow knight"])
          == ["hollow knight"])

    print("8. an old single-key table migrates without losing a decision")
    old = sqlite3.connect(":memory:")
    old.execute("CREATE TABLE ss_resolution(norm_key TEXT PRIMARY KEY, ss_id INTEGER, "
                "name TEXT, matched_by TEXT, resolved_at INTEGER, year INTEGER, "
                "system TEXT)")
    old.executemany(
        "INSERT INTO ss_resolution VALUES(?,?,?,?,?,?,?)",
        [("sonic", 1234, "Sonic", "search", 1, 1991, "1"),
         ("shinobi", 4321, "Shinobi", "manual", 1, 1993, None)])
    old.commit()
    provider_ids.ensure_tables(old)
    cols = [r[1] for r in old.execute("PRAGMA table_info(ss_resolution)")]
    check("the migrated table carries a platform column", "platform" in cols)
    pk = [r[1] for r in old.execute("PRAGMA table_info(ss_resolution)") if r[5]]
    check("the primary key is (norm_key, platform)", sorted(pk) == ["norm_key",
                                                                    "platform"])
    check("no row was lost",
          old.execute("SELECT COUNT(*) FROM ss_resolution").fetchone()[0] == 2)
    # A carried row has no platform yet. It is LEGACY, not an answer: a platform-keyed
    # read must not serve it, or the migration would preserve the very bug it fixes.
    check("a carried row does not answer for a platform",
          provider_ids.cached(old, "screenscraper", "sonic",
                              platform="genesis") is None)
    check("but it is still there to be placed",
          provider_ids.legacy_rows(old, "screenscraper")[0][0] == "shinobi"
          or provider_ids.legacy_rows(old, "screenscraper")[0][0] == "sonic")

    print("9. migration is idempotent")
    provider_ids.ensure_tables(old)
    check("running it again changes nothing",
          old.execute("SELECT COUNT(*) FROM ss_resolution").fetchone()[0] == 2)

    print("10. a carried row is placed on the platform it describes, or dropped")
    # `systeme_id` as ScreenScraper really behaves: PC has NO system id and must stay
    # that way, because "we have no system for this" is not "this does not fit".
    SYS = {"genesis": 1, "switch": 225, "gb": 9}

    def sysid(platform):
        return SYS.get((platform or "").strip().lower())

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE ss_resolution(norm_key TEXT PRIMARY KEY, ss_id INTEGER, "
               "name TEXT, matched_by TEXT, resolved_at INTEGER, year INTEGER, "
               "system TEXT)")
    db.executemany("INSERT INTO ss_resolution VALUES(?,?,?,?,?,?,?)", [
        ("sonic", 100, "Sonic", "search", 1, 1991, "1"),        # system says genesis
        ("shinobi", 200, "Shinobi", "search", 1, 1993, "1"),    # genesis record, gb game
        ("celeste", 300, "Celeste", "search", 1, 2018, None),   # no system, 2 platforms
        ("braid", 400, "Braid", "search", 1, 2008, None),       # no system, 1 platform
        ("stray", 500, "Stray", "search", 1, 2022, "225"),      # switch rec, pc+switch
    ])
    db.commit()
    provider_ids.ensure_tables(db)
    owned = {"sonic": ["genesis"], "shinobi": ["gb"], "celeste": ["pc", "switch"],
             "braid": ["pc"], "stray": ["pc", "switch"]}
    rep = provider_ids.place_legacy(db, "screenscraper", owned, sysid, apply=True)
    placed = {nk: p for nk, p, _w in rep["placed"]}
    dropped = {nk for nk, _w in rep["dropped"]}

    check("a row whose system IS the owned platform is placed there",
          placed.get("sonic") == "genesis")
    check("a row with no system on a single-platform game goes to that platform",
          placed.get("braid") == "pc")
    check("a row whose system fits ONE of several owned platforms is placed there",
          placed.get("stray") == "switch")
    check("a row whose system is a platform the game is NOT owned on is dropped",
          "shinobi" in dropped)
    check("an unattributable row on a multi-platform game is dropped",
          "celeste" in dropped)

    # A DROP IS AN ABSENCE, NOT A MISS. Writing a miss would make the next sweep skip
    # the game for MISS_TTL, remembering a verdict nothing ever reached.
    check("a dropped row leaves nothing behind for gb",
          provider_ids.cached(db, "screenscraper", "shinobi", platform="gb") is None)
    check("a dropped row leaves nothing behind for either celeste platform",
          provider_ids.cached(db, "screenscraper", "celeste", platform="pc") is None
          and provider_ids.cached(db, "screenscraper", "celeste",
                                  platform="switch") is None)
    check("the placed rows answer for their own platform",
          provider_ids.cached(db, "screenscraper", "sonic",
                              platform="genesis")[0] == 100)
    check("stray's pc platform is now outstanding, not the switch record",
          provider_ids.cached(db, "screenscraper", "stray", platform="pc") is None)
    check("no legacy row survives", provider_ids.legacy_rows(db, "screenscraper") == [])
    check("placing again is a no-op",
          provider_ids.place_legacy(db, "screenscraper", owned, sysid, apply=True)
          == {"placed": [], "dropped": []})

    print("11. a placed row is never overwritten by a carried one")
    db2 = sqlite3.connect(":memory:")
    provider_ids.ensure_tables(db2)
    provider_ids.record(db2, "screenscraper", "sonic", 999, "Sonic", "search",
                        system=1, platform="genesis")
    db2.execute("INSERT INTO ss_resolution(norm_key,platform,ss_id,matched_by,"
                "resolved_at,system) VALUES('sonic','',100,'search',1,'1')")
    db2.commit()
    rep2 = provider_ids.place_legacy(db2, "screenscraper", {"sonic": ["genesis"]},
                                     sysid, apply=True)
    check("the carried row is dropped, not promoted over the placed one",
          [nk for nk, _w in rep2["dropped"]] == ["sonic"])
    check("the per-platform search's answer survives",
          provider_ids.cached(db2, "screenscraper", "sonic",
                              platform="genesis")[0] == 999)

    print("12. a rescore refusal clears ONE platform, not the whole game")
    # `rescore` deleted by norm_key alone. On a platform-keyed table that takes every
    # platform's identity with it, including the ones today's gate still accepts — a
    # refusal about ONE match silently destroying matches nobody re-judged.
    db3 = sqlite3.connect(":memory:")
    provider_ids.ensure_tables(db3)
    provider_ids.record(db3, "screenscraper", "fortnite", 219252,
                        "Fortnite (Standard Founder's Pack)", "search",
                        year=2017, platform="switch2")
    provider_ids.record(db3, "screenscraper", "fortnite", 500000, "Fortnite", "search",
                        year=2020, platform="pc")
    # The gate judges the record's year against the GAME's era, and `game_era` takes that
    # from IGDB's own first_release_date. Live, Fortnite reads 2020 and the ScreenScraper
    # record it held was the 2017 Save the World founder's pack — a different product.
    db3.execute("CREATE TABLE IF NOT EXISTS igdb_meta(igdb_id INTEGER PRIMARY KEY, "
                "payload_json TEXT)")
    db3.execute("INSERT INTO igdb_meta VALUES(1,?)",
                ('{"name": "Fortnite", "first_release_date": 1593388800}',))
    provider_ids.record(db3, "igdb", "fortnite", 1, "Fortnite", "search")
    lib3 = sqlite3.connect(":memory:")
    lib3.execute("CREATE TABLE games(norm_key TEXT, canonical_title TEXT, "
                 "platform TEXT)")
    lib3.executemany("INSERT INTO games VALUES(?,?,?)",
                     [("fortnite", "Fortnite", "switch2"),
                      ("fortnite", "Fortnite", "pc")])
    lib3.commit()
    rep3 = provider_ids.rescore(db3, lib3, apply=True)
    check("the wrong-era record is refused",
          any("switch2" in str(r[1]) for r in rep3["refused"]))
    check("its row is gone",
          provider_ids.cached(db3, "screenscraper", "fortnite",
                              platform="switch2") is None)
    check("the OTHER platform's identity is untouched",
          (provider_ids.cached(db3, "screenscraper", "fortnite",
                               platform="pc") or [0])[0] == 500000)

    print("13. index_answer carries the key's platform, and resolve honours it")
    import inspect
    src = inspect.getsource(provider_ids.index_answer)
    check("the answer is a 5-tuple ending in the key's platform",
          "_key_platform(con, ns, vals[0])" in src)
    rsrc = inspect.getsource(provider_ids.resolve)
    check("a per-system provider refuses an index key for another platform",
          "is_platform_keyed(provider) and ix_plat" in rsrc)
    check("and records the platform the key states, not None",
          "system=ix_plat" in rsrc)
    check("a key that states nothing is still taken, because NULL is UNKNOWN",
          "not (is_platform_keyed(provider) and ix_plat" in rsrc)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
