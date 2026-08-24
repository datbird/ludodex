#!/usr/bin/env python3
"""A STRING provider id must never be compared to an integer.

MobyGames, ArcadeDB and ZXInfo id by string — a slug (`bulletstorm`), a MAME set name
(`pacman`), a zero-padded number (`0002259`). `provider_ids.cached()` returns `""` for a
miss on those providers and the slug itself for a hit, and two call sites then compared
that to 0:

    resolve()   `if pid > 0 or matched_by == "manual"`
    unlinked()  `elif row[0] <= 0 and ...`

Both raise `TypeError: '>' not supported between instances of 'str' and 'int'`, for a HIT
and for a MISS alike. `server/app.py`'s provider sweep loops every provider through
`unlinked()`, so the whole non-forced sweep died the moment any moby/arcadedb/zxinfo row
existed — for every provider, including the ones that work.

`is_identified()`'s docstring at provider_ids.py:145 documents this exact shape and its
fix; the fix was simply never applied to the other two readers of the same value. So the
rule lives in ONE place now and all three ask it.

Also covered here, same file and same theme:
  * the `--scrub` CLI derived its data dir from the PACKAGE dir while every other module
    derives it from the repo root above it;
  * `provider_links` kept a SECOND copy of STRING_ID_PROVIDERS and tested membership with
    the un-lowercased provider name while the line above it lowercased for PAGE_URL;
  * `index_lookup` opened a fresh match-index connection per call — three CREATE TABLEs,
    a commit, a ~1 GB ATTACH and a config read, once per game per provider.

Offline. No network.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-ident-ptypes-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import provider_ids                                            # noqa: E402
import provider_links                                          # noqa: E402
import matchindex                                              # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def cache():
    con = sqlite3.connect(os.path.join(DATA, "metadata-cache.sqlite"))
    con.row_factory = sqlite3.Row
    provider_ids.ensure_tables(con)
    return con


def main():
    print("a string provider id is never compared to an integer")
    con = cache()

    print()
    print("1. a RECORDED string id is a decision, and resolve() must return it")
    provider_ids.record(con, "mobygames", "bulletstorm", "bulletstorm",
                        name="Bulletstorm", matched_by="search")
    searched = []

    def never(_t, _s):
        searched.append(1)
        return None

    got = provider_ids.resolve(con, "mobygames", "bulletstorm", "Bulletstorm",
                               ["pc"], never)
    check("the slug came back: %r" % got, got == "bulletstorm")
    check("and the provider was never asked again", not searched)

    print()
    print("2. a RECORDED MISS on a string provider is a miss, not a crash")
    provider_ids.record(con, "arcadedb", "nothing here", "", matched_by="none")
    hit = []

    def searcher(_t, _s):
        hit.append(1)
        return None

    # Fresh miss: suppressed for MISS_TTL. The point is that reading it does not raise.
    got2 = provider_ids.resolve(con, "arcadedb", "nothing here", "Nothing", ["arcade"],
                                searcher)
    check("a fresh miss answers falsy: %r" % got2, not got2)
    check("without asking the provider", not hit)

    print()
    print("3. unlinked() is the sweep's work list — it must survive a string row")
    todo = provider_ids.unlinked(con, "mobygames", ["bulletstorm", "never seen"])
    check("the identified game is not work: %r" % todo, "bulletstorm" not in todo)
    check("the unseen game is", "never seen" in todo)
    # A ZXInfo id is zero-padded, so int() would eat the padding and `> 0` would raise.
    provider_ids.record(con, "zxinfo", "manic miner", "0002259", matched_by="search")
    check("a zero-padded id counts as identified",
          provider_ids.unlinked(con, "zxinfo", ["manic miner"]) == [])

    print()
    print("4. every provider sweeps, the way server/app.py drives it")
    # app.py:1535 does exactly this loop. One string row used to kill all of it.
    todo = set()
    for prov in provider_ids.PROVIDERS:
        todo.update(provider_ids.unlinked(con, prov, ["bulletstorm", "manic miner"]))
    check("the sweep completed across all %d providers" % len(provider_ids.PROVIDERS),
          isinstance(todo, set))

    print()
    print("5. ONE rule for 'is this a real id', asked by all three readers")
    check("is_identified agrees with resolve on a slug",
          provider_ids.is_identified(con, "mobygames", "bulletstorm"))
    check("and on a recorded miss",
          not provider_ids.is_identified(con, "arcadedb", "nothing here"))
    src = open(os.path.join(DIR, "ludodex", "provider_ids.py")).read()
    check("resolve() no longer compares a provider id to 0",
          'if pid > 0 or matched_by == "manual"' not in src)
    check("unlinked() no longer compares a provider id to 0",
          "row[0] <= 0" not in src)

    print()
    print("6. the --scrub CLI resolves DATA the way every other module does")
    # DIR is the PACKAGE; DATA is the repo root ABOVE it. Deriving DATA from the package
    # dir points the scrub at a directory that holds no databases at all — and the
    # sibling modules carry a comment saying exactly that.
    check("it does not fall back to the package directory",
          'os.path.dirname(os.path.abspath(__file__))\n' not in
          src[src.index("def _main("):])
    check("it falls back to the repo root above the package",
          "os.path.dirname(os.path.dirname(os.path.abspath(__file__)))"
          in src[src.index("def _main("):])

    print()
    print("7. STRING_ID_PROVIDERS has ONE home")
    lsrc = open(os.path.join(DIR, "ludodex", "provider_links.py")).read()
    check("provider_links does not restate the set",
          'STRING_ID_PROVIDERS = {"mobygames"' not in lsrc)
    check("it reads provider_ids' copy",
          "provider_ids.STRING_ID_PROVIDERS" in lsrc)
    # PAGE_URL is looked up with the provider LOWERCASED; the membership test one line
    # below used the raw value, so 'MobyGames' found a template and then took the
    # isdigit() branch, dropping every link for a provider whose ids are never digits.
    check("a differently-cased provider still builds a slug URL",
          provider_links.page_url("MobyGames", "bulletstorm")
          == "https://www.mobygames.com/game/bulletstorm/")
    check("and a numeric provider is unaffected",
          provider_links.page_url("screenscraper", "9911")
          == "https://www.screenscraper.fr/gameinfos.php?gameid=9911")

    print()
    print("8. the match index is opened once per thread, not once per lookup")
    ix = sqlite3.connect(matchindex.DB)
    ix.executescript("""
    CREATE TABLE identity(id INTEGER PRIMARY KEY, name TEXT, norm_key TEXT, year INTEGER,
      first_release_date INTEGER, built_at INTEGER);
    CREATE TABLE identity_key(ns TEXT, val TEXT, identity_id INTEGER, kind TEXT,
      platform TEXT, PRIMARY KEY(ns, val, identity_id));
    CREATE TABLE identity_state(k TEXT PRIMARY KEY, v TEXT);
    """)
    ix.execute("INSERT INTO identity VALUES(20,'BioShock','bioshock',2007,NULL,0)")
    for ns, val in (("steam", "7670"), ("igdb", "20")):
        ix.execute("INSERT INTO identity_key(ns,val,identity_id,kind) "
                   "VALUES(?,?,20,'exact')", (ns, val))
    ix.commit()
    ix.close()

    opens = {"n": 0}
    _real_connect = matchindex.connect

    def counting():
        opens["n"] += 1
        return _real_connect()

    matchindex.connect = counting
    try:
        for _ in range(5):
            check("the index still answers",
                  provider_ids.index_lookup("igdb", {"steam": "7670"}) == "20")
        check("five lookups opened the index once, not five times: %d" % opens["n"],
              opens["n"] == 1)
    finally:
        matchindex.connect = _real_connect

    # ABSENCE MUST STAY REACHABLE. A cached handle keeps a deleted file open, so an index
    # that goes away would keep answering — the exact opposite of the fail-open rule this
    # module is built on.
    os.rename(matchindex.DB, matchindex.DB + ".gone")
    check("with the index gone the lookup falls through",
          provider_ids.index_lookup("igdb", {"steam": "7670"}) is None)
    os.rename(matchindex.DB + ".gone", matchindex.DB)
    check("and it answers again once the file is back",
          provider_ids.index_lookup("igdb", {"steam": "7670"}) == "20")

    con.close()
    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
