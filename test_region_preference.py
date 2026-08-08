#!/usr/bin/env python3
"""Region is a DETERMINISTIC signal, and it was going unused (#31).

Contra: Hard Corps served its Japanese box while the US box sat beside it — same
provider, same 497x680, same `box-2D` type — and the Japanese one had been vision-picked.
ScreenScraper had stamped both with their region in `media.meta` all along
({"type":"box-2D","region":"jp"}), and nothing consulted that tag when CHOOSING. It was
read only as a proxy for language, to hide or ban off-language art.

Asking a model to read box artwork is the fallback for "which release is this", not the
answer, when the answer is already written down in the row.

Offline. No network, no model.
"""
import os
import sqlite3
import sys

import test_support

PASS = []


def check(label, cond):
    PASS.append(cond); print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    d = test_support.isolate("ludodex-region-")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import config
    import medialang as M
    import media_choose

    # ---- 1. the preference ------------------------------------------------------
    check("with nothing set, the US/EU release is preferred",
          M.preferred_regions()[0] == "us")
    config.set_("media_languages", "Japanese")
    check("the region preference FOLLOWS the language preference",
          M.preferred_regions()[0] == "jp")
    config.set_("media_regions", "eu,us")
    check("an explicit region preference outranks the language default",
          M.preferred_regions()[:2] == ["eu", "us"])
    config.set_("media_regions", "")
    config.set_("media_languages", "")

    # ---- 2. reading the tag -----------------------------------------------------
    check("a ScreenScraper region tag is read",
          M.region_of('{"type":"box-2D","region":"us"}') == "us")
    check("a multi-region tag takes the first",
          M.region_of('{"region":"eu,us"}') == "eu")
    check("a non-JSON meta (an appid) is not a region",
          M.region_of("35797") == "")
    check("absent meta is not a region", M.region_of(None) == "")
    check("malformed JSON is not a region", M.region_of("{oops") == "")

    # ---- 3. the ordering --------------------------------------------------------
    prefs = M.preferred_regions()
    us = M.region_rank('{"region":"us"}', prefs)
    unknown = M.region_rank(None, prefs)
    jp = M.region_rank('{"region":"jp"}', prefs)
    check("the preferred region ranks first", us == 0)
    check("an unregioned store asset beats an unwanted region", unknown < jp)
    check("an unwanted region is ranked last, never excluded", jp == len(prefs) + 1)

    # ---- 4. selection actually uses it ------------------------------------------
    idx = os.path.join(d, "media-index.sqlite")
    con = sqlite3.connect(idx)
    con.executescript("""
    CREATE TABLE media(id INTEGER PRIMARY KEY, norm_key TEXT, kind TEXT, provider TEXT,
      ref TEXT, ref_type TEXT DEFAULT 'url', system TEXT, game_key TEXT, chosen INT
      DEFAULT 0, ai_pick INT, hidden INT DEFAULT 0, width INT, height INT, filler INT,
      detail REAL,
      matched INT DEFAULT 1, meta TEXT, sha1 TEXT, ext TEXT DEFAULT 'jpg');
    """)

    def add(i, region, ai=None, w=497, h=680):
        con.execute("INSERT INTO media(id,norm_key,kind,provider,ref,system,game_key,"
                    "width,height,ai_pick,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (i, "contra hard corps", "cover", "screenscraper", "u%d" % i,
                     "genesis", "igdb:20192", w, h, ai,
                     '{"type":"box-2D","region":"%s"}' % region))

    add(1, "jp", ai=1)          # the vision pick — exactly the live shape
    add(2, "us")
    con.commit()
    media_choose.select(con)
    con.commit()
    won = con.execute("SELECT id FROM media WHERE chosen=1").fetchone()[0]
    check("the US box wins over a vision-picked Japanese one", won == 2)

    # ...and a bigger wrong-region asset still loses: region is above resolution
    con.execute("UPDATE media SET chosen=0")
    add(3, "jp", w=2000, h=2800)
    con.commit()
    media_choose.select(con)
    con.commit()
    won = con.execute("SELECT id FROM media WHERE chosen=1").fetchone()[0]
    check("a much larger wrong-region asset still loses to the right region", won == 2)

    # ...but region never beats a user PIN or a proven-wrong shape
    con.execute("UPDATE media SET chosen=0")
    con.execute("UPDATE media SET filler=1 WHERE id=2")
    con.commit()
    media_choose.select(con)
    con.commit()
    won = con.execute("SELECT id FROM media WHERE chosen=1").fetchone()[0]
    check("a confirmed placeholder loses even in the right region", won != 2)

    print("\n%d/%d passed" % (sum(PASS), len(PASS)))


if __name__ == "__main__":
    main()
