#!/usr/bin/env python3
"""Own-console art is preferred over neutral art — but not when it is the wrong language.

Live: Castlevania Dracula X (SNES) served ScreenScraper's 478x864 JAPANESE SFC box while
a 600x900 English SteamGridDB cover sat chosen and idle beside it. Both mechanisms that
should have caught this were working correctly and neither could:

  * `region_rank` rated the Japanese asset WORST of the six candidates — but the US and
    EU ScreenScraper boxes are full box scans INCLUDING THE SPINE, so they are landscape
    and `shape_ok` had already disqualified them from a portrait cover slot. Ranking only
    orders survivors; it cannot rescue a disqualified candidate.
  * The serve resolver takes own-console art before neutral art unconditionally
    (DESIGN §11.4), and the two never meet in one bucket, so no ordering term inside a
    bucket can reach the comparison at all.

The fix is a cross-bucket step: the console bucket stands down — elects NOTHING — when
its winner is off-language and the neutral bucket's winner is not, so the existing
COALESCE falls through. These cases pin the three properties that keep it safe.
"""
import os
import sqlite3
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


JP = '{"type":"box-2D","region":"jp","format":"png"}'
US = '{"type":"box-2D","region":"us","format":"png"}'
NEUTRAL = "37357"                     # a bare provider id — store art, no language


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import test_support
    test_support.isolate("ludodex-offlang-")
    import media_choose
    import medialang

    check("an unconfigured install still has a language opinion",
          medialang.preferred_languages() == ["English"])
    check("a Japanese-region asset is off-language", medialang.is_off_language(JP))
    check("a US-region asset is not", not medialang.is_off_language(US))
    check("language-neutral store art is never off-language",
          not medialang.is_off_language(NEUTRAL, "steamgriddb"))

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE media(
        id INTEGER PRIMARY KEY, norm_key TEXT, system TEXT, game_key TEXT, kind TEXT,
        provider TEXT, ref TEXT, ref_type TEXT, matched INT, sha1 TEXT,
        width INT, height INT, filler INT, detail REAL, ai_pick INT, meta TEXT,
        frame TEXT, chosen INT DEFAULT 0, hidden INT DEFAULT 0)""")

    def add(nk, sysv, provider, meta, w=600, h=900, kind="cover"):
        con.execute(
            "INSERT INTO media(norm_key,system,game_key,kind,provider,ref,ref_type,"
            "matched,width,height,meta) VALUES(?,?,'title:x',?,?,?,'url',1,?,?,?)",
            (nk, sysv, kind, provider, "http://x/%s-%s-%s.png" % (nk, provider, sysv),
             w, h, meta))
        return con.execute("SELECT last_insert_rowid()").fetchone()[0]

    def chosen(nk, sysv, kind="cover"):
        r = con.execute("SELECT provider FROM media WHERE norm_key=? AND "
                        "COALESCE(system,'')=? AND kind=? AND chosen=1",
                        (nk, sysv, kind)).fetchone()
        return r["provider"] if r else None

    print()
    print("1. the console bucket stands down for an on-language neutral cover")
    add("dracula", "snes", "screenscraper", JP, 478, 864)
    add("dracula", "", "steamgriddb", NEUTRAL, 600, 900)
    media_choose.select(con)
    check("the Japanese own-console box is NOT chosen", chosen("dracula", "snes") is None)
    check("the English neutral cover still is",
          chosen("dracula", "") == "steamgriddb")
    check("the Japanese asset stays visible, not hidden",
          con.execute("SELECT hidden FROM media WHERE provider='screenscraper'"
                      ).fetchone()["hidden"] == 0)

    print()
    print("2. nothing is emptied when there is no replacement")
    # A box_back that exists ONLY as a Japanese scan must keep serving it — this is the
    # 19 slots a blunt hide-filter would have blanked.
    con.execute("DELETE FROM media")
    add("contra", "snes", "screenscraper", JP, 390, 705, kind="box_back")
    media_choose.select(con)
    check("a Japanese-only box_back keeps its slot",
          chosen("contra", "snes", "box_back") == "screenscraper")

    print()
    print("3. an ON-language own-console asset still beats neutral art")
    con.execute("DELETE FROM media")
    add("sonic", "genesis", "screenscraper", US, 478, 864)
    add("sonic", "", "steamgriddb", NEUTRAL, 600, 900)
    media_choose.select(con)
    check("the US own-console box keeps the console bucket",
          chosen("sonic", "genesis") == "screenscraper")
    check("...and the neutral bucket keeps its own pick",
          chosen("sonic", "") == "steamgriddb")

    print()
    print("4. a neutral bucket that is ITSELF off-language rescues nothing")
    con.execute("DELETE FROM media")
    add("ys", "snes", "screenscraper", JP, 478, 864)
    add("ys", "", "screenscraper", JP, 600, 900)
    media_choose.select(con)
    check("the console bucket does not stand down for another Japanese asset",
          chosen("ys", "snes") == "screenscraper")

    print()
    print("5. every invariant this could have broken")
    con.execute("DELETE FROM media")
    add("dracula", "snes", "screenscraper", JP, 478, 864)
    add("dracula", "", "steamgriddb", NEUTRAL, 600, 900)
    media_choose.select(con)
    n = con.execute("SELECT COUNT(*) FROM media WHERE norm_key='dracula' AND kind='cover' "
                    "AND chosen=1").fetchone()[0]
    check("I4: the game still elects a winner across its buckets (%d)" % n, n >= 1)
    dup = con.execute("SELECT COUNT(*) c FROM media WHERE chosen=1 GROUP BY norm_key, kind, "
                      "COALESCE(system,'') HAVING c > 1").fetchall()
    check("I5: no bucket has two winners", not dup)

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
