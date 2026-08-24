#!/usr/bin/env python3
"""SteamGridDB: ask once, ask for everything, and never cache a failure as a miss.

Three defects, all in the same twenty lines.

1. `_sgdb_game_id` sets `looked = True` only AFTER `_sgdb_get` RETURNS. SteamGridDB's
   normal answer for an appid it does not carry is a 404, which raises — so `looked`
   stayed False, the function raised "not a miss", and the caller (which passes no title
   for a Steam game) never negative-cached it. That appid was re-queried on every run,
   forever. Its own docstring describes the opposite intent.

   The mirror image is worse: if the appid call fails TRANSIENTLY but the title search
   cleanly misses, it returns None and the caller writes "SteamGridDB does not have this
   game" — which is exactly the defect the docstring says it fixed. The appid is the
   EXACT handle; if we could not ask it, we did not look.

2. The id is resolved by a live lookup every time, although `provider_ids` exists to
   cache precisely this and `server/app.py`'s match sweep already fills it. And
   `_sgdb_game_id` was called TWICE per game inside the work loop.

3. The work list skipped a game that already had cover/hero/logo from ANY provider,
   with the gate hand-listing three of the four kinds this function fetches — so a game
   missing only an `icon` could never get one. The endpoint's docstring promises it
   "pulls everything a matched provider holds"; for the per-game on-demand path that has
   to mean everything, and the gate kinds must be DERIVED from what is fetched.

Offline. Every HTTP call is a stub.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-sgdb-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import config                                                  # noqa: E402
import media_fetch                                             # noqa: E402
import media_index                                             # noqa: E402
import provider_ids                                            # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


CALLS = []
ROUTES = {}


def _stub_get(path, key):
    CALLS.append(path)
    a = ROUTES.get(path)
    if a is None:
        import urllib.error
        raise urllib.error.HTTPError("https://sgdb" + path, 404, "no", None, None)
    if isinstance(a, Exception):
        raise a
    return a


def main():
    media_fetch._sgdb_get = _stub_get
    config.steamgriddb_key = lambda: "k"

    print("1. a 404 for an appid is a LOOK, not a failure")
    del CALLS[:]
    ROUTES.clear()
    got = media_fetch._sgdb_game_id("k", appid="999", title=None)
    check("it answers 'no such game' instead of raising", got is None)

    print("2. an appid we could NOT ask is never reported as a miss")
    del CALLS[:]
    ROUTES.clear()
    ROUTES["/games/steam/999"] = OSError("connection reset")
    ROUTES["/search/autocomplete/Some%20Game"] = {"data": []}
    raised = False
    try:
        media_fetch._sgdb_game_id("k", appid="999", title="Some Game")
    except RuntimeError:
        raised = True
    check("a transient appid error + a clean title miss RAISES, never returns None",
          raised)

    print("3. a real answer is still a real answer")
    ROUTES["/games/steam/999"] = {"data": {"id": 4242}}
    check("the appid resolves", media_fetch._sgdb_game_id("k", "999", None) == 4242)

    print("4. the id comes from the shared identity cache, not a fresh lookup")
    mc = sqlite3.connect(os.path.join(DATA, "metadata-cache.sqlite"))
    provider_ids.ensure_tables(mc)
    provider_ids.record(mc, "steamgriddb", "team fortress 2", 7777, "Team Fortress 2",
                        "steam_appid")
    mc.commit()
    mc.close()

    ROUTES.clear()
    ROUTES["/grids/game/7777?dimensions=600x900"] = {"data": [
        {"url": "https://cdn.sgdb/grid/a.png"},
        {"url": "https://cdn.sgdb/grid/b.png"},
        {"url": "https://cdn.sgdb/grid/c"},          # dotless: ext must not come from it
    ]}
    ROUTES["/heroes/game/7777"] = {"data": [{"url": "https://cdn.sgdb/hero/a.jpg"}]}
    ROUTES["/logos/game/7777"] = {"data": [{"url": "https://cdn.sgdb/logo/a.png"}]}
    ROUTES["/icons/game/7777"] = {"data": [{"url": "https://cdn.sgdb/icon/a.png"}]}

    con = media_index.index_con()
    # the game already holds a cover, a hero and a logo from another provider — the old
    # gate skipped it entirely, so its missing ICON could never arrive.
    for k in ("cover", "hero", "logo"):
        con.execute("INSERT INTO media(norm_key,system,kind,provider,ref_type,ref,ext,"
                    "matched,indexed_at) VALUES('team fortress 2','',?,'igdb','url',?,"
                    "'jpg',1,0)", (k, "https://igdb/%s.jpg" % k))
    con.commit()
    del CALLS[:]
    media_fetch.fetch_steamgriddb_targets(
        con, 1, [("team fortress 2", "Team Fortress 2", "440")])
    con.commit()
    check("no game lookup was made — the cached id was used",
          not [c for c in CALLS if c.startswith("/games/") or c.startswith("/search/")])
    kinds = {r[0] for r in con.execute(
        "SELECT kind FROM media WHERE provider='steamgriddb'")}
    check("the missing icon finally arrives", "icon" in kinds)

    print("5. more than one candidate per kind, so the ranker has a choice")
    covers = [r[0] for r in con.execute(
        "SELECT ref FROM media WHERE provider='steamgriddb' AND kind='cover'")]
    check("several grids are indexed, not just items[0] (%d)" % len(covers),
          len(covers) > 1)
    exts = {r[0] for r in con.execute(
        "SELECT ext FROM media WHERE provider='steamgriddb'")}
    check("no url tail became an extension (%r)" % exts,
          all(e in media_ext_ok() for e in exts))

    print("6. a lookup this path DOES make is written back to the shared cache")
    ROUTES["/games/steam/220"] = {"data": {"id": 8888}}
    ROUTES["/grids/game/8888?dimensions=600x900"] = {"data": [
        {"url": "https://cdn.sgdb/grid/hl.png"}]}
    media_fetch.fetch_steamgriddb_targets(con, 2, [("half life 2", "Half-Life 2", "220")])
    con.commit()
    mc = sqlite3.connect(os.path.join(DATA, "metadata-cache.sqlite"))
    cached = provider_ids.cached(mc, "steamgriddb", "half life 2")
    mc.close()
    check("the id it resolved is no longer thrown away",
          cached is not None and cached[0] == 8888)

    print("7. an id another game already holds fetches nothing")
    # A title search's nearest-record guess is how two games come to share one id
    # (Hammerwatch II / Heroes of Hammerwatch II, live). provider_ids refuses it; the art
    # must be refused with it, or the other game's cover lands on this one.
    ROUTES["/search/autocomplete/Clone%20Game"] = {"data": [{"id": 8888,
                                                             "name": "Clone Game"}]}
    ROUTES["/grids/game/8888?dimensions=600x900"] = {"data": [
        {"url": "https://cdn.sgdb/grid/clone.png"}]}
    media_fetch.fetch_steamgriddb_targets(con, 3, [("clone game", "Clone Game", None)])
    con.commit()
    check("no art was taken under the colliding id",
          not con.execute("SELECT 1 FROM media WHERE norm_key='clone game'").fetchone())
    con.close()

    print("\nRESULT: %d checks, all passed" % len(PASS))


def media_ext_ok():
    import media
    return media.SAFE_EXTS


if __name__ == "__main__":
    main()
