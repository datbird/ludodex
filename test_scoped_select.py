#!/usr/bin/env python3
"""A scoped re-select must fix ONE game without disturbing the rest.

Measurement is lazy: dimensions and the filler verdict are stamped when an asset is
first served, AFTER the selection that ranked it. Without a cheap per-game re-rank the
stale pick stands forever — live, a 460x215 screenshot held the cover slot for Golden
Axe while eight measured 484x680 covers sat unused, because at ranking time nothing
knew any of their shapes.

The trap this pins: a scoped run must scope its `chosen=0` reset too. Resetting the
whole table and restoring only the scoped rows would blank every other game's art.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import media_choose                                     # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE media(id INTEGER PRIMARY KEY, norm_key TEXT, system TEXT, kind TEXT,
      provider TEXT, ref TEXT, matched INT, ref_type TEXT, game_key TEXT,
      width INT, height INT, filler INT, detail REAL, ai_pick INT, meta TEXT, chosen INT DEFAULT 0,
      sha1 TEXT, ext TEXT, hidden INT DEFAULT 0);
    """)
    def add(nk, prov, w, h, sysm="genesis"):
        con.execute("INSERT INTO media(norm_key,system,kind,provider,ref,matched,"
                    "ref_type,game_key,width,height,filler,ai_pick) "
                    "VALUES(?,?,'cover',?,?,1,'url','igdb:1',?,?,0,NULL)",
                    (nk, sysm, prov, "http://x/%s-%s-%sx%s" % (nk, prov, w, h), w, h))
    # the live shape: a measured landscape vs measured portrait covers
    add("golden axe", "screenscraper", 460, 215)
    add("golden axe", "screenscraper", 484, 680)
    # a second game that must NOT be touched by a scoped run
    add("other game", "igdb", 600, 900)
    con.commit()

    media_choose.select(con)                     # full pass first
    other_before = con.execute("SELECT id FROM media WHERE norm_key='other game' "
                               "AND chosen=1").fetchone()
    check("full pass chose the correctly-shaped cover",
          con.execute("SELECT width FROM media WHERE norm_key='golden axe' "
                      "AND chosen=1").fetchone()[0] == 484)
    check("the other game got a pick too", other_before is not None)

    print("1. simulate the live bug: a stale pick on the landscape")
    con.execute("UPDATE media SET chosen=0 WHERE norm_key='golden axe'")
    con.execute("UPDATE media SET chosen=1 WHERE norm_key='golden axe' AND width=460")
    con.commit()
    check("stale state in place",
          con.execute("SELECT width FROM media WHERE norm_key='golden axe' "
                      "AND chosen=1").fetchone()[0] == 460)

    print("2. a scoped re-select repairs that game")
    media_choose.select(con, only=["golden axe"])
    check("the landscape lost the cover slot",
          con.execute("SELECT width FROM media WHERE norm_key='golden axe' "
                      "AND chosen=1").fetchone()[0] == 484)

    print("3. and leaves every other game alone (the reset-scoping trap)")
    other_after = con.execute("SELECT id FROM media WHERE norm_key='other game' "
                              "AND chosen=1").fetchone()
    check("the untouched game still has its pick", other_after is not None)
    check("and it is the same row", other_after[0] == other_before[0])

    print("4. an empty scope is a no-op, not a library wipe")
    media_choose.select(con, only=[])
    check("other game still chosen",
          con.execute("SELECT COUNT(*) FROM media WHERE norm_key='other game' "
                      "AND chosen=1").fetchone()[0] == 1)


    print("5. a MEASURED wrong shape is disqualified, not merely ranked last")
    con2 = sqlite3.connect(":memory:")
    con2.row_factory = sqlite3.Row
    con2.executescript("""
    CREATE TABLE media(id INTEGER PRIMARY KEY, norm_key TEXT, system TEXT, kind TEXT,
      provider TEXT, ref TEXT, matched INT, ref_type TEXT, game_key TEXT,
      width INT, height INT, filler INT, detail REAL, ai_pick INT, meta TEXT, chosen INT DEFAULT 0,
      sha1 TEXT, ext TEXT, hidden INT DEFAULT 0);
    """)
    # the ONLY candidate for this cover is a measured landscape
    con2.execute("INSERT INTO media(norm_key,system,kind,provider,ref,matched,ref_type,"
                 "game_key,width,height,filler,ai_pick) VALUES"
                 "('lonely','genesis','cover','screenscraper','http://x/a',1,'url',"
                 "'igdb:9',460,215,0,NULL)")
    # and an UNMEASURED one, which must still be electable
    con2.execute("INSERT INTO media(norm_key,system,kind,provider,ref,matched,ref_type,"
                 "game_key,width,height,filler,ai_pick) VALUES"
                 "('unknown','genesis','cover','screenscraper','http://x/b',1,'url',"
                 "'igdb:9',NULL,NULL,0,NULL)")
    con2.commit()
    media_choose.select(con2)
    check("a known-wrong-shape sole candidate is NOT elected",
          con2.execute("SELECT COUNT(*) FROM media WHERE norm_key='lonely' "
                       "AND chosen=1").fetchone()[0] == 0)
    check("an UNMEASURED sole candidate still is",
          con2.execute("SELECT COUNT(*) FROM media WHERE norm_key='unknown' "
                       "AND chosen=1").fetchone()[0] == 1)


    print("6. a PLAIN connection must not leave a game with nothing chosen")
    # The live regression: the serve-time re-rank passed a bare sqlite3.connect. select()
    # reads rows by name, so it raised — AFTER its own chosen=0 reset — and the caller
    # swallowed it. Every image viewed wiped that game's art.
    plain = sqlite3.connect(":memory:")            # deliberately NO row_factory
    plain.executescript("""
    CREATE TABLE media(id INTEGER PRIMARY KEY, norm_key TEXT, system TEXT, kind TEXT,
      provider TEXT, ref TEXT, matched INT, ref_type TEXT, game_key TEXT,
      width INT, height INT, filler INT, detail REAL, ai_pick INT, meta TEXT, chosen INT DEFAULT 0,
      sha1 TEXT, ext TEXT, hidden INT DEFAULT 0);
    """)
    plain.execute("INSERT INTO media(norm_key,system,kind,provider,ref,matched,ref_type,"
                  "game_key,width,height,filler,ai_pick) VALUES"
                  "('g','genesis','cover','igdb','http://x/c',1,'url','igdb:1',600,900,0,NULL)")
    plain.commit()
    media_choose.select(plain, only=["g"])
    check("a plain connection still elects a winner",
          plain.execute("SELECT COUNT(*) FROM media WHERE norm_key='g' "
                        "AND chosen=1").fetchone()[0] == 1)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
