#!/usr/bin/env python3
"""An ingest must say what it will cost BEFORE it runs (#33).

Every long pass in this project has been a black box: the convergence run took 5h16m and
nothing said so in advance. The estimate has to be honest in three specific ways, or it
is worse than none at all.

  * It must count WORK, not games. Most of an ingest is skipped — a recorded identity is
    not re-searched, a cached Steam record is not re-fetched, a judged game is not
    re-billed — so the question is never "how long for 2,257 games".
  * It must be a RANGE. A ScreenScraper hit is ~10s and a miss ~2min, because a title SS
    lacks falls to the slow cross-system search. One number would be a lie both ways.
  * It must respect the TIER. Algo makes no model calls by definition, so its vision
    time is zero, not small.

Offline. No network.
"""
import os
import sqlite3
import sys

import test_support

PASS = []


def check(l, c):
    PASS.append(c); print("  %s   %s" % ("ok " if c else "FAIL", l))
    if not c:
        sys.exit("FAILED: " + l)


def main():
    d = test_support.isolate("ludodex-est-")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import estimate as E

    # ---- shape -------------------------------------------------------------
    p = E.plan("lite", total=1000, fresh=True)
    check("a range, not a point", p["high"] > p["low"] > 0)
    names = [x["phase"] for x in p["phases"]]
    for n in ("match", "sgdb", "steam_attrs", "media", "vision", "build"):
        check("phase %s is estimated" % n, n in names)
    check("the total is the sum of its phases",
          p["low"] == sum(x["low"] for x in p["phases"]))

    # ---- the tier decides whether AI time exists ---------------------------
    algo = E.plan("algo", total=1000, fresh=True)
    lite = E.plan("lite", total=1000, fresh=True)
    heavy = E.plan("heavy", total=1000, fresh=True)
    vis = lambda x: [q for q in x["phases"] if q["phase"] == "vision"][0]
    check("Algo estimates ZERO vision time — it makes no model calls",
          vis(algo)["high"] == 0)
    check("Lite estimates vision time", vis(lite)["high"] > 0)
    check("Heavy costs more than Lite — it judges every kind",
          vis(heavy)["high"] > vis(lite)["high"])
    check("Algo is cheaper than Lite overall", algo["high"] < lite["high"])

    # ---- concurrency is reflected ------------------------------------------
    slow = E.plan("lite", total=1000, fresh=True, workers={"match": 1})
    fast = E.plan("lite", total=1000, fresh=True, workers={"match": 6})
    check("more workers means less wall clock", fast["high"] < slow["high"])
    check("six workers is about six times faster on that phase",
          abs(slow["phases"][0]["high"] / 6.0 - fast["phases"][0]["high"]) < 2)

    # ---- it counts WORK, not games -----------------------------------------
    lib = os.path.join(d, "game-library.sqlite")
    c = sqlite3.connect(lib)
    c.execute("CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT)")
    for i in range(500):
        c.execute("INSERT INTO games(norm_key) VALUES(?)", ("g%d" % i,))
    c.commit(); c.close()

    mc = sqlite3.connect(os.path.join(d, "metadata-cache.sqlite"))
    mc.execute("CREATE TABLE ss_resolution(norm_key TEXT PRIMARY KEY, ss_id INT)")
    for i in range(450):                       # 90% already matched
        mc.execute("INSERT INTO ss_resolution VALUES(?,1)", ("g%d" % i,))
    mc.commit(); mc.close()

    # ...and the other caches too, or the "resync" is really a first run and the
    # comparison below proves nothing. This is the shape of a settled library.
    sm = sqlite3.connect(os.path.join(d, "steam-meta.sqlite"))
    sm.execute("CREATE TABLE steam_meta(appid TEXT PRIMARY KEY, norm_key TEXT)")
    for i in range(480):
        sm.execute("INSERT INTO steam_meta VALUES(?,?)", (str(i), "g%d" % i))
    sm.commit(); sm.close()
    mi = sqlite3.connect(os.path.join(d, "media-index.sqlite"))
    mi.execute("CREATE TABLE art_adjudicated(norm_key TEXT PRIMARY KEY, scope TEXT)")
    for i in range(470):
        mi.execute("INSERT INTO art_adjudicated VALUES(?,'cover')", ("g%d" % i,))
    mi.commit(); mi.close()

    resync = E.plan("lite")
    reset = E.plan("lite", fresh=True)
    m_resync = [x for x in resync["phases"] if x["phase"] == "match"][0]
    m_reset = [x for x in reset["phases"] if x["phase"] == "match"][0]
    check("a resync only matches what has no identity yet", m_resync["games"] == 50)
    check("a RESET matches everything — nothing is cached", m_reset["games"] == 500)
    check("so a reset is much more expensive than a resync",
          reset["high"] > resync["high"] * 3)
    check("the game count is reported either way", resync["games"] == 500)

    # ---- the words a person reads ------------------------------------------
    check("seconds render coarsely", E.human(45) == "under a minute")
    check("minutes render coarsely", E.human(1800) == "30 min")
    check("hours render with one decimal", E.human(9000) == "2.5 hours")
    check("a range reads as a range", " to " in E.summary(
        {"low": 600, "high": 7200}))
    check("a tight range collapses to one figure", " to " not in E.summary(
        {"low": 3600, "high": 3660}))

    print("\n%d/%d passed" % (sum(PASS), len(PASS)))


main()
