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
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
