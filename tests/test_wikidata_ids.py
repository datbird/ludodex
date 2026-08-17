#!/usr/bin/env python3
"""Free cross-database pointers, and the layer they exposed as broken.

A CROSS-REFERENCE IS A POINTER, NOT CONTENT. "This game is MobyGames #bulletstorm"
carries none of their prose, art or curation — it is the coordinate you use to go and ask
them. That is why the shipped supplement can carry ids from sources whose DATA it could
never redistribute, and Wikidata is CC0 on top, so the pointers have no strings at all.
Pulled live 2026-08-17: 33,956 MobyGames, 59,447 Redump, 1,878 TheGamesDB — 95,281 for
zero requests and zero dollars.

THE JOIN IS ON THE IGDB SLUG, and that is the whole reason this is allowed to run.
Wikidata stores IGDB's slug rather than its numeric id, and the mirror carries both, so
`bulletstorm` resolves locally and exactly — the same class of key as a hash. Nothing
matches on a title and nothing mints an identity: an unrecognised slug is skipped,
because a pointer anchored to a game we do not have points at nothing.

AND THE BUG THIS WORK FOUND. `provider_ids` was written when every provider ided by
NUMBER. Adding MobyGames (a slug), ArcadeDB (a MAME set name) and ZXInfo (a zero-padded
string) broke it SILENTLY: `record()` ran `int('bulletstorm')`, caught the ValueError,
and wrote a MISS. A perfectly good id became "we looked and found nothing" — the worst
possible failure, because it looks like an answer and suppresses the re-search for thirty
days. Section 5 exists so that cannot come back.
"""
import os
import sqlite3
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


