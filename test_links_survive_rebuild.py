#!/usr/bin/env python3
"""A catalog rebuild must not throw away a provider match (#27).

`_match_providers` records an identity for every configured provider and writes the
`metadata_links` row that makes it visible. `build_library.py` then rebuilds the catalog
from scratch — and rebuilt `metadata_links` from ONLY the sources it knows about: IGDB
from `igdb_resolution`, ScreenScraper only where a cached SS payload happened to exist,
SteamGridDB not at all.

Measured live, 2026-08-04, on a rebuild that was supposed to be a no-op:

    before                    after
    steamgriddb  2255    ->   0
    screenscraper 1807   ->   152
    igdb          2191   ->   2191

That is the whole "a match is not an ingest" guarantee being undone one layer down, and
it is worse than it looks: the tiered ingest itself runs `build_library.py` again after
the media phase, so a match recorded early in a reset is destroyed later in the SAME job.
The user would finish the great reset and find no SteamGridDB links — which is exactly
the complaint that started this work.

The fix is one derivation: `provider_links.sync()` writes these rows FROM the identity
cache, and both the live matcher and the rebuild go through it. So this test asserts a
property, not an implementation — take any set of recorded identities, rebuild, and the
links must still be there.

Offline. No network.
"""
import os
import sqlite3
import sys

