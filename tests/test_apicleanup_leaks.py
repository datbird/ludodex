#!/usr/bin/env python3
"""Handles that outlived their scope, and reads that were wider than their caller.

  * `_steam_canon_map` opened steam-meta.sqlite READ-WRITE and put its close() inside the
    try, so the very error it is written to tolerate — an OperationalError from the
    store_name query against a pre-migration cache — leaked the handle. It runs on both
    the collection scan and the collection apply path.
  * `_media_worker` closed the media index only on the success path, so every failed
    download leaked one for the life of the process.
  * `_match_worker` opened metadata-cache.sqlite READ-ONLY and only then called
    `provider_ids.ensure_tables` — which is what CREATES that file. On a fresh install the
    first non-forced provider match therefore died with "unable to open database file",
    and the write connection it handed ensure_tables was never closed.
  * `_fetch_media_for` reported `res["fetched"]` as `web_added`. `fetched` counts GAMES
    touched, always 1 here, so the refresh-media response claimed one web image had been
    added on every single call, web pass or not.
  * The AI art step read `SELECT norm_key, canonical_title FROM games` with no WHERE, so a
    wand run on ONE game loaded every title in the catalog to use one of them.
  * `banned_media` ran one title lookup per banned row.

Offline: sqlite fixtures in an isolated data dir, and counting wrappers around
sqlite3.connect. No provider is contacted — every network-touching step is stubbed, and
what is asserted is the SHAPE of the database work around it, which is where the bugs
were. Coverage stops at the provider boundary on purpose: a real IGDB/ScreenScraper call
cannot run in an offline test.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-apicleanup-l-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


class Tracker:
    """Wraps sqlite3.connect so a test can see which handles were never closed."""

    def __init__(self):
        self.open = []
        self._real = sqlite3.connect

    def __enter__(self):
        tr = self

        class Con(sqlite3.Connection):
            def close(self):
                if self in tr.open:
                    tr.open.remove(self)
                sqlite3.Connection.close(self)

        def connect(*a, **kw):
            kw.setdefault("factory", Con)
            c = tr._real(*a, **kw)
            tr.open.append(c)
            return c

        sqlite3.connect = connect
        return self

    def __exit__(self, *e):
        sqlite3.connect = self._real
        for c in list(self.open):
            try:
                sqlite3.Connection.close(c)
            except Exception:                                  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
def _steam_meta(cols, row):
    p = os.path.join(DATA, "steam-meta.sqlite")
    if os.path.exists(p):
        os.remove(p)
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE steam_meta(%s)" % ", ".join("%s TEXT" % c for c in cols))
    con.execute("INSERT INTO steam_meta VALUES(%s)" % ",".join("?" * len(row)), row)
    con.commit()
    con.close()


def part1():
    print("1. _steam_canon_map closes its handle on every pre-migration shape")
    # (a) no canonical_appid at all: the FIRST query raises, and the guard that
    # tolerates it is the OUTER except — which the close used to sit inside.
    _steam_meta(["appid"], ("1",))
    with Tracker() as tr:
        canon, names = app._steam_canon_map()
        leaked_a = len(tr.open)
    check("a cache with no canonical_appid still answers", canon == {})
    check("and leaves no connection open", leaked_a == 0)

    # (b) canonical_appid but no store_name: the inner except's path
    _steam_meta(["appid", "canonical_appid"], ("1", "1"))
    with Tracker() as tr:
        canon, names = app._steam_canon_map()
        leaked_b = len(tr.open)
    check("a cache with no store_name still answers", names == {})
    check("and leaves no connection open either", leaked_b == 0)


def part2():
    print("\n2. _media_worker closes the media index when the job FAILS")
    app._MEDIA_JOB["job"] = {"running": True, "finished": False, "mode": "chosen",
                             "step": "", "ok": None, "downloaded": 0, "dead": 0}
    import media_choose
    real = media_choose.select

    def boom(*a, **kw):
        raise RuntimeError("provider exploded")

    media_choose.select = boom
    try:
        with Tracker() as tr:
            app._media_worker("chosen")
            leaked = len(tr.open)
    finally:
        media_choose.select = real
    check("the failure is reported", app._MEDIA_JOB["job"]["ok"] is False)
    check("the job is marked finished", app._MEDIA_JOB["job"]["finished"] is True)
    check("and the index connection is closed anyway", leaked == 0)


def part3():
    print("\n3. _match_worker survives a fresh install (no metadata cache yet)")
    mcp = os.path.join(DATA, "metadata-cache.sqlite")
    if os.path.exists(mcp):
        os.remove(mcp)
    app._MATCH_JOB["job"] = {"running": True, "finished": False, "step": "",
                             "ok": None, "error": None, "matched": {}, "done": 0,
                             "total": 0, "cancel": False}
    real_creds = app.config.screenscraper_creds
    real_key = app.config.steamgriddb_key
    real_mp = app._match_providers
    app.config.screenscraper_creds = lambda: {"devid": "x", "devpassword": "y",
                                              "ssid": "z", "sspassword": "w"}
    app.config.steamgriddb_key = lambda: "k"
    app._match_providers = lambda *a, **kw: {"screenscraper": 0, "steamgriddb": 0}
    try:
        with Tracker() as tr:
            app._match_worker(False)
            leaked = len(tr.open)
    finally:
        app.config.screenscraper_creds = real_creds
        app.config.steamgriddb_key = real_key
        app._match_providers = real_mp
    j = app._MATCH_JOB["job"]
    check("it does not die on the missing cache", j["ok"] is True)
    check("no 'unable to open database file'",
          "unable to open database" not in str(j.get("error") or ""))
    check("the cache was CREATED before it was read", os.path.exists(mcp))
    check("and ensure_tables' write handle is closed", leaked == 0)


def part4():
    print("\n4. web_added counts web images, not games touched")
    calls = []

    def fake_pull(con, nk, want_web=False, provider=None, kinds=None,
                  already_matched=False):
        calls.append(already_matched)
        return 3 if want_web else 0

    real_pull, real_fin, real_match = (app._pull_media_sources, app._media_finish,
                                       app._match_providers)
    app._pull_media_sources = fake_pull
    app._media_finish = lambda keys, **kw: {"measured": 0, "pruned": 0}
    app._match_providers = lambda *a, **kw: {"screenscraper": 0, "steamgriddb": 0}
    try:
        res = app._enrich_media(["g1"], web=False)
        check("no web pass means no web images claimed", res["web_added"] == 0)
        check("but the game was still touched", res["fetched"] == 1)
        res = app._enrich_media(["g1"], web=True)
        check("a web pass reports what it actually added", res["web_added"] == 3)
        check("the batch match ran, so the per-game one is skipped",
              calls and calls[-1] is True)

        real_enrich = app._enrich_media
        app._enrich_media = lambda keys, web=False, **kw: {
            "fetched": 1, "web_added": 3 if web else 0}
        try:
            out = app._fetch_media_for("g1", want_web=False)
            check("refresh-media no longer claims a web image on every call",
                  out["web_added"] == 0)
            out = app._fetch_media_for("g1", want_web=True)
            check("and reports the real count when the web pass ran",
                  out["web_added"] == 3)
        finally:
            app._enrich_media = real_enrich
    finally:
        (app._pull_media_sources, app._media_finish,
         app._match_providers) = real_pull, real_fin, real_match


def part5():
    print("\n5. reads are scoped to the caller's keys")
    lc = sqlite3.connect(app.LIBRARY_DB)
    lc.execute("DELETE FROM games")
    for i in range(50):
        nk = "bulk%d" % i
        lc.execute("INSERT INTO games(canonical_title,norm_key,platform,entry_key,"
                   "base_key,game_key,n_sources,n_kinds,sources_summary,wanted) "
                   "VALUES(?,?,'pc',?,?,?,1,0,'steam',0)",
                   (nk.title(), nk, nk + "@pc", nk, "title:" + nk))
    lc.commit()
    lc.close()

    seen = {}
    real_pull, real_fin, real_match = (app._pull_media_sources, app._media_finish,
                                       app._match_providers)
    real_adj, real_mark, real_done = (app._ai_adjudicate_game, app._mark_art_adjudicated,
                                      app._art_adjudicated)
    app._pull_media_sources = lambda *a, **kw: 0
    app._media_finish = lambda keys, **kw: {"measured": 0, "pruned": 0}
    app._match_providers = lambda *a, **kw: {"screenscraper": 0, "steamgriddb": 0}
    app._art_adjudicated = lambda nk, scope="all": False
    app._mark_art_adjudicated = lambda nk, now: None
    app._ai_adjudicate_game = lambda nk, title: seen.update({nk: title})
    try:
        res = app._enrich_media(["bulk7"], ai_art=True)
        check("the AI art step ran for the one key", res["adjudicated"] == 1)
        check("and it got that game's real title", seen == {"bulk7": "Bulk7"})
        src = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read()
        check("the title lookup is no longer a whole-catalog SELECT",
              'canonical_title FROM games")' not in src)
    finally:
        (app._pull_media_sources, app._media_finish,
         app._match_providers) = real_pull, real_fin, real_match
        (app._ai_adjudicate_game, app._mark_art_adjudicated,
         app._art_adjudicated) = real_adj, real_mark, real_done


def part6():
    print("\n6. banned_media resolves every title in ONE lookup")
    import mediaflags
    for i in range(6):
        mediaflags.ban("bulk%d" % i, "cover", "igdb", "ref%d" % i)
    lc = sqlite3.connect(app.LIBRARY_DB)
    lc.execute("DELETE FROM games WHERE norm_key='bulk3'")     # a ban with no game row
    lc.commit()
    lc.close()

    n = [0]
    real_lib = app.lib

    class Counting:
        """Passes everything through, counting the TITLE lookups."""

        def __init__(self, con):
            self._c = con

        def execute(self, sql, *a):
            if "canonical_title" in sql:
                n[0] += 1
            return self._c.execute(sql, *a)

        def __getattr__(self, k):
            return getattr(self._c, k)

    app.lib = lambda: Counting(real_lib())
    try:
        out = app.banned_media()["banned"]
    finally:
        app.lib = real_lib
    check("every banned row comes back", len(out) == 6)
    check("titles are resolved", any(b["title"] == "Bulk0" for b in out))
    check("a ban whose game is gone falls back to the key",
          any(b["title"] == "bulk3" for b in out))
    check("ONE title query for the whole list, not one per row", n[0] == 1)


def part7():
    print("\n7. ops_status reports the bind it is actually serving on")
    class Req:
        scope = {"server": ("127.0.0.1", 9123)}
    st = app.ops_status(Req())["services"][0]
    check("the port comes from the ASGI scope, not a constant", st["port"] == 9123)
    check("and so does the host", st["host"] == "127.0.0.1")

    class NoSrv:
        scope = {}
    st = app.ops_status(NoSrv())["services"][0]
    check("an unknown bind is reported as unknown, never invented",
          st["port"] is None and st["host"] is None)


def part8b():
    print("\n9. devices._mgr_rom_count closes its handle on a half-written index")
    import devices
    idx = os.path.join(DATA, "roms-index-mgr7.sqlite")
    con = sqlite3.connect(idx)          # created, but the `roms` table does not exist yet
    con.execute("CREATE TABLE placeholder(x)")
    con.commit()
    con.close()
    with Tracker() as tr:
        got = devices._mgr_rom_count(7)
        leaked = len(tr.open)
    check("an unscanned index reads as 'not scanned yet'", got == (None, None))
    check("and leaves no connection open", leaked == 0)


def part8():
    print("\n8. the apply-path 'instant media' reconcile actually runs")
    # A leftover `del now` bound `now` as a LOCAL, so the first statement of
    # _reconcile_media_now raised UnboundLocalError on every call — and all four call
    # sites swallow exceptions, so an apply/pin silently did no instant media at all.
    ran = []
    real = app._enrich_media
    app._enrich_media = lambda keys, **kw: ran.append(sorted(keys)) or {}
    try:
        app._reconcile_media_now({"g1", "g2"})
    finally:
        app._enrich_media = real
    check("it reaches the media pipeline instead of raising", ran == [["g1", "g2"]])
    check("and an empty set is still a no-op",
          app._reconcile_media_now(set()) is None)


def main():
    print("audit cleanup: handles, scopes and honest counts")
    part1()
    part2()
    part3()
    part4()
    part5()
    part6()
    part7()
    part8()
    part8b()
    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
