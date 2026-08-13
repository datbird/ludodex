#!/usr/bin/env python3
"""Scrape emulation games from ScreenScraper — metadata + media in one pass.

Iterates distinct emulation games (from roms-index.sqlite: system + ROM filename
+ size — the best match inputs we have without hashes), calls jeuInfos.php once
per game, and caches the full response in screenscraper-cache.sqlite. One call
yields BOTH metadata (merged fill-gaps by build_library) and media URLs (indexed
by media_index/media_fetch as the 'screenscraper' provider).

Tier-aware + resumable: reads the live ssuser quota, paces under
maxrequestspermin, and STOPS before maxrequestsperday (resuming next day). Skips
games already cached, so re-runs only fetch what's new.

  python3 ludodex/ss_scrape.py --status        # just show your tier + quota
  python3 ludodex/ss_scrape.py                  # scrape until daily cap (or all done)
  python3 ludodex/ss_scrape.py --limit 50       # cap this run
  python3 ludodex/ss_scrape.py --all            # re-scrape (ignore cache)
"""
import json
import os
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
sys.path.insert(0, DIR)
import config
import screenscraper as ss
from titlenorm import norm

CACHE = os.path.join(DATA, "screenscraper-cache.sqlite")
REGION_RANK = {"usa": 0, "world": 1, "europe": 2, "japan": 3}