import test_support

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    d = test_support.isolate("ludodex-links-")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import provider_ids
    import provider_links

    cache = os.path.join(d, "metadata-cache.sqlite")
    cc = sqlite3.connect(cache)
    provider_ids.ensure_tables(cc)

    # Three games. `arcadia` matched everywhere, `orphan` matched only SGDB (the case
    # build_library could not express at all), `nomatch` matched nothing.
    provider_ids.record(cc, "screenscraper", "arcadia", 4321, name="Arcadia")
    provider_ids.record(cc, "steamgriddb", "arcadia", 999, name="Arcadia")
    provider_ids.record(cc, "steamgriddb", "orphan", 555, name="Orphan")
    provider_ids.record(cc, "screenscraper", "nomatch", 0)      # a recorded MISS
    provider_ids.record(cc, "steamgriddb", "blocked", 777, name="Homebrew Hack")
    cc.commit()

    lib = sqlite3.connect(":memory:")
    lib.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT, canonical_title TEXT);
    CREATE TABLE metadata_links(game_id INTEGER, provider TEXT, provider_id TEXT,
                                slug TEXT, url TEXT);
    """)
    for i, nk in enumerate(("arcadia", "orphan", "nomatch", "blocked"), start=1):
        lib.execute("INSERT INTO games VALUES(?,?,?)", (i, nk, nk.title()))
    # two entries for one title (per-platform entries) — both must get the link
    lib.execute("INSERT INTO games VALUES(9,'arcadia','Arcadia (Genesis)')")
    lib.commit()

    def links():
        return {(r[0], r[1], r[2]) for r in lib.execute(
            "SELECT game_id,provider,provider_id FROM metadata_links")}

    n = provider_links.sync(lib, cache, blocked_gids={4})
    lib.commit()
    got = links()

    check("SS identity becomes a link", (1, "screenscraper", "4321") in got)
    check("SGDB identity becomes a link", (1, "steamgriddb", "999") in got)
    check("a game matched by ONLY SGDB is linked (build_library could not do this)",
          (2, "steamgriddb", "555") in got)
    check("every entry of a title gets the link", (9, "screenscraper", "4321") in got)
    check("a recorded MISS never becomes a link",
          not any(g == 3 for g, _, _ in got))
    check("a blocked game is never linked", not any(g == 4 for g, _, _ in got))
    # counts are ROWS, not titles: arcadia has two entries, so it contributes two.
    check("sync reports what it wrote (rows, not titles)",
          n.get("steamgriddb") == 3 and n.get("screenscraper") == 2)

    # idempotent — the rebuild runs this every time
    provider_links.sync(lib, cache, blocked_gids={4})
    lib.commit()
    check("idempotent: no duplicate rows on a second sync", links() == got)
    check("no duplicates by count",
          lib.execute("SELECT COUNT(*) FROM metadata_links").fetchone()[0] == len(got))

    # a link that no longer has an identity behind it must go — otherwise a detach or a
    # corrected match leaves a dead provider icon on the page forever
    lib.execute("INSERT INTO metadata_links VALUES(1,'steamgriddb','111',NULL,'x')")
    lib.execute("DELETE FROM metadata_links WHERE game_id=1 AND provider='steamgriddb' "
                "AND provider_id='999'")
    provider_links.sync(lib, cache, blocked_gids={4})
    lib.commit()
    check("a stale link is replaced by the recorded identity",
          (1, "steamgriddb", "999") in links()
          and (1, "steamgriddb", "111") not in links())

    # urls are derived, never stored by hand in two shapes
    url = {r[0]: r[1] for r in lib.execute(
        "SELECT provider,url FROM metadata_links WHERE game_id=1")}
    check("SS url points at the matched id",
          url.get("screenscraper", "").endswith("gameinfos.php?gameid=4321"))
    check("SGDB url points at the matched id",
          url.get("steamgriddb", "").endswith("steamgriddb.com/game/999"))

    # IGDB is build_library's own (it applies bundle refusal and rename-on-match), so
    # sync must never OVERWRITE it — otherwise the two fight over the same rows.
    cc.execute("CREATE TABLE IF NOT EXISTS igdb_resolution(norm_key TEXT PRIMARY KEY, "
               "igdb_id INTEGER, slug TEXT, matched_by TEXT)")
    cc.execute("INSERT OR REPLACE INTO igdb_resolution(norm_key,igdb_id,slug,matched_by) VALUES('arcadia',7,NULL,'exact')")
    cc.commit()
    lib.execute("INSERT INTO metadata_links VALUES(1,'igdb','7',NULL,'u')")
    provider_links.sync(lib, cache, blocked_gids={4})
    lib.commit()
    check("an existing igdb link is left alone", (1, "igdb", "7") in links())

    # ...but an identity with no CACHED PAYLOAD must still produce a link. build_library
    # builds the IGDB link from `igdb_resolution JOIN igdb_meta`, so a game resolved by
    # the collection-member path — which records an id without fetching the payload —
    # rebuilt with no link at all. Live, that silently cost 31 titles (the whole SSI
    # gold-box run: Pool of Radiance, Champions of Krynn, Eye of the Beholder 2/3...),
    # which is the "why no IGDB link on collection members?" report.
    cc.execute("INSERT OR REPLACE INTO igdb_resolution(norm_key,igdb_id,slug,matched_by) VALUES('orphan',8732,NULL,"
               "'member_exact')")
    cc.execute("INSERT OR REPLACE INTO igdb_resolution(norm_key,igdb_id,slug,matched_by) VALUES('nomatch',0,NULL,'none')")
    cc.execute("INSERT OR REPLACE INTO igdb_resolution(norm_key,igdb_id,slug,matched_by) VALUES('blocked',111,NULL,"
               "'member_exact')")
    cc.commit()
    provider_links.sync(lib, cache, blocked_gids={4})
    lib.commit()
    lib.execute("ALTER TABLE games ADD COLUMN game_key TEXT")
    lib.execute("UPDATE games SET game_key='igdb:'||id")   # all identified for this part
    provider_links.sync(lib, cache, blocked_gids={4})
    lib.commit()
    check("a payload-less igdb identity still becomes a link",
          (2, "igdb", "8732") in links())
    check("filling igdb did not disturb the link build_library owns",
          (1, "igdb", "7") in links())
    check("an igdb miss is still not a link", not any(
        g == 3 and p == "igdb" for g, p, _ in links()))
    check("a blocked game gets no igdb fill either", not any(
        g == 4 for g, _, _ in links()))
    u = lib.execute("SELECT url FROM metadata_links WHERE game_id=2 AND provider='igdb'"
                    ).fetchone()[0]
    check("the filled igdb link has a usable url",
          (u or "").endswith("igdb.com/games/8732"))

    # an igdb link must not outlive the identity it asserts: `nomatch` has no
    # igdb_resolution row at all, so a link on it is a claim nothing backs.
    lib.execute("INSERT INTO metadata_links VALUES(3,'igdb','21032',NULL,'u')")
    provider_links.sync(lib, cache, blocked_gids={4})
    lib.commit()
    check("an igdb link with no resolution behind it is removed",
          not any(g == 3 and p == "igdb" for g, p, _ in links()))
    check("an igdb link that IS backed by a resolution survives",
          (2, "igdb", "8732") in links())
    check("build_library's own igdb link is still not disturbed",
          (1, "igdb", "7") in links())

    # Every recorded identity must be able to BECOME a link. IGDB satisfies that through
    # sync's dedicated fill path rather than a PAGE_URL template, because its url needs
    # the slug — so the guard is about coverage, not about which mechanism.
    uncovered = [p for p in provider_ids.PROVIDERS
                 if p not in provider_links.PAGE_URL and p != "igdb"]
    check("every provider_ids provider is covered by sync (%r)" % uncovered,
          not uncovered)
    check("...and igdb is covered by the fill path rather than forgotten",
          "igdb_resolution" in open(os.path.join(
              os.path.dirname(os.path.abspath(__file__)), "provider_links.py")).read())

    # An identity that BECOMES a miss must take its link with it. sync() only visited
    # games that still had an identity, so a corrected match that resolved to nothing
    # left the old link in place: live, `dune awakening` kept ScreenScraper 12706 (Dune:
    # Imperium) after the re-match had correctly decided SS does not have it. A link is
    # a claim about an identity; with no identity there is no claim.
    provider_ids.record(cc, "screenscraper", "arcadia", 0)     # was 4321, now a miss
    cc.commit()
    provider_links.sync(lib, cache, blocked_gids={4})
    lib.commit()
    check("a link whose identity became a miss is removed",
          not any(p == "screenscraper" and g in (1, 9) for g, p, _ in links()))
    check("the other provider's link is untouched by that",
          (1, "steamgriddb", "999") in links())
    provider_ids.record(cc, "screenscraper", "arcadia", 4321, name="Arcadia")
    cc.commit()
    provider_links.sync(lib, cache, blocked_gids={4})
    lib.commit()
    check("and it comes back when the identity does",
          (1, "screenscraper", "4321") in links())

    # ---- the regression itself: a full rebuild keeps the links -------------------
    # Simulate what build_library does — drop and recreate the table — then sync.
    lib.execute("DELETE FROM metadata_links")
    provider_links.sync(lib, cache, blocked_gids={4})
    lib.commit()
    after = {(g, p) for g, p, _ in links()}
    check("after a full table rebuild, SGDB links are back",
          ("steamgriddb", ) and (1, "steamgriddb") in after and (2, "steamgriddb") in after)
    check("after a full table rebuild, SS links are back", (1, "screenscraper") in after)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
