#!/usr/bin/env python3
"""One card per game, and the counts that go with it.

Six Dark Souls rows in the live catalog were six tiles: two platforms of one game, an
unmatched edition, and two more editions filed as their own titles. Grouping on
card_key makes them three. The properties that must survive the grouping are the ones
the per-platform refactor bought in the first place, so they are pinned here too.
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
    CREATE TABLE sco.game_scores(norm_key TEXT, universal REAL);
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

    # the six live rows, with the card_key the fold produces
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
    keys = [it["card_key"] for it in res["items"]]
    check("six entries become three cards", len(res["items"]) == 3)
    check("the total counts cards, not entries", res["total"] == 3)
    check("no card key repeats", len(set(keys)) == len(keys))
    check("Dark Souls is one card", keys.count("igdb:2155") == 1)
    check("Dark Souls II is its own card", "igdb:2368" in keys)
    check("Dark Souls III is its own card", "igdb:11133" in keys)

    ds = [it for it in res["items"] if it["card_key"] == "igdb:2155"][0]
    check("the card unions its platforms",
          set(ds["platforms"].split(",")) == {"pc", "switch"})
    check("the card sums its sources", ds["n_sources"] == 3)
    check("the card carries an addressable entry_key",
          ds["entry_key"] in ("dark souls@pc", "dark souls@switch",
                              "dark souls prepare to die@pc"))

    # determinism: the representative must not move between identical queries
    again = srv._query_games(con, limit=100)
    ds2 = [it for it in again["items"] if it["card_key"] == "igdb:2155"][0]
    check("the representative is deterministic", ds["entry_key"] == ds2["entry_key"])

    # a filter still narrows to cards, not to entries
    sw = srv._query_games(con, platform="switch", limit=100)
    check("a platform filter returns the whole card", len(sw["items"]) == 1)
    check("and it is the Dark Souls card", sw["items"][0]["card_key"] == "igdb:2155")

    # an un-rebuilt catalog (no card_key column) must still serve
    old = sqlite3.connect(":memory:")
    check("degrades without the column", srv._has_col(old, "games", "card_key") is False)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
