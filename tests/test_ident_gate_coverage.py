#!/usr/bin/env python3
"""`matchgate` is THE acceptance gate. Four live identity paths went round it.

  (a) EXACT MUST MEAN PUBLISHED. matchindex's own header defines `exact` as "the source
      PUBLISHES this pairing ... it needs no acceptance gate and cannot be a wrong bind",
      and `derived` as "WE concluded it, by matching a name and a year through matchgate".
      `_merge_ss`, `_merge_tgdb_catalog` and `_merge_moby` all CONCLUDE an identity with
      `matchgate.score` and then stamped the resulting key `exact`. Downstream,
      `provider_ids.index_lookup` states that an index answer "cannot be a wrong bind
      because the pairing was published, not concluded", `record(..., "index")` is exempt
      from the collision guard, and `NAME_DERIVED` excludes it from `rescore()`. So a
      name-derived bind wore a permanent exact badge that nothing could ever re-judge.

  (b) `--era-reheal` OVERWROTE DECISIONS. It excluded only `matched_by != 'manual'` and
      then `INSERT OR REPLACE`d, so it destroyed the AI-decided identities that
      `decided_identities()` protects from a full refresh — each one a PAID judgment made
      precisely because the deterministic search could not settle it. Re-running that
      search and writing 0 when it fails again is the same mistake `decided_identities`
      exists to prevent, reached by a different door.

  (c) HARDWARE IS PART OF THE RULE AND `score()` HAS NO PLATFORM ARGUMENT.
      docs/PIPELINE.md:62 lists hardware beside coverage and era. `_merge_ss` skipped the
      platform check entirely whenever ScreenScraper's own `igdb_platform` column was NULL
      — which `backfill_platforms` documents is the case for system 138, "PC Windows", the
      biggest system in the mirror. `_match_tgdb` matched on name plus year alone, so
      Sonic 2 (Game Gear) could bind to Sonic 2 (Genesis) on the name and 1992.

  (d) RETROACHIEVEMENTS HAD NO GATE AT ALL. `ra_by_norm.setdefault(norm(Title), ...)`
      then `INSERT OR REPLACE INTO ra_games` keyed on norm_key alone: RA's `~Hack~` and
      `[Subset - ...]` entries competed for the key, first to normalise won, and a game
      owned on two consoles silently kept the LAST console's id.

Offline. No network — every mirror and list here is a local fixture.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-ident-gate-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import config                                                  # noqa: E402
import matchgate                                               # noqa: E402
import matchindex as M                                         # noqa: E402
import provider_ids                                            # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def mirrors():
    """IGDB + ScreenScraper + TheGamesDB + MobyGames fixtures, built once."""
    ig = sqlite3.connect(M.IGDB_DB)
    ig.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, name TEXT, slug TEXT, norm_key TEXT,
      game_type INTEGER, year INTEGER, first_release_date INTEGER, platforms TEXT,
      parent_game INTEGER, version_parent INTEGER, updated_at INTEGER, seen_at INTEGER);
    CREATE TABLE alt_names(game_id INTEGER, name TEXT, norm_key TEXT);
    CREATE TABLE platforms(id INTEGER PRIMARY KEY, name TEXT, abbreviation TEXT,
      alternative_name TEXT, platform_type TEXT, platform_family TEXT, generation INT);
    CREATE TABLE game_platforms(game_id INTEGER, platform_id INTEGER);
    CREATE TABLE stores(id INTEGER PRIMARY KEY, name TEXT);
    CREATE TABLE external_ids(game_id INTEGER, source_id INTEGER, uid TEXT, name TEXT);
    """)
    ig.executemany("INSERT INTO platforms VALUES(?,?,?,NULL,NULL,NULL,NULL)", [
        (6, "PC (Microsoft Windows)", "PC"),
        (29, "Sega Mega Drive/Genesis", "Genesis"),
        (35, "Sega Game Gear", "Game Gear"),
    ])
    # Sonic 2 is genuinely TWO games with one name and one year: the Genesis game and the
    # 8-bit Game Gear game. Nothing but hardware separates them.
    ig.execute("INSERT INTO games VALUES(100,'Sonic the Hedgehog 2','s2',"
               "'sonic the hedgehog 2',0,1992,NULL,'29',NULL,NULL,0,0)")
    ig.execute("INSERT INTO games VALUES(101,'Sonic the Hedgehog 2','s2gg',"
               "'sonic the hedgehog 2',0,1992,NULL,'35',NULL,NULL,0,0)")
    # A PC-only game, for the "system 138 has no igdb_platform" hole.
    ig.execute("INSERT INTO games VALUES(200,'Deus Ex','deusex','deus ex',0,2000,NULL,"
               "'6',NULL,NULL,0,0)")
    ig.executemany("INSERT INTO game_platforms VALUES(?,?)",
                   [(100, 29), (101, 35), (200, 6)])
    ig.executemany("INSERT INTO stores VALUES(?,?)", [(1, "Steam")])
    ig.execute("INSERT INTO external_ids VALUES(200,1,'6910','Deus Ex')")
    ig.commit(); ig.close()

    ss = sqlite3.connect(M.SS_DB)
    ss.executescript("""
    CREATE TABLE ss_games(id INTEGER PRIMARY KEY, systeme INTEGER, name TEXT,
      norm_key TEXT, year INTEGER, developer TEXT, publisher TEXT, notgame INTEGER,
      n_roms INTEGER, seen_at INTEGER);
    CREATE TABLE ss_names(game_id INTEGER, region TEXT, name TEXT, norm_key TEXT);
    CREATE TABLE ss_roms(game_id INTEGER, crc TEXT, md5 TEXT, sha1 TEXT,
      filename TEXT, size INTEGER, region TEXT);
    CREATE TABLE ss_systems(id INTEGER PRIMARY KEY, name TEXT, names TEXT,
      company TEXT, type TEXT, igdb_platform INTEGER, mapped_by TEXT);
    """)
    ss.executemany("INSERT INTO ss_systems VALUES(?,?,?,?,?,?,?)", [
        (1, "Genesis", '["Genesis"]', "Sega", "console", 29, "name"),
        # THE HOLE. System 138 is "PC Windows" and its igdb_platform was never mapped, so
        # the platform check was skipped for every record on it.
        (138, "Microsoft Windows,Windows 10", '["PC Windows", "Windows"]',
         "Microsoft", "Ordinateur", None, None),
    ])
    ss.execute("INSERT INTO ss_games VALUES(500,1,'Sonic the Hedgehog 2',"
               "'sonic the hedgehog 2',1992,'S','S',0,1,0)")
    # A PC-Windows record whose name collides with a CONSOLE game. Hardware is the only
    # thing that separates them, and it was the one thing not being asked.
    ss.execute("INSERT INTO ss_games VALUES(900,138,'Sonic the Hedgehog 2',"
               "'sonic the hedgehog 2',1992,'S','S',0,1,0)")
    ss.execute("INSERT INTO ss_games VALUES(901,138,'Deus Ex','deus ex',2000,"
               "'I','E',0,1,0)")
    ss.commit(); ss.close()

    tg = sqlite3.connect(M.TGDB_DB)
    tg.executescript("""
    CREATE TABLE tgdb_games(id INTEGER PRIMARY KEY, name TEXT, norm_key TEXT,
      platform INTEGER, region_id INTEGER, country_id INTEGER, release_date TEXT,
      year INTEGER, players INTEGER, coop TEXT, esrb TEXT, genres TEXT,
      developers TEXT, publishers TEXT, youtube TEXT, os TEXT, min_spec TEXT,
      seen_at INTEGER);
    """)
    # TheGamesDB's grain is (title, platform, region) and its platform is an id no local
    # mirror can name — so nothing here can tell which Sonic 2 this row is.
    tg.execute("INSERT INTO tgdb_games VALUES(4444,'Sonic the Hedgehog 2',"
               "'sonic the hedgehog 2',20,NULL,NULL,'1992-01-01',1992,1,NULL,NULL,"
               "NULL,NULL,NULL,NULL,NULL,NULL,0)")
    tg.execute("INSERT INTO tgdb_games VALUES(4445,'Deus Ex','deus ex',1,NULL,NULL,"
               "'2000-01-01',2000,1,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,0)")
    tg.commit(); tg.close()


