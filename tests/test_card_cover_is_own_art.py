#!/usr/bin/env python3
"""A card never borrows another console's art.

This is the property the 2026-07-15 per-platform refactor was built for: a TurboGrafx
game must not show a Game Boy cover. Collapsing platforms into one card is exactly the
change that could give it back, because a card now spans consoles. The rule survives
because the CARD shows one REPRESENTATIVE ENTRY's art, and that entry's art is still
gated on its own system and its own game_key.
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
      n_sources INTEGER DEFAULT 1, n_kinds INTEGER DEFAULT 1, sources_summary TEXT,
      has_emulation INT DEFAULT 1, wanted INT DEFAULT 0, parent_key TEXT,
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
    CREATE TABLE sco.store_type(norm_key TEXT, source TEXT, type TEXT);
    CREATE TABLE ov.overrides(norm_key TEXT, kind TEXT, value TEXT);
    """)

    n = [0]

    def game(nk, plat, gk, ck):
        n[0] += 1
        con.execute("INSERT INTO games(id,canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,card_key,sources_summary) "
                    "VALUES(?,?,?,?,?,?,?,?,'emulation')",
                    (n[0], nk, nk, plat, "%s@%s" % (nk, plat), nk, gk, ck))
        con.execute("INSERT INTO sources(game_id,source,platform) VALUES(?,'emulation',?)",
                    (n[0], plat))
        con.execute("INSERT INTO metadata_links(game_id,provider) VALUES(?,'igdb')",
                    (n[0],))

    def art(nk, system, gk, sha):
        con.execute("INSERT INTO m.media(norm_key,system,kind,chosen,sha1,game_key) "
                    "VALUES(?,?,'cover',1,?,?)", (nk, system, sha, gk))

    # One game, two consoles. ONLY the Game Boy copy has art. The atari row is inserted
    # FIRST so a representative rule that ignored art would pick it.
    game("klax", "atari 2600", "igdb:70", "igdb:70")
    game("klax", "gameboy", "igdb:70", "igdb:70")
    art("klax", "gameboy", "igdb:70", "gbcover0001")
    con.commit()

    res = srv._query_games(con, limit=100)
    check("the two consoles are one card", len(res["items"]) == 1)
    card = res["items"][0]
    check("the card reports a cover", card["has_cover"] is True)
    check("the cover is the Game Boy art", card["cover_v"] == "gbcover0001")
    check("the representative is the entry that owns the art",
          card["entry_key"] == "klax@gameboy")

    # Now a card whose ONLY art belongs to a console nobody on the card owns.
    con.execute("DELETE FROM m.media")
    art("klax", "snes", "igdb:70", "snescover001")
    con.commit()
    res2 = srv._query_games(con, limit=100)
    card2 = res2["items"][0]
    check("foreign art does not count as a cover", card2["has_cover"] is False)
    check("and no foreign hash is offered", card2["cover_v"] is None)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
