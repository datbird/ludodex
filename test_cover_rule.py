#!/usr/bin/env python3
"""ONE definition of "has a cover" (#28).

Five places decided it independently — the stats card, the has_cover filter, the
has_cover sort, the library grid and Spotlight — and three used a NAIVE rule
(`chosen=1 AND kind='cover'`, no system or identity gate). So a game whose only chosen
cover belonged to another console counted as covered in the stats, passed the filter,
sorted as covered, then rendered a placeholder in the grid.

_has_cover_sql is now the single source. This pins the properties that made the naive
version wrong.
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
    d = tempfile.mkdtemp(prefix="ludodex-coverrule-")
    os.environ["LUDODEX_DATA"] = d
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from server import app as srv

    con = sqlite3.connect(":memory:")
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT, platform TEXT, game_key TEXT);
    CREATE TABLE media(norm_key TEXT, system TEXT, kind TEXT, chosen INT, game_key TEXT);
    CREATE TABLE user_media(norm_key TEXT, kind TEXT);
    """)
    con.execute("ATTACH DATABASE ':memory:' AS m")
    con.execute("ATTACH DATABASE ':memory:' AS u")
    con.executescript("""
    CREATE TABLE m.media(norm_key TEXT, system TEXT, kind TEXT, chosen INT, game_key TEXT);
    CREATE TABLE u.user_media(norm_key TEXT, kind TEXT);
    """)

    def game(nk, plat, gk):
        con.execute("INSERT INTO games(norm_key,platform,game_key) VALUES(?,?,?)", (nk, plat, gk))

    def art(nk, system, gk, chosen=1):
        con.execute("INSERT INTO m.media(norm_key,system,kind,chosen,game_key) "
                    "VALUES(?,?,'cover',?,?)", (nk, system, chosen, gk))

    game("own", "genesis", "igdb:1")
    art("own", "genesis", "igdb:1")                 # own-console art -> covered
    game("foreign", "genesis", "igdb:2")
    art("foreign", "snes", "igdb:2")                # ANOTHER console's art -> NOT covered
    game("neutral", "genesis", "igdb:3")
    art("neutral", "", "igdb:3")                    # neutral, identity matches -> covered
    game("mismatch", "genesis", "igdb:4")
    art("mismatch", "", "title:mismatch")           # neutral, identity differs -> NOT covered
    game("upload", "genesis", "igdb:5")
    con.execute("INSERT INTO u.user_media(norm_key,kind) VALUES('upload','cover')")
    game("none", "genesis", "igdb:6")
    con.commit()

    expr = srv._has_cover_sql(True, True)
    got = {r[0] for r in con.execute("SELECT norm_key FROM games g WHERE " + expr)}

    print("1. the display rule, not the naive one")
    check("own-console art counts", "own" in got)
    check("ANOTHER console's art does NOT count", "foreign" not in got)
    check("neutral art with a matching identity counts", "neutral" in got)
    check("neutral art with a DIFFERENT identity does not", "mismatch" not in got)
    check("a user upload counts", "upload" in got)
    check("no art at all does not", "none" not in got)

    print("2. the naive rule would have got two of those wrong")
    naive = ("EXISTS(SELECT 1 FROM m.media md WHERE md.norm_key=g.norm_key "
             "AND md.chosen=1 AND md.kind='cover')")
    ngot = {r[0] for r in con.execute("SELECT norm_key FROM games g WHERE " + naive)}
    check("naive wrongly counts another console's art", "foreign" in ngot)
    check("naive wrongly counts a mismatched identity", "mismatch" in ngot)
    check("and it misses user uploads entirely", "upload" not in ngot)

    print("3. degrades safely on a catalog without entry_key/game_key")
    plain = srv._has_cover_sql(False, False)
    pgot = {r[0] for r in con.execute("SELECT norm_key FROM games g WHERE " + plain)}
    check("no crash, and uploads still count", "upload" in pgot)
    check("a game with no art is still uncovered", "none" not in pgot)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
