#!/usr/bin/env python3
"""Every configured provider is matched for every game (#21b, #21c).

datbird, three times now: *"I'm not asking for either steamgriddb or SS to be 'majority
provider'. I'm asking for them both to be A PROVIDER"* — and *"even if there is no
metadata or media being taken from a provider, I still want it actually matched"*.

What shipped earlier was #21(a): ScreenScraper eligibility following `games.platform`
instead of `sources.source`, which had made SS unreachable for a store-only library.
That made SS *media pulls* possible. It did not make provider MATCHING happen, and those
were reported as the same thing. They are not:

  * SS identity existed only as a side effect of `_pull_ss_media` — 151/2255 entries.
  * SGDB identity existed only inside `fetch_steamgriddb_targets`, whose work list SKIPS
    any game that already has a cover/hero/logo — so a game with IGDB art never got an id.
    0 links, against 177 entries holding SGDB media.

These pin the property directly: the identity is recorded, and the link written, for a
game that needs no art at all.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-matchprov-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    from server import app as srv
    import media_fetch as _mf

    lib = sqlite3.connect(srv.LIBRARY_DB)
    cur = lib.execute(
        "INSERT INTO games(canonical_title,norm_key,platform,entry_key,base_key,"
        "game_key,n_sources,n_kinds,sources_summary,wanted) "
        "VALUES('System Shock: Classic','system shock classic','pc',"
        "'system shock classic@pc','system shock classic','igdb:23',1,0,'steam',0)")
    gid = cur.lastrowid
    lib.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw,state) "
                "VALUES(?,'steam','pc','410710','System Shock: Classic','have')", (gid,))
    lib.commit()
    lib.close()

    # config has to look configured or the pass short-circuits
    srv.config.set_("steamgriddb_api_key", "test-key")

    real_ss, real_sgdb = srv._ss_match, _mf._sgdb_game_id
    seen = {"ss": 0, "sgdb": 0}

    def fake_ss(queries, systems, year=None):
        seen["ss"] += 1
        return {"provider": "screenscraper", "ss_id": 8123, "name": "System Shock"}

    def fake_sgdb(key, appid=None, title=None):
        seen["sgdb"] += 1
        return 5155

    srv._ss_match = fake_ss
    _mf._sgdb_game_id = fake_sgdb
    try:
        print("1. a game with NO art still gets matched on every provider")
        got = srv._match_providers(["system shock classic"])
        check("screenscraper matched", got.get("screenscraper") == 1)
        check("steamgriddb matched", got.get("steamgriddb") == 1)

        print("2. the link the Matched-providers menu reads is written")
        lib = sqlite3.connect(srv.LIBRARY_DB)
        links = {r[0]: (r[1], r[2]) for r in lib.execute(
            "SELECT provider, provider_id, url FROM metadata_links WHERE game_id=?",
            (gid,))}
        lib.close()
        check("screenscraper link exists", links.get("screenscraper", ("",))[0] == "8123")
        check("steamgriddb link exists", links.get("steamgriddb", ("",))[0] == "5155")
        check("each link carries a page URL",
              all(v[1] for v in links.values()))

        print("3. it is idempotent and does not re-search a decided game")
        before = dict(seen)
        srv._match_providers(["system shock classic"])
        check("no second screenscraper search", seen["ss"] == before["ss"])
        check("no second steamgriddb search", seen["sgdb"] == before["sgdb"])
        lib = sqlite3.connect(srv.LIBRARY_DB)
        n = lib.execute("SELECT COUNT(*) FROM metadata_links WHERE game_id=? AND "
                        "provider='screenscraper'", (gid,)).fetchone()[0]
        lib.close()
        check("the link is not duplicated", n == 1)
    finally:
        srv._ss_match, _mf._sgdb_game_id = real_ss, real_sgdb

    print("4. every media path records identity, not just the ones that take art")
    # The reason SGDB had zero links is that its identity lived inside a fetcher that
    # skips games which already have art. Matching must not be reachable only through a
    # code path whose job is to download something.
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "server", "app.py"), encoding="utf-8").read()
    for fn in ("def _pull_media_sources(", "def _scoped_media_reconcile("):
        i = src.index(fn)
        body = src[i:i + 4000]
        check("%s calls _match_providers" % fn.split("(")[0].replace("def ", ""),
              "_match_providers(" in body)

    print("5. the IMPORT path does too — the one that was still missing it")
    # This is the gap that mattered most and was found last. `_match_providers` was wired
    # into the wand, the apply and the scoped reconcile — but a first-run import runs
    # `_sync_worker`, whose media steps are `media_fetch.py` SUBPROCESSES that cannot
    # reach a function living in the server. So a clean ingest produced IGDB + Steam and
    # nothing else, and would have done so again on every reset, no matter how many
    # times the other three paths were fixed.
    i = src.index("def _sync_worker(")
    j = src.index("\ndef ", i + 10)
    worker = src[i:j]
    # `_parallel_match` IS the matcher — it fans `_match_providers` across a pool.
    # The guard is about the import matching providers at all, not about which spelling.
    def _match_at(text):
        """Where the import matches providers, by either spelling — `_parallel_match`
        fans `_match_providers` across a pool and is the same step."""
        for name in ("_parallel_match(", "_match_providers("):
            if name in text:
                return text.index(name)
        raise AssertionError("the import does not match providers at all")

    check("_sync_worker matches providers",
          "_match_providers(" in worker or "_parallel_match(" in worker)
    check("it does so BEFORE the media passes, so the fetchers have an identity to use",
          _match_at(worker) < worker.index("for sid in media_targets:"))
    check("and it is a declared phase, visible in the job monitor",
          '"id": "provmatch"' in src)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
