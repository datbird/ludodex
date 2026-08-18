#!/usr/bin/env python3
"""The MobyGames and TheGamesDB catalogues must actually reach the index.

  * A MIRROR THAT IS ATTACHED IS NOT A MIRROR THAT IS MERGED. `_attach` opens four
    databases and then reports which of them are usable. Steps 8 and 9 ask that report
    whether their catalogue is there; if the report cannot NAME them, both steps return
    (0, 0) against a full, healthy catalogue and the build says it succeeded.
  * THIS IS THE PROJECT'S RECURRING BUG, one layer up: a lookup misses and the miss is
    read as consent. Here the missing thing is the mirror's own name.
  * THE EXISTING TESTS COULD NOT SEE IT. test_moby_mirror and test_tgdb_mirror slice
    matchindex.py as TEXT and assert on the source of the merge steps, so they pass
    whether or not anything ever calls them. A guard on this class has to BUILD.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ludodex")
    sys.path.insert(0, here)
    import test_support
    test_support.isolate("ludodex-mi-catalogues-")
    import sqlite3
    import config
    import matchindex as M

    # All three free layers fetch over the network. They are real behaviour with their
    # own tests; here they would only add nondeterminism to an assertion about two
    # LOCAL catalogues.
    config.set_("matchindex_tgdb_freemap", "0")
    config.set_("matchindex_wikidata_ids", "0")
    config.set_("matchindex_libretro_dats", "0")

    # ---- fixture mirrors ---------------------------------------------------- #
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
    ig.execute("INSERT INTO games VALUES(20,'BioShock','bioshock','bioshock',0,2007,"
               "1187654400,'6',NULL,NULL,0,0)")
    ig.execute("INSERT INTO platforms VALUES(6,'Windows','PC',NULL,NULL,NULL,NULL)")
    ig.execute("INSERT INTO game_platforms VALUES(20,6)")
    ig.commit(); ig.close()

    # MobyGames: the same game, on hardware that maps to IGDB's platform 6, so this one
    # should LINK rather than mint. Its id is what the index has to come away holding.
    mb = sqlite3.connect(M.MOBY_DB)
    mb.executescript("""
    CREATE TABLE moby_games(id INTEGER PRIMARY KEY, title TEXT, norm_key TEXT,
      year INTEGER, score REAL, genres TEXT, payload TEXT, seen_at INTEGER);
    CREATE TABLE moby_platforms(game_id INTEGER, platform_id INTEGER,
      platform_name TEXT, first_release TEXT, PRIMARY KEY(game_id, platform_id));
    CREATE TABLE moby_alt(game_id INTEGER, name TEXT, norm_key TEXT,
      PRIMARY KEY(game_id, name));
    CREATE TABLE state(k TEXT PRIMARY KEY, v TEXT);
    """)
    mb.execute("INSERT INTO moby_games VALUES(555,'BioShock','bioshock',2007,"
               "NULL,NULL,NULL,0)")
    mb.execute("INSERT INTO moby_platforms VALUES(555,3,'Windows','2007-08-21')")
    mb.commit(); mb.close()

    tg = sqlite3.connect(M.TGDB_DB)
    tg.executescript("""
    CREATE TABLE tgdb_games(id INTEGER PRIMARY KEY, name TEXT, norm_key TEXT,
      platform INTEGER, region_id INTEGER, country_id INTEGER, release_date TEXT,
      year INTEGER, players INTEGER, coop TEXT, esrb TEXT, genres TEXT,
      developers TEXT, publishers TEXT, youtube TEXT, os TEXT, min_spec TEXT,
      seen_at INTEGER);
    """)
    tg.execute("INSERT INTO tgdb_games VALUES(777,'BioShock','bioshock',1,NULL,NULL,"
               "'2007-08-21',2007,1,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,0)")
    tg.commit(); tg.close()

    print()
    print("1. _attach reports every mirror it opened")
    # The regression itself, at the smallest scale that can express it. Attaching a
    # database and omitting it from the report are two different statements, and every
    # caller downstream believes the report.
    con = M.con_db()
    have = M._attach(con)
    opened = {r[1] for r in con.execute("PRAGMA database_list")}
    # No ScreenScraper fixture here — that merge has its own test. The three this
    # fixture builds are the ones the claim is about.
    check("the mirrors that exist are attached: %s" % sorted(opened - {"main"}),
          {"ig", "mb", "tg"} <= opened)
    check("_attach names the MobyGames mirror it opened (have=%s)" % sorted(have),
          "mb" in have)
    check("_attach names the TheGamesDB mirror it opened", "tg" in have)
    con.close()

    print()
    print("2. a build folds both catalogues in")
    st = M.build(progress=False)
    ix = M.open_index()
    moby_keys = [tuple(r) for r in ix.execute(
        "SELECT val, identity_id FROM identity_key WHERE ns='mobygames'")]
    tgdb_keys = [tuple(r) for r in ix.execute(
        "SELECT val, identity_id FROM identity_key WHERE ns='thegamesdb'")]
    ix.close()
    check("the MobyGames id reached the index: %s" % moby_keys, len(moby_keys) == 1)
    check("the TheGamesDB id reached the index: %s" % tgdb_keys, len(tgdb_keys) == 1)

    print()
    print("3. a matched catalogue game LINKS instead of minting a rival identity")
    # Minting would be the quiet failure: the id lands, the count looks right, and the
    # library now holds two identities for one game.
    check("moby 555 resolves to the IGDB identity 20, not %d+555"
          % M.MOBY_ID_BASE, moby_keys and moby_keys[0][1] == 20)
    check("the build reports it as linked, not new (linked=%s new=%s)"
          % (st.get("moby_linked"), st.get("moby_new_identities")),
          st.get("moby_linked") == 1 and st.get("moby_new_identities") == 0)

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
