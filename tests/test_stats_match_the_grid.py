#!/usr/bin/env python3
"""Every dashboard number counts what the library view it opens shows.

Found by the live browser suite (tests/browser/ui_full.py): /api/stats reported 1,829
games while the library showed 1,794; "Cover undecided" said 10 and its view showed 8;
Wanted said 595 and the view showed 592. The grid (`_query_games`) leaves out the
entries "Hide non-games" hides and the add-ons filed under an owned base game. The
stats endpoint applied neither, so every card read a little high.

The rule now lives in ONE place, `_view_hidden_where`, and both sides take it from
there. This seeds a catalog with a tool, an add-on and a wishlist tool beside real
games, and asserts each stats number equals the total of the view it links to, with
the setting on and with it off.

One deliberate difference: the dashboard counts GAMES, so an add-on whose base game is
not owned (listed in the grid, since there is nowhere to file it) is still not counted.

Offline: an isolated data dir and the empty catalog the server seeds on first run.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-stats-grid-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402
import config                                                  # noqa: E402

PASS = []


def check(label, cond, detail=None):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: %s%s" % (label, "" if detail is None else "  (%r)" % (detail,)))


# norm_key, wanted, parent_key, is a non-game, has two all-flagged covers (undecided)
GAMES = [
    ("doom", 0, None, False, True),
    ("quake", 0, None, False, False),
    ("quake mission pack", 0, "quake", False, False),    # an add-on under an owned base
    ("wallpaper engine", 0, None, True, True),           # a tool, undecided covers too
    ("fps monitor", 1, None, True, False),               # a WISHED tool
    ("hexen", 1, None, False, False),                    # a wished game
    ("quake ii ground zero", 0, None, False, False),     # an add-on, base NOT owned
]
ORPHAN_ADDON = "quake ii ground zero"


def seed():
    lc = sqlite3.connect(app.LIBRARY_DB)
    lc.execute("ALTER TABLE games ADD COLUMN parent_key TEXT")
    lc.execute("ALTER TABLE games ADD COLUMN content_kind TEXT")
    for i, (nk, wanted, parent, _ng, _und) in enumerate(GAMES, 1):
        lc.execute("INSERT INTO games(id,canonical_title,norm_key,platform,entry_key,"
                   "base_key,game_key,n_sources,n_kinds,sources_summary,wanted,has_steam,"
                   "parent_key) VALUES(?,?,?,'pc',?,?,?,1,0,'steam',?,1,?)",
                   (i, nk.title(), nk, nk + "@pc", nk, "igdb:%d" % i, wanted, parent))
        lc.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw,state)"
                   " VALUES(?,'steam','pc',?,?,?)",
                   (i, str(i), nk, "want" if wanted else "have"))
        lc.execute("INSERT INTO metadata_links(game_id,provider,provider_id) "
                   "VALUES(?,'igdb',?)", (i, str(i)))
        if wanted:
            lc.execute("INSERT INTO wanted(game_id,store,store_id,title_raw) "
                       "VALUES(?,'steam',?,?)", (i, str(i), nk))
    lc.execute("UPDATE games SET content_kind='expansion' WHERE norm_key IN (?,?)",
               (ORPHAN_ADDON, "quake mission pack"))
    lc.commit()
    lc.close()
    sc = app._scores_con()
    for nk, _w, _p, ng, _u in GAMES:
        if ng:
            sc.execute("INSERT INTO store_type(norm_key,source,type,updated) "
                       "VALUES(?,'steam','application',0)", (nk,))
    sc.commit()
    sc.close()
    mc = sqlite3.connect(app.INDEX_DB)
    n = 0
    for nk, _w, _p, _ng, und in GAMES:
        if und:
            for _ in range(2):
                n += 1
                mc.execute("INSERT INTO media(norm_key,system,game_key,kind,provider,ref,"
                           "ref_type,ext,chosen,filler) "
                           "VALUES(?,'',NULL,'cover','igdb',?,'url','jpg',0,1)",
                           (nk, "ref-%d" % n))
    mc.commit()
    mc.close()


def view_total(**kw):
    con = app.lib()
    try:
        return app._query_games(con, limit=100, **kw)["total"]
    finally:
        con.close()


def compare(hide):
    config.set_("hide_non_games", "1" if hide else "0")
    st = app.stats()
    owned = view_total()
    check("the grid still lists the add-on whose base is not owned",
          view_total(q="ground zero") == 1)
    check("Games (identified) == the default library view, less that add-on",
          st["identified"] == owned - 1, (st["identified"], owned))
    check("and stats says how many add-ons it left out", st["addons"] == 1, st["addons"])
    wanted = view_total(status="wanted")
    check("Wanted == the Wanted view", st["wanted"] == wanted, (st["wanted"], wanted))
    und = view_total(include=["cover_undecided"])
    check("Cover undecided == its view", st["cover_undecided"] == und,
          (st["cover_undecided"], und))
    steam = view_total(include=["steam"])
    check("by_source steam == the Steam filter, less that add-on",
          st["by_source"]["steam"] == steam - 1, (st["by_source"]["steam"], steam))
    return st


def main():
    seed()

    print("1. Hide non-games ON (the default)")
    st = compare(True)
    check("the tool and the add-on are out of the Games count", st["identified"] == 2,
          st["identified"])
    check("the wished tool is out of Wanted", st["wanted"] == 1, st["wanted"])
    check("only doom is cover-undecided (the tool's covers are hidden with it)",
          st["cover_undecided"] == 1, st["cover_undecided"])

    print("2. Hide non-games OFF")
    st = compare(False)
    check("the tool is counted again, the add-on still is not", st["identified"] == 3,
          st["identified"])
    check("the wished tool is back in Wanted", st["wanted"] == 2, st["wanted"])

    print("3. one rule, not two copies")
    src = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read()
    stats_src = src[src.index("def stats():"):src.index("def facets():")]
    qg_src = src[src.index("def _query_games("):src.index("def _spotlight_rows(")]
    check("/api/stats takes the rule from _view_hidden_where",
          "_view_hidden_where(con" in stats_src)
    check("and so does _query_games", "_view_hidden_where(con, status)" in qg_src)
    check("neither writes the non-game rule out by hand",
          "_non_game_hidden_sql()" not in stats_src
          and qg_src.count("_non_game_hidden_sql()") == 1)   # the 'utilities' inverse

    config.set_("hide_non_games", "1")
    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
