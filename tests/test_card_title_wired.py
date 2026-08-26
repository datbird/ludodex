#!/usr/bin/env python3
"""The card's TITLE reaches the grid, not just the module that computes it.

cardkey.card_title() was written and unit-tested, then the query never called it, so the
first live rebuild produced a correctly collapsed Dark Souls card labelled "DARK SOULS:
Prepare To Die Edition". The card was right and its name was an edition's. A rule that
exists but is not wired is the same as no rule, so this pins the wiring, not the rule.
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


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    from server import app as srv

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
      platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, card_key TEXT,
      card_title TEXT,
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
    for a in ("m", "u", "t", "sco", "ov"):
        con.execute("ATTACH DATABASE ':memory:' AS %s" % a)
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

    def game(title, nk, plat, gk, ck, ct):
        n[0] += 1
        con.execute("INSERT INTO games(id,canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,card_key,card_title,sources_summary) "
                    "VALUES(?,?,?,?,?,?,?,?,?,'steam')",
                    (n[0], title, nk, plat, "%s@%s" % (nk, plat), nk, gk, ck, ct))
        con.execute("INSERT INTO sources(game_id,source,platform) VALUES(?,'steam',?)",
                    (n[0], plat))
        con.execute("INSERT INTO metadata_links(game_id,provider) VALUES(?,'igdb')",
                    (n[0],))

    # the live rows, with the card title build_library computes
    game("DARK SOULS: Prepare To Die Edition", "dark souls prepare to die", "pc",
         "title:dark souls prepare to die", "igdb:2155", "DARK SOULS")
    game("DARK SOULS: REMASTERED", "dark souls", "pc", "igdb:81085", "igdb:2155",
         "DARK SOULS")
    game("DARK SOULS: REMASTERED", "dark souls", "switch", "igdb:81085", "igdb:2155",
         "DARK SOULS")
    # a card whose root is the REGIONAL original keeps the owned title
    game("Mega Man 2", "mega man 2", "nes", "igdb:1715", "igdb:170742", "Mega Man 2")
    con.commit()

    res = srv._query_games(con, limit=100)
    by = {it["card_key"]: it for it in res["items"]}
    check("the cards collapsed", len(res["items"]) == 2)
    check("the card is named for the GAME, not an edition",
          by["igdb:2155"]["title"] == "DARK SOULS")
    check("no edition suffix survives on the card",
          "Prepare To Die" not in by["igdb:2155"]["title"]
          and "REMASTERED" not in by["igdb:2155"]["title"])
    check("a regional root does not rename the card",
          by["igdb:170742"]["title"] == "Mega Man 2")

    # an un-rebuilt catalog has no card_title and must still show a title
    con.execute("UPDATE games SET card_title=NULL")
    con.commit()
    res2 = srv._query_games(con, limit=100)
    check("a missing card_title falls back to the entry's own title",
          all(it["title"] for it in res2["items"]))

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
