#!/usr/bin/env python3
"""One table, one query, any direction — and the ways that can go quietly wrong.

  * A STORE ID IS EXACT, A NAME IS NOT. Both live in the same table, so `kind` is the
    only thing keeping "IGDB publishes this pairing" apart from "we concluded it".
  * A ScreenScraper game that does NOT match must get its OWN identity. The recurring
    defect in this codebase is a lookup that misses and gets read as consent; here that
    would silently weld two different games together and hand one the other's hashes.
  * HARDWARE HAS TO AGREE. Two games can share a name across systems, and the ROM
    hashes hanging off the wrong one would be worse than no match at all.
  * IDENTITY ID RANGES MUST NOT COLLIDE. IGDB ids and SS-only ids share one column.
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
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, here)
    import test_support
    test_support.isolate("ludodex-matchindex-")
    import sqlite3
    import matchindex as M

    # ---- fake mirrors ------------------------------------------------------- #
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
    ig.execute("INSERT INTO games VALUES(1074,'Super Mario 64','sm64','super mario 64',"
               "0,1996,835488000,'4',NULL,NULL,0,0)")
    # Same name, different hardware — the trap the platform check exists for.
    ig.execute("INSERT INTO games VALUES(555,'Golden Axe','ga','golden axe',0,1989,"
               "NULL,'29',NULL,NULL,0,0)")
    ig.executemany("INSERT INTO alt_names VALUES(?,?,?)",
                   [(1074, 'Mario 64', 'mario 64'), (1074, 'SM64', 'sm64')])
    ig.executemany("INSERT INTO game_platforms VALUES(?,?)",
                   [(20, 6), (1074, 4), (555, 29)])
    ig.executemany("INSERT INTO stores VALUES(?,?)",
                   [(1, 'Steam'), (5, 'GOG'), (26, 'Epic Games Store')])
    ig.executemany("INSERT INTO external_ids VALUES(?,?,?,?)",
                   [(20, 1, '7670', 'BioShock'), (20, 5, '1207658930', 'BioShock'),
                    (20, 26, 'epic-bio', 'BioShock')])
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
    ss.executemany("INSERT INTO ss_systems VALUES(?,?,?,?,?,?,?)",
                   [(14, 'N64', '[]', 'Nintendo', 'console', 4, 'name'),
                    (1, 'Genesis', '[]', 'Sega', 'console', 29, 'name'),
                    (9, 'Arcade', '[]', '', 'arcade', 52, 'name')])
    # matches igdb 1074 by name+year+platform
    ss.execute("INSERT INTO ss_games VALUES(500,14,'Super Mario 64','super mario 64',"
               "1996,'N','N',0,2,0)")
    # same name as igdb 555 but on ARCADE, which igdb 555 is not on -> must NOT merge
    ss.execute("INSERT INTO ss_games VALUES(600,9,'Golden Axe','golden axe',1989,"
               "'S','S',0,1,0)")
    # nothing like it in igdb at all -> its own identity
    ss.execute("INSERT INTO ss_games VALUES(700,1,'Pulseman','pulseman',1994,"
               "'G','S',0,1,0)")
    ss.executemany("INSERT INTO ss_roms VALUES(?,?,?,?,?,?,?)",
                   [(500, 'aabbccdd', 'md5mario', 'sha1mario', 'sm64.z64', 8, 'us'),
                    (600, '11223344', 'md5axe', 'sha1axe', 'ga.zip', 4, 'us'),
                    (700, '99887766', 'md5pulse', 'sha1pulse', 'pulse.md', 4, 'jp')])
    ss.commit(); ss.close()

    print("1. it builds, and every handle lands in one table")
    st = M.build(progress=False)
    check("3 igdb + 2 ss-only identities: %d" % st["identities"],
          st["identities"] == 5)
    check("ss games that matched were merged, not duplicated: %d merged"
          % st["ss_merged"], st["ss_merged"] == 1)
    check("ss games that did not match got their own: %d" % st["ss_own_identity"],
          st["ss_own_identity"] == 2)

    con = M.connect()

    print()
    print("2. THE query — a GOG id in, every other handle out, one hop")
    r = M.resolve(con, "gog", "1207658930")
    check("found BioShock: %r" % r.get("_name"), r.get("_name") == "BioShock")
    check("steam appid came back: %s" % r.get("steam"), r.get("steam") == ["7670"])
    check("epic came back too", r.get("epic") == ["epic-bio"])
    check("and the igdb id", r.get("igdb") == ["20"])

    print()
    print("3. a ROM hash resolves the same way, with no name matching at all")
    r = M.resolve(con, "sha1", "sha1mario")
    check("the hash found Super Mario 64: %r" % r.get("_name"),
          r.get("_name") == "Super Mario 64")
    check("and carries the screenscraper id", r.get("ss") == ["500"])
    check("and the igdb id — the two catalogs joined", r.get("igdb") == ["1074"])

    print()
    print("4. exact and derived are distinguishable, because they are not equal")
    kinds = {r["ns"]: r["kind"] for r in con.execute(
        "SELECT ns,kind FROM ix.identity_key WHERE identity_id=20")}
    check("a store id is exact", kinds.get("steam") == "exact")
    check("a name is derived", kinds.get("name") == "derived")

    print()
    print("5. same name, wrong hardware -> NOT merged")
    # SS 600 is arcade Golden Axe; IGDB 555 is the Genesis one. A name-only merge would
    # hang the arcade ROM hashes off the console game.
    r = M.resolve(con, "ss", "600")
    check("it did not take the igdb identity: %s" % r.get("igdb"),
          not r.get("igdb"))
    check("it got an id in the SS-only range",
          r["_identity_id"] >= M.SS_ID_BASE)
    r555 = M.resolve(con, "igdb", "555")
    check("and the genesis game kept no arcade hash", not r555.get("crc"))

    print()
    print("6. a game only ScreenScraper knows still gets an identity")
    r = M.resolve(con, "crc", "99887766")
    check("Pulseman resolved: %r" % r.get("_name"), r.get("_name") == "Pulseman")
    check("with its ss id", r.get("ss") == ["700"])

    print()
    print("7. a name off a filename resolves through the SAME gate as a provider")
    hits = M.resolve_name(con, "Super Mario 64", 1996)
    check("it matched", hits and hits[0]["name"] == "Super Mario 64")
    check("an alias works too", M.resolve_name(con, "Mario 64", 1996))
    check("a year that disagrees is refused, not merely ranked lower",
          not M.resolve_name(con, "Super Mario 64", 2015))

    print()
    print("8. a miss returns nothing rather than a plausible neighbour")
    check("unknown store id", M.resolve(con, "steam", "999999") == {})
    check("unknown hash", M.resolve(con, "sha1", "deadbeef") == {})
    check("unknown name", M.resolve_name(con, "Not A Real Game At All") == [])

    print()
    print("9. rebuilding is idempotent — the same input gives the same table")
    before = con.execute("SELECT COUNT(*) FROM identity_key").fetchone()[0]
    con.close()
    M.build(progress=False)
    con = M.con_db()
    after = con.execute("SELECT COUNT(*) FROM identity_key").fetchone()[0]
    check("no duplication on rebuild: %d -> %d" % (before, after), before == after)
    con.close()

    print()
    print("10. the index is OPTIONAL, and absence is not the same as a miss")
    # A machine with no index must fall back to the network; a machine WITH an index
    # that has no row for this game has actually answered. Collapsing those into one
    # empty dict makes the caller refuse games it merely never looked up.
    con = M.connect()
    check("the pipeline handle always opens", con is not None)
    check("the bulk index is attached", M.has_index(con))
    check("a genuine miss is an empty answer", M.resolve(con, "steam", "000000") == {})
    con.close()
    moved = M.DB + ".away"
    os.rename(M.DB, moved)
    try:
        con = M.connect()
        check("with no index file, the handle STILL opens", con is not None)
        check("but reports it has no index", not M.has_index(con))
        con.close()
    finally:
        os.rename(moved, M.DB)

    print()
    print("10b. the pipeline: miss -> search -> learn -> hit, locally, next time")
    con = M.connect()
    check("before searching, ludodex knows nothing about it",
          M.resolve(con, "steam", "424242") == {})
    # ...this is where the old provider search runs. It comes back with three handles
    # for one game, which get written back so the round trip is never repeated.
    iid = M.learn(con, [("steam", "424242"), ("ss", "88888"), ("crc", "feedface")],
                  name="Some Obscure Game", year=1998, provider="screenscraper")
    r = M.resolve(con, "steam", "424242")
    check("now the steam id resolves locally", r.get("ss") == ["88888"])
    check("and so does the ROM hash, from the other direction",
          M.resolve(con, "crc", "feedface").get("steam") == ["424242"])
    check("the learned identity is in its own id range",
          iid >= M.LEARNED_ID_BASE)
    check("and it is marked learned, not exact or derived",
          con.execute("SELECT kind FROM learned_key WHERE ns='ss' AND val='88888'"
                      ).fetchone()["kind"] == "learned")

    print()
    print("10c. a learned handle BINDS to a mirror identity when one already exists")
    # A live search that finds a Steam appid we already know must not mint a second
    # identity for a game the index already has.
    iid2 = M.learn(con, [("steam", "7670"), ("ss", "31337")], provider="screenscraper")
    check("it bound to the existing BioShock identity, not a new one: %s" % iid2,
          iid2 == 20)
    check("and the new ss id now hangs off BioShock",
          "31337" in (M.resolve(con, "gog", "1207658930").get("ss") or []))

    print()
    print("10d. REBUILDING THE INDEX MUST NOT DESTROY WHAT WAS LEARNED")
    # The whole reason learned rows live in the main db. A rebuild regenerates the
    # bulk index from the mirrors; anything obtained by a rate-limited search that no
    # mirror contains would be gone forever if it lived in the rebuilt file.
    con.close()
    M.build(progress=False)
    con = M.connect()
    check("the learned game survived the rebuild",
          M.resolve(con, "steam", "424242").get("ss") == ["88888"])
    check("and so did the learned key on a mirror identity",
          "31337" in (M.resolve(con, "igdb", "20").get("ss") or []))
    con.close()

    print()
    print("10e. the user's override outranks the shipped supplement")
    # The supplement is read-only and replaced wholesale on sync, so a correction
    # cannot be written into it. If it did not outrank the file here, the next sync
    # would silently revert the user.
    con = M.connect()
    check("the supplement's answer, before any correction",
          M.resolve(con, "crc", "aabbccdd").get("igdb") == ["1074"])
    M.override(con, "crc", "aabbccdd", identity_id=20, note="actually bioshock")
    check("the override wins", M.resolve(con, "crc", "aabbccdd").get("igdb") == ["20"])
    M.override(con, "crc", "11223344", note="not a game I own")
    check("an unbind suppresses without naming a replacement",
          M.resolve(con, "crc", "11223344") == {})
    con.close()
    M.build(progress=False)                      # a resync/rebuild must not undo it
    con = M.connect()
    check("and it survives the supplement being rebuilt",
          M.resolve(con, "crc", "aabbccdd").get("igdb") == ["20"])
    con.close()

    print()
    print("10f. running WITHOUT the supplement, then adding it, keeps your own answers")
    # The user's scenario: work for a while with only the dynamic table, then drop the
    # supplement in. What was learned stays authoritative; the supplement fills gaps.
    moved = M.DB + ".away"
    os.rename(M.DB, moved)
    con = M.connect()
    check("no supplement present", not M.has_index(con))
    # Learn something the supplement will later DISAGREE with: this sha1 is Super Mario
    # 64 in the shipped file, but this user has concluded otherwise.
    M.learn(con, [("sha1", "sha1mario"), ("ss", "424243")], name="My Own Conclusion",
            year=2001, provider="screenscraper")
    check("it resolves from the dynamic table alone",
          M.resolve(con, "sha1", "sha1mario").get("ss") == ["424243"])
    con.close()
    os.rename(moved, M.DB)                       # ...the user adds the supplement

    con = M.connect()
    check("the supplement is now present", M.has_index(con))
    r = M.resolve(con, "sha1", "sha1mario")
    check("the DYNAMIC answer still wins: %r" % r.get("_name"),
          r.get("_name") == "My Own Conclusion")
    check("and the supplement's conflicting game is NOT welded in",
          "1074" not in (r.get("igdb") or []))
    check("while a handle only the supplement knows still resolves",
          M.resolve(con, "crc", "99887766").get("_name") == "Pulseman")
    con.close()

    print()
    print("10g. the preference is a toggle, and overrides ignore it entirely")
    con = M.connect()
    check("it defaults to the user's own data",
          getattr(con, "_prefer") == M.PREFER_DYNAMIC)
    con.close()
    M.set_preference(M.PREFER_SUPPLEMENT)
    con = M.connect()
    r = M.resolve(con, "sha1", "sha1mario")
    check("flipped, the supplement now answers first: %r" % r.get("_name"),
          r.get("_name") == "Super Mario 64")
    # An override is the user's explicit word and is not part of this contest.
    check("but an override still outranks BOTH layers",
          M.resolve(con, "crc", "aabbccdd").get("igdb") == ["20"])
    check("and a handle only the dynamic table knows still resolves",
          M.resolve(con, "steam", "424242").get("ss") == ["88888"])
    con.close()
    M.set_preference(M.PREFER_DYNAMIC)
    con = M.connect()
    check("flipping back restores the user's own answer",
          M.resolve(con, "sha1", "sha1mario").get("_name") == "My Own Conclusion")
    con.close()

    print()
    print("11. md5 is not indexed; crc and sha1 are")
    con = M.con_db()
    ns = {r[0] for r in con.execute("SELECT DISTINCT ns FROM identity_key")}
    con.close()
    check("crc present", "crc" in ns)
    check("sha1 present", "sha1" in ns)
    check("md5 absent — dropped deliberately", "md5" not in ns)

    print()
    print("13. licence and attribution are stamped INTO the file")
    # A published sqlite gets copied to a NAS, handed to someone, restored from a
    # backup — each of which separates it from the release notes. ScreenScraper's data
    # is CC BY-NC-SA, so attribution is a CONDITION of redistributing it, and one that
    # only holds while the file sits beside its README is not being met.
    st2 = M.build(progress=False)
    check("the licence is recorded: %r" % st2.get("license"),
          st2.get("license") == "CC BY-NC-SA 4.0")
    check("attribution names ScreenScraper",
          "ScreenScraper" in (st2.get("attribution") or ""))
    check("and states the non-commercial condition",
          "commercial" in (st2.get("attribution") or "").lower())
    srcs = {s["name"] for s in (st2.get("sources") or [])}
    check("both upstreams are named: %s" % sorted(srcs),
          {"ScreenScraper.fr", "IGDB.com"} <= srcs)
    # It has to survive being read back by someone who only has the file.
    ix = M.open_index()
    got = {r[0]: r[1] for r in ix.execute("SELECT k,v FROM identity_state")}
    ix.close()
    check("readable from the file alone, with no ludodex code involved",
          "ScreenScraper" in got.get("attribution", ""))

    print()
    print("12. the index does not squat in the main db")
    # It shipped there briefly. Two copies, one of them stale, is worse than either.
    main = sqlite3.connect(M.MAIN_DB)
    main.executescript("CREATE TABLE IF NOT EXISTS identity(id INTEGER PRIMARY KEY);"
                       "CREATE TABLE IF NOT EXISTS identity_key(ns TEXT);")
    main.commit(); main.close()
    M.con_db().close()                       # opening the index evicts the legacy copy
    main = sqlite3.connect(M.MAIN_DB)
    left = {r[0] for r in main.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    main.close()
    check("legacy tables were dropped from metadata-cache",
          not ({"identity", "identity_key"} & left))

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