def main():
    print("one gate, and the paths that went round it")
    # Every layer that would DOWNLOAD is off: this test is about the acceptance rules, and
    # a suite that reaches the network is not offline no matter what it asserts.
    config.set_("matchindex_tgdb_freemap", "0")
    config.set_("matchindex_wikidata_ids", "0")
    config.set_("matchindex_libretro_dats", "0")
    mirrors()
    M.build(progress=False)
    con = M.connect()

    print()
    print("(a) `exact` means the source PUBLISHED the pairing")
    kinds = {(r["ns"], r["val"]): r["kind"] for r in con.execute(
        "SELECT ns, val, kind FROM ix.identity_key")}
    check("a store id IGDB published is exact",
          kinds.get(("steam", "6910")) == "exact")
    check("an igdb id on its own identity is exact",
          kinds.get(("igdb", "200")) == "exact")
    check("a name is derived", kinds.get(("name", "deus ex")) == "derived")
    check("an SS id MERGED onto an igdb identity by matchgate is DERIVED: %r"
          % kinds.get(("ss", "500")), kinds.get(("ss", "500")) == "derived")
    check("an SS id on its OWN identity is exact: %r" % kinds.get(("ss", "900")),
          kinds.get(("ss", "900")) == "exact")
    check("a TheGamesDB id merged by name is derived too: %r"
          % kinds.get(("thegamesdb", "4445")),
          kinds.get(("thegamesdb", "4445")) == "derived")
    check("matchindex can be ASKED the kind of a key",
          M.key_kind(con, "ss", "500") == "derived")
    con.close()

    print()
    print("(a2) a derived index answer re-enters the collision guard and rescore()")
    cc = sqlite3.connect(os.path.join(DATA, "metadata-cache.sqlite"))
    cc.row_factory = sqlite3.Row
    provider_ids.ensure_tables(cc)
    # 'deus ex' resolves its ScreenScraper id 901 through an EXACT anchor (the steam id)
    # but the ss key itself was CONCLUDED, so the identity it produces is name-derived.
    pid = provider_ids.resolve(cc, "screenscraper", "deus ex", "Deus Ex", ["pc"],
                               lambda _t, _s: None, anchors={"steam": "6910"})
    check("the index still answers: %r" % pid, str(pid) == "901")
    how = provider_ids.cached(cc, "screenscraper", "deus ex")[1]
    check("and it is recorded as name-derived, not 'index': %r" % how,
          how in provider_ids.NAME_DERIVED)
    # Which is the whole point: a second game arriving at the same id is now refused.
    provider_ids.record(cc, "screenscraper", "other game", 901, name="Deus Ex",
                        matched_by="search")
    check("a second game cannot take the same id",
          provider_ids.cached(cc, "screenscraper", "other game")[1] == "collision")
    cc.close()

    print()
    print("(b) --era-reheal never re-decides a DECIDED identity")
    import igdb_enrich
    ec = sqlite3.connect(os.path.join(DATA, "cache-b.sqlite"))
    ec.execute("CREATE TABLE igdb_resolution(norm_key TEXT PRIMARY KEY, igdb_id INTEGER,"
               " slug TEXT, matched_by TEXT, resolved_at INTEGER)")
    ec.executemany("INSERT INTO igdb_resolution VALUES(?,?,NULL,?,0)", [
        ("plain", 11, "search"),      # a derivation — re-decidable
        ("pinned", 12, "manual"),     # a person decided
        ("ai picked", 13, "ai_name"),  # a PAID judgment the search already failed at
    ])
    ec.commit()
    cand = dict(igdb_enrich.reheal_candidates(ec))
    check("a plain search result is a candidate", "plain" in cand)
    check("a hand pin is not", "pinned" not in cand)
    check("and an AI-decided identity is not: %r" % sorted(cand),
          "ai picked" not in cand)
    check("the two protections agree on what a decision is",
          igdb_enrich.decided_identities(ec) == {"pinned", "ai picked"})
    ec.close()

    print()
    print("(c) hardware is part of the shared gate")
    check("matchgate states the rule", hasattr(matchgate, "hardware_ok"))
    check("hardware that disagrees is refused",
          not matchgate.hardware_ok({"genesis"}, {"gamegear"}))
    check("hardware that agrees is accepted",
          matchgate.hardware_ok({"genesis"}, {"genesis", "pc"}))
    # An unknown platform is the ABSENCE of evidence, never a mismatch — reading it as one
    # would refuse every candidate at once.
    check("an unknown platform on our side never refuses",
          matchgate.hardware_ok(set(), {"genesis"}))
    check("nor on theirs", matchgate.hardware_ok({"genesis"}, set()))
    check("but a caller can ask whether the answer was EVIDENCE",
          matchgate.hardware_stated({"genesis"}, {"gamegear"})
          and not matchgate.hardware_stated({"genesis"}, set()))

    print()
    print("(c2) a ScreenScraper system with no igdb_platform is still checked")
    con = M.connect()
    # ss 900 is Sonic 2 on system 138 (PC Windows). The only IGDB games of that name are
    # a Genesis game and a Game Gear game, so it must NOT have merged onto either.
    r = M.resolve(con, "ss", "900")
    check("it took neither console game's identity: %r" % r.get("igdb"),
          not r.get("igdb"))
    check("it minted its own instead", r["_identity_id"] >= M.SS_ID_BASE)
    # And the same hole must not have BROKEN the case it exists for: a genuine PC game on
    # system 138 still merges onto its PC IGDB record.
    r = M.resolve(con, "ss", "901")
    check("a real PC record still merges onto the PC game: %r" % r.get("igdb"),
          r.get("igdb") == ["200"])

    print()
    print("(c3) TheGamesDB cannot name its platform, so ambiguity is refused")
    r = M.resolve(con, "thegamesdb", "4444")
    check("the Sonic 2 row did not pick one of the two: %r" % r.get("igdb"),
          not r.get("igdb"))
    check("it minted its own identity", r["_identity_id"] >= M.TGDB_CAT_ID_BASE)
    r = M.resolve(con, "thegamesdb", "4445")
    check("while an unambiguous row still links: %r" % r.get("igdb"),
          r.get("igdb") == ["200"])
    msrc = open(os.path.join(DIR, "ludodex", "matchindex.py")).read()
    check("the moby merge no longer shadows the platmap MODULE with a local dict",
          "\n        platmap = {}" not in msrc)
    con.close()

    print()
    print("(d) RetroAchievements identity goes through the gate")
    import ra_fetch
    listing = [
        {"ID": 1, "Title": "Sonic the Hedgehog 2"},
        {"ID": 2, "Title": "~Hack~ Sonic the Hedgehog 2 Delta"},
        {"ID": 3, "Title": "Sonic the Hedgehog 2 [Subset - Bonus]"},
        {"ID": 4, "Title": "~Prototype~ Sonic the Hedgehog 2"},
        {"ID": 5, "Title": "Streets of Rage"},
        {"ID": 6, "Title": "Streets of Rage (Alt)"},
    ]
    ix = ra_fetch.ra_candidates(listing)
    check("a hack is not the game", 2 not in {g for v in ix.values() for g, _t in v})
    check("a subset is not the game", 3 not in {g for v in ix.values() for g, _t in v})
    check("a prototype is not the game",
          4 not in {g for v in ix.values() for g, _t in v})
    check("the real game is: %r" % ix.get("sonic the hedgehog 2"),
          ra_fetch.ra_match(ix, "sonic the hedgehog 2", "Sonic the Hedgehog 2")[0] == 1)
    # Two RA rows normalising to one key is not a tie to break — nothing separates them.
    check("an ambiguous key answers nothing",
          ra_fetch.ra_match(ix, "streets of rage", "Streets of Rage") is None)
    # A norm_key equal by accident is still judged against the owned title.
    ix2 = ra_fetch.ra_candidates([{"ID": 9, "Title": "Contra III: The Alien Wars"}])
    check("a candidate the gate refuses is not accepted",
          ra_fetch.ra_match(ix2, "contra 3 the alien wars", "Contra") is None)
    check("while the real title is",
          ra_fetch.ra_match(ix2, "contra 3 the alien wars",
                            "Contra III: The Alien Wars")[0] == 9)

    print()
    print("(d2) a second console does not silently overwrite the first")
    rdb = sqlite3.connect(":memory:")
    rdb.execute("CREATE TABLE ra_games(norm_key TEXT PRIMARY KEY, ra_id INTEGER, "
                "ra_title TEXT, console_id INTEGER, matched_at TEXT)")
    kept = ra_fetch.record_match(rdb, "sonic the hedgehog 2", 1, "Sonic 2", 1)
    check("the first console's id is written", kept)
    again = ra_fetch.record_match(rdb, "sonic the hedgehog 2", 77, "Sonic 2", 15)
    check("the second console's id does not replace it", not again)
    row = rdb.execute("SELECT ra_id, console_id FROM ra_games").fetchone()
    check("the stored row is still the first console's: %r" % (row,),
          tuple(row) == (1, 1))
    check("and re-running the same console is still idempotent",
          ra_fetch.record_match(rdb, "sonic the hedgehog 2", 1, "Sonic 2", 1))
    rdb.close()

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