def cache_con():
    con = sqlite3.connect(CACHE)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS ss_game(
      norm_key TEXT, system TEXT, ss_id INTEGER, status TEXT,
      payload_json TEXT, fetched_at INTEGER, PRIMARY KEY(norm_key, system));
    CREATE TABLE IF NOT EXISTS ss_quota(
      date TEXT PRIMARY KEY, requeststoday INTEGER, maxrequestsperday INTEGER,
      requestskotoday INTEGER, maxthreads INTEGER, updated_at INTEGER);
    """)
    return con


def worklist():
    """Distinct emulation (system, norm_key) -> a representative (filename,size).

    Picks one ROM per game/system, preferring USA/World region.
    """
    roms = config.get("roms_index_db") or os.path.expanduser("~/roms-index.sqlite")
    if not os.path.exists(roms):
        print("ss_scrape: no roms-index (%s)" % roms, file=sys.stderr)
        return []
    rc = sqlite3.connect(roms)
    best = {}
    for system, game, fn, size, region in rc.execute(
            "SELECT system, game, filename, size_bytes, region FROM roms"):
        title = game or fn          # 'game' = base game (17,353 distinct); dedup on it
        if not (system and title):
            continue
        nk = norm(title)
        if not nk:
            continue
        rank = REGION_RANK.get((region or "").split(",")[0].strip().lower(), 5)
        key = (system, nk)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, fn, size)
    rc.close()
    return [(sysname, nk, fn, size) for (sysname, nk), (_, fn, size) in best.items()]


def today():
    return time.strftime("%Y-%m-%d")


def record_quota(con, q):
    con.execute(
        "INSERT INTO ss_quota(date,requeststoday,maxrequestsperday,"
        "requestskotoday,maxthreads,updated_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(date) DO UPDATE SET requeststoday=excluded.requeststoday, "
        "maxrequestsperday=excluded.maxrequestsperday, "
        "requestskotoday=excluded.requestskotoday, updated_at=excluded.updated_at",
        (today(), q.get("requeststoday", 0), q.get("maxrequestsperday", 0),
         q.get("requestskotoday", 0), q.get("maxthreads", 1), int(time.time())))
    con.commit()


def show_status(creds):
    q = ss.user_info(creds)
    used, cap = q["requeststoday"], q["maxrequestsperday"]
    print("ScreenScraper: level %s | threads %d | %d/%d requests today"
          " (%d KO) | region %s"
          % (q["level"], q["maxthreads"], used, cap, q["requestskotoday"],
             q["favregion"]), file=sys.stderr)
    return q


def main(argv):
    if not config.metadata_enabled("screenscraper"):
        print("ss_scrape: disabled (config.py enable screenscraper)", file=sys.stderr)
        return
    creds = config.screenscraper_creds()
    if not creds:
        print("ss_scrape: no devid — request one at "
              "https://www.screenscraper.fr/forumsujets.php?frub=12, then "
              "config.py set screenscraper_devid/<devpassword>. Skipping.",
              file=sys.stderr)
        return

    # The quota probe (ssuserInfos) needs a ScreenScraper USER account. The embedded dev
    # credentials alone are enough to SCRAPE (jeuInfos works fine), so a failure here must
    # not abort: without this, an install with no user account scraped nothing at all while
    # the API was perfectly reachable — which is why ScreenScraper never produced anything.
    # Unknown quota is already safe downstream: the daily-cap check is guarded on a
    # positive cap, and every jeuInfos response carries the live quota back, so the real
    # limits self-populate after the first request.
    try:
        q = show_status(creds)
    except ss.SSError as e:
        if "--status" in argv:
            print("ss_scrape: cannot read your quota (%s)" % e, file=sys.stderr)
            return
        print("ss_scrape: no user account for the quota check (%s) — continuing with the "
              "dev credentials; limits will be read from the first response." % e.kind,
              file=sys.stderr)
        q = {"requeststoday": 0, "maxrequestsperday": 0, "maxrequestspermin": 0}
    if "--status" in argv:
        return

    do_all = "--all" in argv
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    margin = int(config.get("screenscraper_daily_margin") or 200)
    interval = max(1.2, 60.0 / max(1, q["maxrequestspermin"] or 400))

    con = cache_con()
    done = set()
    if not do_all:
        done = {(nk, sysn) for nk, sysn in con.execute(
            "SELECT norm_key, system FROM ss_game WHERE status IN "
            "('ok','notfound')")}
    work = [w for w in worklist() if (w[1], w[0]) not in done]
    print("ss_scrape: %d emulation games to scrape (%d already cached)"
          % (len(work), len(done)), file=sys.stderr)
    if limit:
        work = work[:limit]

    n = found = miss = 0
    for system, nk, fn, size in work:
        if q["requeststoday"] >= q["maxrequestsperday"] - margin > 0:
            print("ss_scrape: daily quota reached (%d/%d) — resume tomorrow."
                  % (q["requeststoday"], q["maxrequestsperday"]), file=sys.stderr)
            break
        sid = ss.systeme_id(system)
        try:
            jeu, qq = ss.jeu_infos(creds, systemeid=sid, romnom=fn, romtaille=size)
        except ss.SSError as e:
            if e.kind == "badcreds":
                print("ss_scrape: bad credentials — stopping. (%s)" % e,
                      file=sys.stderr)
                break
            if e.kind == "quota":
                print("ss_scrape: quota exhausted — resume tomorrow.", file=sys.stderr)
                break
            if e.kind == "closed":
                print("ss_scrape: API temporarily closed — backing off 60s.",
                      file=sys.stderr)
                time.sleep(60)
                continue
            print("ss_scrape: error on %s/%s: %s" % (system, fn, e), file=sys.stderr)
            time.sleep(interval)
            continue
        if qq:
            q = qq
            record_quota(con, q)
        if jeu:
            con.execute("INSERT OR REPLACE INTO ss_game(norm_key,system,ss_id,"
                        "status,payload_json,fetched_at) VALUES(?,?,?,?,?,?)",
                        (nk, system, jeu.get("id"), "ok",
                         json.dumps(jeu, ensure_ascii=False), int(time.time())))
            found += 1
        else:
            con.execute("INSERT OR REPLACE INTO ss_game(norm_key,system,ss_id,"
                        "status,payload_json,fetched_at) VALUES(?,?,?,?,?,?)",
                        (nk, system, None, "notfound", None, int(time.time())))
            miss += 1
        n += 1
        if n % 25 == 0:
            con.commit()
            print("ss_scrape: %d done (%d found, %d miss) | %d/%d quota"
                  % (n, found, miss, q["requeststoday"], q["maxrequestsperday"]),
                  file=sys.stderr)
        time.sleep(interval)
    con.commit()
    con.close()
    print("ss_scrape: done this run — %d scraped (%d found, %d not in SS)."
          % (n, found, miss), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
