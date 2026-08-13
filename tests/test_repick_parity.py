#!/usr/bin/env python3
"""A promotion after a dead asset must obey the SAME ordering as selection.

`materialize()` deletes a reference whose bytes will not come down and promotes the
next best. That promotion carried its own hand-copied sort key, and the copy went
stale: it had no resolution band, ranked provider priority ABOVE size, and merely
ranked a measured wrong shape last instead of disqualifying it. So the exact defects
fixed in `select()` — a 264x352 thumbnail beating a 600x900 cover on provider order, a
landscape grid installed into a `cover` slot — came straight back the moment one
provider URL 404'd.

Two implementations of one rule is the bug. These cases pin the behaviour the user
sees, so the fix can only be "make the promotion use the real ranker".
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
    d = tempfile.mkdtemp(prefix="ludodex-repick-")
    os.environ["LUDODEX_DATA"] = d
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import media_choose

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE media(
        id INTEGER PRIMARY KEY, norm_key TEXT, system TEXT, game_key TEXT, kind TEXT,
        provider TEXT, ref TEXT, ref_type TEXT, matched INT, sha1 TEXT,
        width INT, height INT, filler INT, detail REAL, ai_pick INT, meta TEXT, chosen INT DEFAULT 0,
        hidden INT DEFAULT 0)""")

    def add(nk, provider, w, h, kind="cover", chosen=0, ref=None):
        con.execute(
            "INSERT INTO media(norm_key,system,game_key,kind,provider,ref,ref_type,"
            "matched,width,height,chosen) VALUES(?,'','title:x',?,?,?,'url',1,?,?,?)",
            (nk, kind, provider,
             ref or ("http://x/%s-%sx%s.jpg" % (provider, w, h) if w and h
                     else "http://x/%s-plain.jpg" % provider),
             w, h, chosen))
        return con.execute("SELECT last_insert_rowid()").fetchone()[0]

    def chosen_of(nk, kind="cover"):
        return con.execute("SELECT provider,width,height FROM media "
                           "WHERE norm_key=? AND kind=? AND chosen=1",
                           (nk, kind)).fetchall()

    print("1. the promotion ranks the IMAGE above the provider")
    dead = add("game a", "steam", 600, 900, chosen=1)
    add("game a", "igdb", 264, 352)                 # higher provider priority, tiny
    add("game a", "steamgriddb", 600, 900)          # lower priority, five times the area
    con.commit()
    con.execute("DELETE FROM media WHERE id=?", (dead,))
    media_choose._repick(con, "game a", "cover", "")
    got = chosen_of("game a")
    check("exactly one cover is promoted", len(got) == 1)
    check("the 600x900 SteamGridDB cover wins over the 264x352 IGDB one",
          got and (got[0]["width"], got[0]["height"]) == (600, 900))

    print("2. a MEASURED wrong shape is disqualified, not merely ranked last")
    dead2 = add("game b", "steam", 600, 900, chosen=1)
    add("game b", "steamgriddb", 920, 430)          # landscape grid — never a cover
    con.commit()
    con.execute("DELETE FROM media WHERE id=?", (dead2,))
    media_choose._repick(con, "game b", "cover", "")
    check("no landscape asset is installed into the cover slot",
          chosen_of("game b") == [])

    print("3. an unmeasured candidate still beats nothing")
    dead3 = add("game c", "steam", 600, 900, chosen=1)
    c_un = add("game c", "screenscraper", None, None)
    con.commit()
    con.execute("DELETE FROM media WHERE id=?", (dead3,))
    media_choose._repick(con, "game c", "cover", "")
    got = chosen_of("game c")
    check("the unmeasured candidate is promoted", len(got) == 1)
    check("and it is the one that was left", got and got[0]["provider"] == "screenscraper")
    assert c_un

    print("4. a hidden candidate is never promoted")
    dead4 = add("game d", "steam", 600, 900, chosen=1)
    hid = add("game d", "igdb", 600, 900)
    con.execute("UPDATE media SET hidden=1 WHERE id=?", (hid,))
    con.commit()
    con.execute("DELETE FROM media WHERE id=?", (dead4,))
    media_choose._repick(con, "game d", "cover", "")
    check("hidden stays out of contention", chosen_of("game d") == [])

    print("5. the promotion does not disturb any OTHER game or kind")
    keep = add("game e", "igdb", 600, 900, chosen=1)
    kbg = add("game e", "igdb", 1920, 1080, kind="background", chosen=1)
    dead5 = add("game f", "steam", 600, 900, chosen=1)
    add("game f", "igdb", 600, 900)
    con.commit()
    con.execute("DELETE FROM media WHERE id=?", (dead5,))
    media_choose._repick(con, "game f", "cover", "")
    check("another game's chosen cover is untouched",
          con.execute("SELECT chosen FROM media WHERE id=?", (keep,)).fetchone()[0] == 1)
    check("the same game's other kinds are untouched",
          con.execute("SELECT chosen FROM media WHERE id=?", (kbg,)).fetchone()[0] == 1)
    check("game f got its replacement", len(chosen_of("game f")) == 1)

    print("6. a plain connection must not wipe the slot (the serve-time bug class)")
    plain = sqlite3.connect(":memory:")
    plain.executescript("".join(l for l in [
        "CREATE TABLE media(id INTEGER PRIMARY KEY, norm_key TEXT, system TEXT, "
        "game_key TEXT, kind TEXT, provider TEXT, ref TEXT, ref_type TEXT, matched INT, "
        "sha1 TEXT, width INT, height INT, filler INT, detail REAL, ai_pick INT, meta TEXT, chosen INT DEFAULT 0, "
        "hidden INT DEFAULT 0);",
        "INSERT INTO media(norm_key,system,game_key,kind,provider,ref,ref_type,matched,"
        "width,height,chosen) VALUES('game g','','title:x','cover','steamgriddb',"
        "'http://x/a.jpg','url',1,600,900,0);"]))
    plain.commit()
    media_choose._repick(plain, "game g", "cover", "")
    check("a caller with no row_factory still gets a winner",
          plain.execute("SELECT COUNT(*) FROM media WHERE chosen=1").fetchone()[0] == 1)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