CSV = ("ns,igdb_slug,value\n"
       "mobygames,bulletstorm,bulletstorm\n"
       "mobygames,bulletstorm,bulletstorm-full-clip-edition\n"
       "thegamesdb,bulletstorm,54321\n"
       "redump,doom,SLUS-00001\n"
       "mobygames,a-game-we-do-not-have,whatever\n"
       "mobygames,,orphan\n"
       "broken line\n")


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_support
    data = test_support.isolate("ludodex-wd-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import config
    import matchindex as M
    import provider_ids as PI
    import provider_links as PL
    import wikidata_ids as W

    print("1. the cache parses, and junk lines are skipped rather than fatal")
    p = os.path.join(data, "wd.csv")
    open(p, "w", encoding="utf-8").write(CSV)
    rows = list(W.rows(p))
    check("4 usable rows out of 7: %d" % len(rows), len(rows) == 5)
    check("the header is not a row", all(r[0] != "ns" for r in rows))
    check("a row with no slug is dropped", all(r[1] for r in rows))
    check("a malformed line is dropped", all(len(r) == 3 for r in rows))

    print()
    print("2. ONE GAME CAN CARRY SEVERAL POINTERS — edition splits are legitimate")
    moby = [r for r in rows if r[0] == "mobygames" and r[1] == "bulletstorm"]
    check("bulletstorm has 2 MobyGames coordinates", len(moby) == 2)
    check("and they differ", moby[0][2] != moby[1][2])

    print()
    print("3. it attaches on the SLUG, and skips what it cannot anchor")
    W.CACHE = p
    con = M.con_db()
    con.execute("ATTACH DATABASE ? AS ig", (M.IGDB_DB,))
    con.executescript("""
      CREATE TABLE ig.games(id INTEGER PRIMARY KEY, name TEXT, slug TEXT,
        norm_key TEXT, year INTEGER, first_release_date INTEGER);
    """)
    con.execute("INSERT INTO ig.games VALUES(7,'Bulletstorm','bulletstorm',"
                "'bulletstorm',2011,NULL)")
    con.execute("INSERT INTO ig.games VALUES(9,'Doom','doom','doom',1993,NULL)")
    con.execute("INSERT INTO identity(id,name,norm_key,year,first_release_date,built_at)"
                " VALUES(7,'Bulletstorm','bulletstorm',2011,NULL,0)")
    con.execute("INSERT INTO identity(id,name,norm_key,year,first_release_date,built_at)"
                " VALUES(9,'Doom','doom',1993,NULL,0)")
    con.commit()
    n = M._merge_wikidata_ids(con, progress=False)

    def owners(ns, val):
        return [r["identity_id"] for r in con.execute(
            "SELECT identity_id FROM identity_key WHERE ns=? AND val=?", (ns, val))]

    check("%d pointer keys written" % n, n == 4)
    check("both MobyGames coordinates landed on the SAME identity",
          owners("mobygames", "bulletstorm") == [7]
          and owners("mobygames", "bulletstorm-full-clip-edition") == [7])
    check("the TheGamesDB pointer too", owners("thegamesdb", "54321") == [7])
    check("and a Redump serial on the other game", owners("redump", "SLUS-00001") == [9])
    check("a slug we do not have was SKIPPED, not minted",
          owners("mobygames", "whatever") == []
          and con.execute("SELECT COUNT(*) FROM identity").fetchone()[0] == 2)
    before = con.execute("SELECT COUNT(*) FROM identity_key").fetchone()[0]
    M._merge_wikidata_ids(con, progress=False)
    check("re-running changes nothing", con.execute(
        "SELECT COUNT(*) FROM identity_key").fetchone()[0] == before)
    con.close()

    print()
    print("4. nothing here matches on a NAME")
    src = open(os.path.join(root, "ludodex", "matchindex.py"), encoding="utf-8").read()
    step = src[src.index("def _merge_wikidata_ids"):src.index("def _merge_libretro_dats")]
    check("no name or alias lookup in the step",
          "ns='name'" not in step and "ns='alias'" not in step
          and "resolve_name" not in step)
    check("it joins on ig.games.slug", "FROM ig.games" in step and "slug" in step)
    check("and says why the slug is allowed to be trusted",
          "EXACT KEY" in step or "exact key" in step.lower())

    print()
    print("5. THE BUG IT FOUND — a string id must not become a silent MISS")
    con2 = sqlite3.connect(":memory:")
    PI.ensure_tables(con2)
    PI.record(con2, "mobygames", "nk1", "bulletstorm", name="Bulletstorm")
    got = PI.cached(con2, "mobygames", "nk1")
    check("the slug is stored AS a slug: %r" % (got[0],), got[0] == "bulletstorm")
    check("...not coerced to 0 and written off as a miss", got[1] == "search")
    check("is_identified is True for it — and does not raise on a string",
          PI.is_identified(con2, "mobygames", "nk1") is True)
    PI.record(con2, "zxinfo", "nk3", "0002259")
    check("a zero-padded id keeps its padding — int() would have eaten it",
          PI.cached(con2, "zxinfo", "nk3")[0] == "0002259")
    check("the collision guard still works on strings",
          PI.holder(con2, "mobygames", "bulletstorm", "nk2") == "nk1")
    PI.record(con2, "mobygames", "nk2", "bulletstorm", name="Impostor")
    check("a second claimant is refused as a collision, not written",
          PI.cached(con2, "mobygames", "nk2")[1] == "collision")
    PI.record(con2, "thegamesdb", "nk4", 142)
    check("and NUMERIC providers are untouched by all of this",
          PI.cached(con2, "thegamesdb", "nk4")[0] == 142
          and PI.is_identified(con2, "thegamesdb", "nk4"))
    con2.close()

    print()
    print("6. every new provider can become a visible link")
    check("mobygames -> a real page",
          PL.page_url("mobygames", "bulletstorm")
          == "https://www.mobygames.com/game/bulletstorm/")
    check("arcadedb keys on the MAME set name",
          PL.page_url("arcadedb", "pacman").endswith("?mame=pacman"))
    check("zxinfo keeps the padding in the url",
          PL.page_url("zxinfo", "0002259").endswith("/0002259"))
    check("a numeric provider still requires a number",
          PL.page_url("thegamesdb", "abc") is None)
    # A provider id goes straight into a URL we hand the user; anything that is not a
    # plain slug is refused rather than escaped and hoped for.
    for bad in ("../../evil", "a b", "javascript:x", ""):
        check("refused: %-14r" % bad, PL.page_url("mobygames", bad) is None)
    check("every provider_ids provider has a page template",
          set(PI.PROVIDERS) - set(PL.PAGE_URL) == {"igdb"})

    print()
    print("7. settings, and a source is a source")
    for k, d in (("matchindex_wikidata_ids", "1"),
                 ("wikidata_ids_namespaces", "mobygames,thegamesdb,redump"),
                 ("wikidata_ids_cache_days", "30")):
        check("%-26s = %r" % (k, d), config.DEFAULTS.get(k) == d)
    check("steam and gog are left OUT by default — IGDB's own store ids are "
          "authoritative", "steam" not in W.wanted() and "gog" not in W.wanted())
    check("but they are available if asked for",
          {"steam", "gog"} <= {ns for _p, ns in W.PROPS})
    check("Wikidata is credited as a source",
          any(s["name"] == "Wikidata" for s in M.SOURCES))
    check("as CC0, which is the whole point",
          any(s.get("license") == "CC0 1.0" for s in M.SOURCES))

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
