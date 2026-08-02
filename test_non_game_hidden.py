#!/usr/bin/env python3
"""The non-game filter must actually be able to fire (#23).

Live, `sco.steam_type` held 0 rows for a 2208-game library, so the rule tested
membership in an EMPTY table and hid nothing, ever — `hide_non_games` was on the whole
time and did nothing. And even fully populated it could not catch fpsVR or Wallpaper
Engine, because Steam SELLS those as `game`: their type is right by Steam's lights and
wrong by ours. Their GENRE says Utilities.

So genre is a second, independent signal — free, already on the entry, and the only one
that catches that class. A manual content_type override still outranks both.

Offline: a synthetic library with the same attached-DB shape the real query uses.
"""
import os
import sqlite3
import sys
import tempfile

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    d = tempfile.mkdtemp(prefix="ludodex-nongame-")
    os.environ["LUDODEX_DATA"] = d
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from server import app as srv

    con = sqlite3.connect(":memory:")
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT, canonical_title TEXT);
    CREATE TABLE game_attributes(game_id INT, kind TEXT, value TEXT);
    """)
    ovp = os.path.join(d, "ov.sqlite")
    scp = os.path.join(d, "sco.sqlite")
    for p, ddl in ((ovp, "CREATE TABLE overrides(norm_key TEXT, kind TEXT, value TEXT)"),
                   (scp, "CREATE TABLE steam_type(norm_key TEXT, type TEXT, at INT)")):
        c = sqlite3.connect(p); c.execute(ddl); c.commit(); c.close()
    con.execute("ATTACH DATABASE ? AS ov", (ovp,))
    con.execute("ATTACH DATABASE ? AS sco", (scp,))

    def add(nk, title, genres=(), steam_type=None, override=None):
        cur = con.execute("INSERT INTO games(norm_key,canonical_title) VALUES(?,?)", (nk, title))
        for gname in genres:
            con.execute("INSERT INTO game_attributes(game_id,kind,value) VALUES(?,'genres',?)",
                        (cur.lastrowid, gname))
        if steam_type:
            con.execute("INSERT INTO sco.steam_type VALUES(?,?,0)", (nk, steam_type))
        if override:
            con.execute("INSERT INTO ov.overrides VALUES(?,'content_type',?)", (nk, override))

    add("fpsvr", "fpsVR", genres=("Indie", "Utilities"))          # Steam calls it a game
    add("displayfusion", "DisplayFusion", genres=("Utilities",))
    add("doom", "DOOM", genres=("Action", "Shooter"))
    add("wallpaper engine", "Wallpaper Engine", steam_type="application")
    add("rescued", "Real Game Tagged Utilities", genres=("Utilities",), override="Game")
    add("forced", "Game Steam Calls A Game", genres=("Action",), override="Utility")
    con.commit()

    expr, args = srv._non_game_hidden_sql()
    hidden = {r[0] for r in con.execute(
        "SELECT g.norm_key FROM games g WHERE %s" % expr, args)}

    print("1. the genre signal catches what the type signal cannot")
    check("fpsVR hidden (Steam type says game, genre says Utilities)", "fpsvr" in hidden)
    check("DisplayFusion hidden", "displayfusion" in hidden)

    print("2. the original type signal still works")
    check("Wallpaper Engine hidden by steam_type=application",
          "wallpaper engine" in hidden)

    print("3. real games are untouched")
    check("DOOM not hidden", "doom" not in hidden)

    print("4. a manual override outranks BOTH signals, in both directions")
    check("override 'Game' rescues a Utilities-tagged real game", "rescued" not in hidden)
    check("override 'Utility' hides something nothing else flagged", "forced" in hidden)

    print("5. an empty steam_type table no longer means 'hide nothing'")
    # The live failure: with 0 rows in steam_type the whole rule evaluated false for
    # every entry. Genre alone must still hide.
    con.execute("DELETE FROM sco.steam_type")
    con.commit()
    hidden2 = {r[0] for r in con.execute(
        "SELECT g.norm_key FROM games g WHERE %s" % expr, args)}
    check("fpsVR still hidden with steam_type empty", "fpsvr" in hidden2)
    check("DOOM still not hidden", "doom" not in hidden2)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
