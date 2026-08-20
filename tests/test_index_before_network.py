#!/usr/bin/env python3
"""The match index must be asked BEFORE a provider is.

  * THE WHOLE POINT OF HOLDING AN INDEX is that a handle we already have answers the
    question for free. An index consulted after the search saves nothing.
  * ONLY EXACT ANCHORS. A store id or another provider's id is a pairing somebody
    PUBLISHED. A name is a conclusion we reached, and anchoring on one would let an index
    collision return a confidently wrong id with no acceptance gate in front of it — the
    fail-open shape this codebase keeps paying for.
  * A MISS MUST FALL THROUGH, NOT STOP. The index not knowing a game is the absence of
    an answer, never "this game has no match".
  * FAIL-OPEN ON ABSENCE. No index at all must behave exactly like an index that has
    never heard of the game.
  * ONLY A USABLE HANDLE. A namespace is a bag of handles, not a typed column. Every
    provider answered from here ids by integer, so a slug sitting under one of those
    namespaces must be refused, not passed on as a lookup key no request can use.
  * SEVERAL IDS IS NOT AN ANSWER. ScreenScraper keeps a record per system and TheGamesDB
    one per region; the build attaches all of them deliberately. Choosing needs the
    platform the CALLER holds, so the index declines and the provider is searched.
  * EVERY STORE IS AN ANCHOR, NOT JUST STEAM. A GOG-only or Xbox-only game used to enter
    the pipeline with no handle and get searched by name like a stranger, while the index
    held 9,340 GOG and 15,547 Xbox keys that answered outright.
  * THE PROVIDER MAP HAS ONE HOME. It was written out by hand in three places. A provider
    ruled unusable in provider_ids stayed answerable in the other two.
  * THE CALLER'S PLATFORM SETTLES MOST AMBIGUITY. ScreenScraper keeps one record per
    system, so filtering by the platform the game actually runs on removes records for
    OTHER hardware — different products, not other opinions.
  * AN UNKNOWN PLATFORM IS NOT A MISMATCH. An index built before the column existed has
    every row NULL. Reading NULL as "wrong platform" would drop every candidate and turn
    a working lookup into a permanent miss.
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
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "ludodex")
    sys.path.insert(0, here)
    import test_support
    test_support.isolate("ludodex-ixfirst-")
    import sqlite3
    import matchindex
    import provider_ids

    # A tiny index: one identity carrying a steam id and an igdb id.
    ix = sqlite3.connect(matchindex.DB)
    ix.executescript("""
    CREATE TABLE identity(id INTEGER PRIMARY KEY, name TEXT, norm_key TEXT, year INTEGER,
      first_release_date INTEGER, built_at INTEGER);
    CREATE TABLE identity_key(ns TEXT, val TEXT, identity_id INTEGER, kind TEXT,
      PRIMARY KEY(ns, val, identity_id));
    CREATE TABLE identity_state(k TEXT PRIMARY KEY, v TEXT);
    """)
    ix.execute("INSERT INTO identity VALUES(20,'BioShock','bioshock',2007,NULL,0)")
    for ns, val in (("steam", "7670"), ("igdb", "20"), ("mobygames", "bioshock"),
                    ("ss", "9911")):
        ix.execute("INSERT INTO identity_key VALUES(?,?,20,'exact')", (ns, val))
    ix.commit(); ix.close()

    con = provider_ids.con() if hasattr(provider_ids, "con") else None
    if con is None:
        import config                              # noqa: F401
        con = sqlite3.connect(os.path.join(os.environ["LUDODEX_DATA"],
                                           "metadata-cache.sqlite"))
        con.row_factory = sqlite3.Row
    provider_ids.ensure_tables(con)

    print()
    print("1. an EXACT anchor answers without touching the provider")
    calls = []

    def never(_t, _s):
        calls.append(1)
        raise AssertionError("the provider was searched despite an index hit")

    pid = provider_ids.resolve(con, "screenscraper", "bioshock", "BioShock", ["pc"],
                               never, anchors={"steam": "7670"})
    check("returned the ScreenScraper id from the index: %r" % pid, pid == 9911)
    check("the provider was never called", not calls)

    row = provider_ids.cached(con, "screenscraper", "bioshock")
    check("recorded as matched_by='index': %r" % (row[1] if row else None),
          row and row[1] == "index")

    print()
    print("2. a NAME is never used as an anchor")
    # 'name' is in the index for this identity via norm_key, but a name is a conclusion,
    # not a published pairing. Anchoring on one would make an index collision a silent
    # wrong bind.
    got = provider_ids.index_lookup("screenscraper", {"name": "bioshock"})
    check("a name anchor resolves nothing", got is None)

    print()
    print("3. an index MISS falls through to the provider")
    searched = []

    def searcher(_t, _s):
        searched.append(1)
        return {"ss_id": 4242, "name": "Other"}

    pid2 = provider_ids.resolve(con, "screenscraper", "other game", "Other", ["pc"],
                                searcher, anchors={"steam": "999999"})
    check("the provider WAS searched", bool(searched))
    check("and its answer was used: %r" % pid2, pid2 == 4242)

    print()
    print("4. no index at all behaves like an index that does not know the game")
    os.rename(matchindex.DB, matchindex.DB + ".gone")
    searched2 = []

    def searcher2(_t, _s):
        searched2.append(1)
        return {"ss_id": 77, "name": "Third"}

    pid3 = provider_ids.resolve(con, "screenscraper", "third game", "Third", ["pc"],
                                searcher2, anchors={"steam": "7670"})
    check("still searched, no crash", bool(searched2) and pid3 == 77)
    os.rename(matchindex.DB + ".gone", matchindex.DB)

    print()
    print("5. igdb_enrich asks the index before the network")
    src = open(os.path.join(here, "igdb_enrich.py")).read()
    i_ix = src.find('matchindex.resolve(_ix, "steam"')
    # The QUERY, not the word: 'external_games' also appears in the comment explaining
    # why the index pass exists, which sits before it and made this assertion lie.
    i_net = src.find('igdb.query("external_games"')
    check("the index pass exists", i_ix > 0)
    check("and it runs BEFORE the external_games query", 0 < i_ix < i_net)

    print()
    print("6. 'index' counts as EXACT evidence in record()")
    psrc = open(os.path.join(here, "provider_ids.py")).read()
    check("exempt from the search-collision guard",
          '"manual", "steam_appid", "hash", "index"' in psrc)

    print()
    print("7. a non-integer handle is refused for an integer-id provider")
    # Wikidata contributes MobyGames URL SLUGS while the catalogue merge contributes
    # numeric ids, and both land under one namespace. A slug returned as a ScreenScraper
    # or TheGamesDB id is a lookup key no request can ever use.
    check("a slug is not a usable screenscraper id",
          not provider_ids._usable_id("screenscraper", "bioshock"))
    check("a number is", provider_ids._usable_id("screenscraper", "9911"))
    check("a slug IS usable for a string-id provider",
          provider_ids._usable_id("mobygames", "bioshock"))
    check("mobygames is not answered from the index at all",
          "mobygames" not in provider_ids.INDEX_NS)

    print()
    print("8. several candidates is a fall-through, not a pick")
    ix2 = sqlite3.connect(matchindex.DB)
    # A second ScreenScraper record for the same game, which is what SS having one row
    # per system actually looks like.
    ix2.execute("INSERT INTO identity_key VALUES('ss','30001',20,'exact')")
    ix2.commit(); ix2.close()
    check("two ss ids resolve to nothing",
          provider_ids.index_lookup("screenscraper", {"steam": "7670"}) is None)
    check("while the unambiguous igdb id still answers",
          provider_ids.index_lookup("igdb", {"steam": "7670"}) == "20")

    print()
    print("9. every store id is an anchor, not just Steam")
    for store, ns in (("gog", "gog"), ("xbox", "xbox"), ("epic", "epic"),
                      ("psn", "psn"), ("itch", "itch"), ("steam", "steam")):
        check("%s maps to the index namespace %r" % (store, ns),
              matchindex.STORE_NS.get(store) == ns)
    ix3 = sqlite3.connect(matchindex.DB)
    ix3.execute("INSERT OR IGNORE INTO identity_key VALUES('gog','2022341186',20,'exact')")
    ix3.commit(); ix3.close()
    check("a GOG id alone resolves the igdb id",
          provider_ids.index_lookup("igdb", {"gog": "2022341186"}) == "20")

    src_app = open(os.path.join(os.path.dirname(here), "server", "app.py")).read()
    check("app.py builds anchors from the store ids it read",
          "_anchors = {k: v for k, v in _store_ids.items() if v}" in src_app)
    check("and reads source_id on the same query it already made",
          "SELECT DISTINCT s.source, s.source_id" in src_app)
    # `appid` is a LIMIT 1 pick, so on a base-game/GOTY pair it is arbitrary. An exact
    # handle is the one kind of evidence no acceptance gate ever re-examines.
    check("an arbitrary LIMIT 1 appid is never used as an exact anchor",
          '_anchors.setdefault("steam"' not in src_app)
    check("two ids for one store cancel instead of picking one",
          "None if _ns in _store_ids else str(_sid)" in src_app)

    print()
    print("10. the provider namespace map is written ONCE")
    check("app.py derives it from provider_ids",
          "_ix_ns = dict(provider_ids.INDEX_NS)" in src_app)
    check("app.py does not restate it",
          '"screenscraper": "ss"' not in src_app)
    rsrc = open(os.path.join(here, "romhash.py")).read()
    check("romhash inverts it rather than restating it",
          "NS_TO_PROVIDER = {" not in rsrc and "provider_ids.INDEX_NS" in rsrc)

    print()
    print("11. the caller's platform separates one record per system")
    import json as _json
    ss = sqlite3.connect(matchindex.SS_DB)
    ss.executescript("""CREATE TABLE ss_systems(id INTEGER PRIMARY KEY, name TEXT,
      names TEXT, company TEXT, type TEXT, igdb_platform INTEGER, mapped_by TEXT);
    CREATE TABLE ss_games(id INTEGER PRIMARY KEY, systeme INTEGER, name TEXT,
      norm_key TEXT, year INTEGER, developer TEXT, publisher TEXT, notgame INTEGER,
      n_roms INTEGER, seen_at INTEGER);""")
    # System 138 is "PC Windows" and its alias list leads with a name platmap does NOT
    # map. Taking the first alias would stamp `pcwindows`, which matches no ludodex
    # platform and would quietly exclude every PC record from every filter.
    ss.execute("INSERT INTO ss_systems VALUES(138,?,?,'Microsoft','Ordinateur',NULL,NULL)",
               ("Microsoft Windows,Windows 10", _json.dumps(["PC Windows", "Windows"])))
    ss.execute("INSERT INTO ss_systems VALUES(33,?,?,'Microsoft','Console',12,'name')",
               ("Microsoft XBOX 360,XBOX 360", _json.dumps(["Xbox 360"])))
    for gid, sysid, nm in ((8001, 33, "Alien"), (8002, 138, "Alien"),
                           (8003, 138, "Alien (Ripley Edition)")):
        ss.execute("INSERT INTO ss_games VALUES(?,?,?,'alien',2014,NULL,NULL,0,0,0)",
                   (gid, sysid, nm))
    ss.commit(); ss.close()

    ixc = matchindex.con_db()
    ixc.execute("INSERT INTO identity VALUES(88,'Alien','alien',2014,NULL,0)")
    for v in ("8001", "8002"):
        ixc.execute("INSERT INTO identity_key(ns,val,identity_id,kind) "
                    "VALUES('ss',?,88,'exact')", (v,))
    ixc.execute("INSERT INTO identity_key(ns,val,identity_id,kind) "
                "VALUES('steam','8800',88,'exact')")
    ixc.commit()
    n = matchindex.backfill_platforms(ixc, progress=False)
    check("platforms were stamped: %d" % n, n >= 2)
    got = dict(ixc.execute("SELECT val, platform FROM identity_key WHERE ns='ss'"))
    check("PC Windows resolved to 'pc', not 'pcwindows': %r" % got.get("8002"),
          got.get("8002") == "pc")
    check("the console record kept its own platform: %r" % got.get("8001"),
          got.get("8001") == "xbox360")

    check("with no platform the answer stays ambiguous",
          provider_ids.index_lookup("screenscraper", {"steam": "8800"}) is None)
    check("asking as a pc game picks the PC record",
          provider_ids.index_lookup("screenscraper", {"steam": "8800"},
                                    systems=["pc"]) == "8002")
    check("asking as an Xbox 360 game picks the console record",
          provider_ids.index_lookup("screenscraper", {"steam": "8800"},
                                    systems=["xbox 360"]) == "8001")
    # No candidate on that platform is the ABSENCE of an answer, so it must fall through
    # to a search — never be read as "this game has no ScreenScraper record".
    check("a platform with no record falls through, it does not answer",
          provider_ids.index_lookup("screenscraper", {"steam": "8800"},
                                    systems=["switch"]) is None)
    check("an unmapped platform label never separates anything",
          provider_ids.index_lookup("screenscraper", {"steam": "8800"},
                                    systems=["nonesuch"]) is None)

    print()
    print("12. editions on ONE platform are not separable, and must not be picked")
    ixc.execute("INSERT INTO identity_key(ns,val,identity_id,kind) "
                "VALUES('ss','8003',88,'exact')")
    ixc.commit()
    matchindex.backfill_platforms(ixc, progress=False)
    check("two PC records leave the answer ambiguous",
          provider_ids.index_lookup("screenscraper", {"steam": "8800"},
                                    systems=["pc"]) is None)
    check("while another platform still resolves",
          provider_ids.index_lookup("screenscraper", {"steam": "8800"},
                                    systems=["xbox 360"]) == "8001")

    print()
    print("13. an unstamped index behaves exactly like one with no column")
    ixc.execute("UPDATE identity_key SET platform=NULL WHERE ns='ss'")
    ixc.commit()
    # NULL is UNKNOWN. Read as a mismatch it would drop every candidate at once.
    check("a NULL platform never removes a candidate",
          provider_ids.index_lookup("screenscraper", {"steam": "8800"},
                                    systems=["pc"]) is None)
    ixc.execute("DELETE FROM identity_key WHERE ns='ss' AND val IN ('8002','8003')")
    ixc.commit()
    check("and a lone unstamped candidate still answers",
          provider_ids.index_lookup("screenscraper", {"steam": "8800"},
                                    systems=["pc"]) == "8001")
    ixc.close()

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
