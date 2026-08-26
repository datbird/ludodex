#!/usr/bin/env python3
"""Every count in the app counts CARDS, and Spotlight groups the way the grid does.

A collapsed grid with an entry-counting stats card is worse than either, because the
number on the dashboard stops matching the number of tiles below it. Spotlight already
collapsed by resolved identity, which is most of the way there: what it must not do is
group by a DIFFERENT key from the grid, or the showcase offers a game the grid shows
once as two tiles.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def fixture():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
      platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, card_key TEXT,
      n_sources INTEGER DEFAULT 1, n_kinds INTEGER DEFAULT 1, sources_summary TEXT,
      has_emulation INT DEFAULT 0, wanted INT DEFAULT 0, parent_key TEXT,
      content_kind TEXT);
    CREATE TABLE sources(game_id INTEGER, source TEXT, platform TEXT,
                         state TEXT DEFAULT 'have');
    CREATE TABLE metadata_links(game_id INTEGER, provider TEXT);
    CREATE TABLE game_attributes(game_id INTEGER, kind TEXT, value TEXT);
    CREATE TABLE game_tags(game_id INTEGER, origin TEXT, tag TEXT);
    CREATE TABLE wanted(game_id INTEGER, store TEXT, store_id TEXT, title_raw TEXT);
    """)
    con.execute("ATTACH DATABASE ':memory:' AS m")
    con.execute("ATTACH DATABASE ':memory:' AS u")
    con.execute("ATTACH DATABASE ':memory:' AS t")
    con.execute("ATTACH DATABASE ':memory:' AS sco")
    con.execute("ATTACH DATABASE ':memory:' AS ov")
    con.executescript("""
    CREATE TABLE m.media(norm_key TEXT, system TEXT, kind TEXT, chosen INT,
                         sha1 TEXT, game_key TEXT);
    CREATE TABLE u.user_media(norm_key TEXT, kind TEXT, sha1 TEXT, created INT);
    CREATE TABLE t.user_tags(norm_key TEXT, tag TEXT);
    CREATE TABLE sco.game_scores(norm_key TEXT, universal REAL, critic REAL, user REAL);
    CREATE TABLE sco.steam_type(norm_key TEXT, type TEXT);
    CREATE TABLE ov.overrides(norm_key TEXT, kind TEXT, value TEXT);
    """)

    n = [0]

    def game(title, nk, plat, gk, ck):
        n[0] += 1
        con.execute("INSERT INTO games(id,canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,card_key,sources_summary) "
                    "VALUES(?,?,?,?,?,?,?,?,'steam')",
                    (n[0], title, nk, plat, "%s@%s" % (nk, plat), nk, gk, ck))
        con.execute("INSERT INTO sources(game_id,source,platform) VALUES(?,'steam',?)",
                    (n[0], plat))
        con.execute("INSERT INTO metadata_links(game_id,provider) VALUES(?,'igdb')",
                    (n[0],))
        con.execute("INSERT INTO sco.game_scores(norm_key,universal,critic,user) "
                    "VALUES(?,80,80,80)", (nk,))

    game("DARK SOULS: REMASTERED", "dark souls", "pc", "igdb:81085", "igdb:2155")
    game("DARK SOULS: REMASTERED", "dark souls", "switch", "igdb:81085", "igdb:2155")
    game("DARK SOULS: Prepare To Die Edition", "dark souls prepare to die", "pc",
         "title:dark souls prepare to die", "igdb:2155")
    game("Dark Souls II", "dark souls 2", "pc", "igdb:2368", "igdb:2368")
    game("Dark Souls II: Scholar of the First Sin",
         "dark souls 2 scholar of the first sin", "pc", "igdb:8222", "igdb:2368")
    game("DARK SOULS III", "dark souls 3", "pc", "igdb:11133", "igdb:11133")
    con.commit()
    return con


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    from server import app as srv

    con = fixture()

    res = srv._query_games(con, limit=100)
    check("the grid shows three cards", len(res["items"]) == 3)
    check("the total agrees with the grid", res["total"] == len(res["items"]))

    # The facets endpoint opens its own connection through lib(), so the SQL it must
    # now use is asserted directly. This is the exact expression the implementation
    # adopts, and the point of the last check is that it is not the same number.
    pc = con.execute(
        "SELECT COUNT(DISTINCT COALESCE(g.card_key, g.entry_key)) FROM games g "
        "WHERE EXISTS(SELECT 1 FROM sources s WHERE s.game_id=g.id AND s.platform='pc')"
    ).fetchone()[0]
    check("the pc facet counts cards, not entries", pc == 3)
    sw = con.execute(
        "SELECT COUNT(DISTINCT COALESCE(g.card_key, g.entry_key)) FROM games g "
        "WHERE EXISTS(SELECT 1 FROM sources s WHERE s.game_id=g.id "
        "AND s.platform='switch')").fetchone()[0]
    check("the switch facet counts one card", sw == 1)
    entries_pc = con.execute(
        "SELECT COUNT(*) FROM games g WHERE EXISTS(SELECT 1 FROM sources s "
        "WHERE s.game_id=g.id AND s.platform='pc')").fetchone()[0]
    check("and that is genuinely different from counting entries", entries_pc == 5)

    spot = srv._spotlight_rows(con, "", [], limit=10)
    keys = [(r["card_key"] if "card_key" in r.keys() else r["entry_key"]) for r in spot]
    check("spotlight returns something", len(keys) > 0)
    check("spotlight never offers one card twice", len(set(keys)) == len(keys))
    check("spotlight offers at most one row per card", len(keys) <= 3)
    check("spotlight groups the way the grid does",
          set(keys) <= {it["card_key"] for it in res["items"]})

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
