#!/usr/bin/env python3
"""A local mirror of ScreenScraper's catalog, walked by game id and kept resumable.

WHY A WALK AND NOT A QUERY. ScreenScraper has no bulk endpoint. jeuxListe.php,
systemeInfos.php and an empty jeuRecherche all return nothing — verified live, not
assumed — so there is no way to ask for "every Switch game". The only addressable
handle is jeuInfos.php?gameid=N, which means the catalog can be obtained exactly one
way: walk the id space.

WHAT THE WALK IS ACTUALLY FOR. Two things, and the second is the bigger one:

  * ss id <-> igdb id. Matched locally afterwards, against the IGDB mirror, so the
    rules can be re-run for free whenever they improve.
  * ROM HASHES. Every jeuInfos response carries the complete rom list for the game,
    each with crc/md5/sha1 — Sonic 2 alone returns 201 of them. A local hash index
    turns matching a ROM file from a name search with an acceptance gate into an
    exact join. No request, no heuristic, no gate. That is why the whole rom block
    is kept even though it dwarfs the identity fields.

THE ID SPACE IS SPARSE. Roughly 68% of ids below 321k are real, 24% between there
and the ~535k ceiling, and nothing above it. A missing id is NORMAL — it is a hole,
not an error, and not a signal to stop. Bisecting for the ceiling is meaningless for
the same reason: the first probe of this mirror "found" a ceiling of 3 because id 4
happens to be empty. The walk therefore ends on a long RUN of dead ids, never on one.

QUOTA COMES FROM THE SERVER. Every response carries an ssuser block with
requeststoday/maxrequestsperday. A local counter would drift the moment anything else
scraped on the same account, so the server's number is the authority and the walk
stops against it — leaving DAILY_RESERVE unspent so normal ludodex scraping still
works while a multi-day walk is in progress.

  python3 ludodex/ss_mirror.py --walk                  # resume; stops at the daily quota
  python3 ludodex/ss_mirror.py --walk --max-requests N # bounded chunk
  python3 ludodex/ss_mirror.py --walk --until-id N     # phase 1 = the dense half
  python3 ludodex/ss_mirror.py --status
  python3 ludodex/ss_mirror.py --systems               # (re)build the SS->IGDB platform map
"""
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
sys.path.insert(0, DIR)
import config              # noqa: E402
import screenscraper as ss  # noqa: E402
from titlenorm import norm  # noqa: E402

DB = os.path.join(DATA, "ss-catalog.sqlite")
IGDB_DB = os.path.join(DATA, "igdb-catalog.sqlite")

# Measured, not guessed: the highest live id seen via search was 535,220 and 75 samples
# above it were all dead. Kept as a floor for the dead-run stop, not as a hard end.
TOP_ID_SEEN = 535220
DEAD_RUN_STOP = 4000        # consecutive dead ids past TOP_ID_SEEN that mean "done"
BLOCK = 60                  # ids dispatched per round; cursor advances a whole block
CLOSED_STRIKES = 8          # consecutive "api closed" rounds that mean stop for now

# EVERY LIMIT BELOW IS A FALLBACK, NOT A POLICY. ScreenScraper reports what an account
# is actually granted — threads, requests per day, requests per minute — and those
# numbers differ enormously between a free account and a financial contributor. Baking
# one tier's numbers in is how a scraper works beautifully for its author and gets the
# next person throttled or banned.
#
# So the account's own figures win, config can narrow them (never widen), and these
# constants only apply when the server says nothing.
# How long to wait before ASKING AGAIN whether the daily quota has reset.
#
# Not "until midnight". The first version parked until the next UTC midnight and was
# wrong by up to a day: ScreenScraper's counter resets on its own schedule, and the walk
# gated the quota READ behind the cooldown — so once parked it could not notice the
# reset it was waiting for. Observed live sitting on 77,000 available requests with
# 20 hours still to run.
#
# So the cooldown is now a re-check interval, and the live quota decides. Costs ~48
# extra requests a day against a five-figure budget, and cannot be wrong by more than
# this interval however the upstream schedule works.
QUOTA_RECHECK_SECS = 30 * 60

HARD_THREAD_CAP = 16        # absurdity guard; the account's value normally decides
FALLBACK_THREADS = 1        # what a free account typically gets
FALLBACK_PER_MIN = 60       # deliberately timid when the server does not say
RESERVE_FRACTION = 0.05     # keep 5% of the day back for the user's own scraping
MIN_RESERVE = 200           # ...but always SOME, even on a tiny quota

# Kept as a module attribute because the tests and older callers refer to it. It is now
# a floor on the computed reserve rather than the reserve itself.
DAILY_RESERVE = MIN_RESERVE


def tier_limits(q, threads=None):
    """What this account grants, and what this run will therefore use.

    Config may only ever NARROW what the server reported. A user who sets 8 threads on a
    1-thread account does not get 8 threads; they get a scraper that behaves and a
    number in a settings box that was never a promise."""
    granted_threads = max(1, int(q.get("maxthreads") or FALLBACK_THREADS))
    want = threads or config.get("screenscraper_walk_threads")
    try:
        want = int(want) if want else granted_threads
    except (TypeError, ValueError):
        want = granted_threads
    use_threads = max(1, min(want, granted_threads, HARD_THREAD_CAP))

    per_day = int(q.get("maxrequestsperday") or 0)
    cfg_res = config.get("screenscraper_walk_reserve")
    try:
        reserve = int(cfg_res) if cfg_res else 0
    except (TypeError, ValueError):
        reserve = 0
    if not reserve:
        reserve = max(MIN_RESERVE, int(per_day * RESERVE_FRACTION))
    reserve = min(reserve, per_day)          # a reserve bigger than the quota is a stop

    per_min = int(q.get("maxrequestspermin") or 0) or FALLBACK_PER_MIN
    # The aggregate rate across the whole pool has to stay under the per-minute cap, so
    # a block of N concurrent requests must take at least N * (60/per_min) seconds.
    block_seconds = (60.0 / per_min) * use_threads

    return {"threads": use_threads, "granted_threads": granted_threads,
            "per_day": per_day, "reserve": reserve, "per_min": per_min,
            "min_block_seconds": block_seconds}

# SS systems whose names do not slug-match an IGDB platform but which ARE the same
# hardware. Verified against the mirror one at a time; every other unmapped SS system
# was checked and genuinely has no IGDB counterpart, so this list is short on purpose.
MANUAL_PLATFORM = {
    114: "Turbografx-16/PC Engine CD",
    207: "Watara/QuickShot Supervision",
    221: "PC-8800 Series",
}


def con_db():
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS ss_games(
      id INTEGER PRIMARY KEY,
      systeme INTEGER, name TEXT, norm_key TEXT, year INTEGER,
      developer TEXT, publisher TEXT,
      notgame INTEGER,               -- SS's own "this is not a game" flag
      n_roms INTEGER, seen_at INTEGER);
    CREATE INDEX IF NOT EXISTS ix_ssg_norm ON ss_games(norm_key);
    CREATE INDEX IF NOT EXISTS ix_ssg_sys  ON ss_games(systeme);
    -- Every regional name, because 'Rondo of Blood' and 'Akumajou Dracula X' are the
    -- same game and an offline matcher can only know that if both are local.
    CREATE TABLE IF NOT EXISTS ss_names(
      game_id INTEGER, region TEXT, name TEXT, norm_key TEXT,
      PRIMARY KEY(game_id, region, name));
    CREATE INDEX IF NOT EXISTS ix_ssn_norm ON ss_names(norm_key);
    -- The exact-match index. A hash needs no acceptance gate and cannot be a wrong bind.
    CREATE TABLE IF NOT EXISTS ss_roms(
      game_id INTEGER, crc TEXT, md5 TEXT, sha1 TEXT,
      filename TEXT, size INTEGER, region TEXT,
      PRIMARY KEY(game_id, filename, crc));
    CREATE INDEX IF NOT EXISTS ix_ssr_crc  ON ss_roms(crc);
    CREATE INDEX IF NOT EXISTS ix_ssr_md5  ON ss_roms(md5);
    CREATE INDEX IF NOT EXISTS ix_ssr_sha1 ON ss_roms(sha1);
    CREATE TABLE IF NOT EXISTS ss_systems(
      id INTEGER PRIMARY KEY, name TEXT, names TEXT, company TEXT, type TEXT,
      igdb_platform INTEGER, mapped_by TEXT);
    CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT);
    """)
    con.commit()
    return con


def get(con, k, d=None):
    r = con.execute("SELECT v FROM state WHERE k=?", (k,)).fetchone()
    return r["v"] if r else d


def put(con, k, v):
    con.execute("INSERT OR REPLACE INTO state(k,v) VALUES(?,?)", (k, str(v)))


# --- the SS system -> IGDB platform map ------------------------------------- #
def _slug(s):
    import re
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def sync_systems(con, creds):
    """Fetch SS's system list and bind each to an IGDB platform where one exists.

    An SS system we fail to map is match ceiling thrown away for free, so this runs
    before any walking and is cheap enough to re-run whenever the IGDB mirror grows."""
    ig = {}
    if os.path.exists(IGDB_DB):
        m = sqlite3.connect("file:%s?mode=ro" % IGDB_DB, uri=True)
        for pid, name, abbr, alt in m.execute(
                "SELECT id,name,abbreviation,alternative_name FROM platforms"):
            for cand in (abbr, name, alt):
                if cand and _slug(cand):
                    ig.setdefault(_slug(cand), pid)
        m.close()

    resp = ss._request("systemesListe.php", creds)
    systems = (resp or {}).get("systemes") or []
    n = mapped = 0
    for s in systems:
        sid = s.get("id")
        if sid is None:
            continue
        noms = s.get("noms") or {}
        names = [v for v in noms.values() if isinstance(v, str)]
        label = noms.get("nom_us") or noms.get("noms_commun") or (
            names[0] if names else "")
        pid, how = None, None
        manual = MANUAL_PLATFORM.get(int(sid))
        if manual and _slug(manual) in ig:
            pid, how = ig[_slug(manual)], "manual"
        if pid is None:
            import re
            for nm in names:
                for part in re.split(r"[/,]", nm):
                    if _slug(part) in ig:
                        pid, how = ig[_slug(part)], "name"
                        break
                if pid:
                    break
        con.execute(
            "INSERT OR REPLACE INTO ss_systems(id,name,names,company,type,"
            "igdb_platform,mapped_by) VALUES(?,?,?,?,?,?,?)",
            (int(sid), label, json.dumps(names), s.get("compagnie"),
             s.get("type"), pid, how))
        n += 1
        mapped += 1 if pid else 0
    con.commit()
    return {"systems": n, "mapped": mapped, "unmapped": n - mapped}


# --- storing one game ------------------------------------------------------- #
def _store(con, gid, jeu, now, region="us"):
    """Persist the identity + every regional name + every hashed rom."""
    roms = jeu.get("roms") or []
    if isinstance(roms, dict):
        roms = [roms]
    name = ss.jeu_name(jeu, region) or ""
    yr = ss.jeu_year(jeu, region)
    dev = (jeu.get("developpeur") or {}).get("text") if isinstance(
        jeu.get("developpeur"), dict) else jeu.get("developpeur")
    pub = (jeu.get("editeur") or {}).get("text") if isinstance(
        jeu.get("editeur"), dict) else jeu.get("editeur")
    con.execute(
        "INSERT INTO ss_games(id,systeme,name,norm_key,year,developer,publisher,"
        "notgame,n_roms,seen_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET systeme=excluded.systeme, name=excluded.name, "
        "norm_key=excluded.norm_key, year=excluded.year, developer=excluded.developer, "
        "publisher=excluded.publisher, notgame=excluded.notgame, "
        "n_roms=excluded.n_roms, seen_at=excluded.seen_at",
        (gid, ss.jeu_system_id(jeu), name, norm(name),
         int(yr) if yr and yr.isdigit() else None, dev, pub,
         1 if str(jeu.get("notgame", "")).lower() in ("1", "true") else 0,
         len(roms), now))

    # Names and roms are REPLACED, not merged: if SS drops a name or a dump, keeping
    # our copy would match on something the source has disowned.
    con.execute("DELETE FROM ss_names WHERE game_id=?", (gid,))
    for e in (jeu.get("noms") or []):
        if isinstance(e, dict) and e.get("text"):
            con.execute("INSERT OR IGNORE INTO ss_names(game_id,region,name,norm_key) "
                        "VALUES(?,?,?,?)",
                        (gid, e.get("region") or "", e["text"], norm(e["text"])))
    con.execute("DELETE FROM ss_roms WHERE game_id=?", (gid,))
    for r in roms:
        if not isinstance(r, dict):
            continue
        crc = (r.get("romcrc") or "").lower()
        md5 = (r.get("rommd5") or "").lower()
        sha1 = (r.get("romsha1") or "").lower()
        if not (crc or md5 or sha1):
            continue                      # a rom with no hash indexes nothing
        try:
            size = int(r.get("romsize") or 0) or None
        except (TypeError, ValueError):
            size = None
        con.execute(
            "INSERT OR IGNORE INTO ss_roms(game_id,crc,md5,sha1,filename,size,region) "
            "VALUES(?,?,?,?,?,?,?)",
            (gid, crc, md5, sha1, r.get("romfilename") or "", size,
             r.get("romregions") or ""))


# --- the walk --------------------------------------------------------------- #
def walk(max_requests=None, until_id=None, progress=True, threads=None):
    """Walk gameid upward until the quota, the budget, or the catalog runs out.

    Never raises on quota: the cursor is durable, so stopping early costs only time."""
    con = con_db()
    creds = config.screenscraper_creds()

    cool = float(get(con, "cooldown_until", 0) or 0)
    if time.time() < cool:
        left = int(cool - time.time())
        print("ss_mirror: quota spent; re-checking in %dm" % max(1, left // 60),
              file=sys.stderr)
        con.close()
        return {"skipped": "cooldown", "seconds_left": left}

    q = ss.user_info(creds)
    tier = tier_limits(q, threads)
    nthreads = tier["threads"]
    limit, used = tier["per_day"], (q.get("requeststoday") or 0)
    budget = max(0, limit - tier["reserve"] - used) if limit else (max_requests or 0)
    if max_requests:
        budget = min(budget, max_requests)
    if budget <= 0:
        put(con, "cooldown_until", time.time() + QUOTA_RECHECK_SECS)
        con.commit()
        con.close()
        print("ss_mirror: daily quota spent (%d/%d used, %d reserved) — resuming after "
              "reset" % (used, limit, tier["reserve"]), file=sys.stderr)
        return {"skipped": "quota", "used": used, "limit": limit,
                "reserve": tier["reserve"]}

    if not con.execute("SELECT COUNT(*) FROM ss_systems").fetchone()[0]:
        print("ss_mirror: %s" % json.dumps(sync_systems(con, creds)), file=sys.stderr)

    cursor = int(get(con, "cursor", 0) or 0)
    dead_run = int(get(con, "dead_run", 0) or 0)
    # A FINISHED walk has dead_run already past the stop line, so without this a re-run
    # walks one block, finds it dead, and re-declares itself complete — creeping 60 ids
    # per invocation and never actually looking for what was added since. Starting a
    # fresh run means the question is open again.
    if get(con, "walk_complete"):
        dead_run = 0
        put(con, "walk_complete", "")
        con.commit()
    region = q.get("favregion") or "us"
    reqs = found = closed_strikes = 0
    t0 = time.time()
    stop = None

    def fetch(gid):
        """-> (gid, jeu|None, err|None). Absence is a RESULT, not a failure."""
        try:
            jeu, _qv = ss.jeu_infos(creds, gameid=gid)
            return gid, jeu, None
        except ss.SSError as e:
            return gid, None, e
        except Exception as e:                       # noqa: BLE001
            return gid, None, ss.SSError("error", str(e)[:120])

    try:
        with ThreadPoolExecutor(max_workers=nthreads) as pool:
            while reqs < budget and stop is None:
                lo = cursor + 1
                hi = min(lo + BLOCK - 1, lo + (budget - reqs) - 1)
                if until_id:
                    hi = min(hi, until_id)
                if hi < lo:
                    stop = "until_id"
                    break
                ids = list(range(lo, hi + 1))
                block_started = time.time()
                results = list(pool.map(fetch, ids))
                reqs += len(ids)
                # THE PER-MINUTE CAP IS A REAL LIMIT AND WAS PREVIOUSLY IGNORED. On a
                # generous tier the block is never fast enough to matter; on a free one
                # it is the difference between scraping and being throttled.
                need = tier["min_block_seconds"] * len(ids) / max(1, nthreads)
                spent = time.time() - block_started
                if spent < need:
                    time.sleep(need - spent)

                hard = None
                for gid, jeu, err in results:
                    if err is not None:
                        if err.kind == "quota":
                            # A 429 and "you are done for the day" arrive as the same
                            # kind, and they are not the same thing. Six concurrent
                            # threads produce the occasional transient throttle;
                            # treating that as daily exhaustion killed a run that had
                            # 77,000 requests left and parked it for half an hour.
                            # The ssuser counters are the authority, as everywhere else.
                            if _really_out_of_quota(creds, tier):
                                hard = err
                            else:
                                hard = hard or ss.SSError("closed", "rate limited")
                        elif err.kind == "badcreds":
                            hard = err
                        elif err.kind == "closed":
                            hard = hard or err
                        continue
                    if jeu and jeu.get("id"):
                        _store(con, gid, jeu, int(time.time()), region)
                        found += 1

                # THE CURSOR ADVANCES A WHOLE BLOCK. Threads finish out of order, so
                # advancing per result would leave holes that a resume never revisits.
                if hard is not None and hard.kind in ("quota", "badcreds"):
                    stop = hard.kind
                    break
                cursor = hi
                put(con, "cursor", cursor)

                if hard is not None and hard.kind == "closed":
                    closed_strikes += 1
                    time.sleep(min(60, 5 * closed_strikes))
                    if closed_strikes >= CLOSED_STRIKES:
                        stop = "closed"
                else:
                    closed_strikes = 0

                # A hole is normal; a long RUN of them past the known ceiling is the end.
                dead_run = 0 if found and any(j for _g, j, _e in results if j) \
                    else dead_run + len(ids)
                put(con, "dead_run", dead_run)
                con.commit()
                if cursor > TOP_ID_SEEN and dead_run >= DEAD_RUN_STOP:
                    put(con, "walk_complete", int(time.time()))
                    stop = "exhausted"
                if progress and reqs % (BLOCK * 10) == 0:
                    print("ss_mirror: id %d | %d games | %d requests | %.0f id/s"
                          % (cursor, found, reqs,
                             reqs / max(0.001, time.time() - t0)), file=sys.stderr)
    finally:
        con.commit()

    if stop == "quota":
        put(con, "cooldown_until", time.time() + QUOTA_RECHECK_SECS)
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM ss_games").fetchone()[0]
    roms = con.execute("SELECT COUNT(*) FROM ss_roms").fetchone()[0]
    con.close()
    return {"requests": reqs, "games_found": found, "cursor": cursor,
            "stopped": stop or "budget", "elapsed": round(time.time() - t0, 1),
            "total_games": total, "total_roms": roms, "threads": nthreads,
            "tier": tier}


def refresh_stale(days=90, max_requests=None, progress=True):
    """Re-fetch games not seen for `days`, oldest first.

    The id walk finds games that are NEW. It can never find what changed about a game
    already held — and ScreenScraper's most frequent change is exactly that: a new ROM
    dump added to an existing entry. Those are the hashes that match a file you just
    acquired, so a mirror that only ever walks forward goes quietly stale where it
    matters most.

    Bounded by max_requests on purpose. 175,000 games is two days of quota to re-check
    in full, so this is meant to be run against the oldest slice repeatedly rather than
    all at once."""
    con = con_db()
    left = _Pacer_cooling(con)
    if left:
        con.close()
        return {"skipped": "cooldown", "seconds_left": left}
    creds = config.screenscraper_creds()
    q = ss.user_info(creds)
    tier = tier_limits(q)
    budget = max(0, tier["per_day"] - tier["reserve"] - (q.get("requeststoday") or 0))
    if max_requests:
        budget = min(budget, max_requests)
    if budget <= 0:
        con.close()
        return {"skipped": "quota"}

    cutoff = time.time() - days * 86400
    rows = [r["id"] for r in con.execute(
        "SELECT id FROM ss_games WHERE COALESCE(seen_at,0) < ? ORDER BY seen_at LIMIT ?",
        (cutoff, budget))]
    if not rows:
        con.close()
        return {"stale": 0, "note": "nothing older than %d days" % days}

    nthreads = tier["threads"]
    now = int(time.time())
    done = roms_before = 0
    roms_before = con.execute("SELECT COUNT(*) FROM ss_roms").fetchone()[0]

    def fetch(gid):
        try:
            jeu, _ = ss.jeu_infos(creds, gameid=gid)
            return gid, jeu
        except Exception:                        # noqa: BLE001
            return gid, None

    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=nthreads) as pool:
        for i in range(0, len(rows), BLOCK):
            chunk = rows[i:i + BLOCK]
            started = time.time()
            for gid, jeu in pool.map(fetch, chunk):
                if jeu and jeu.get("id"):
                    _store(con, gid, jeu, now, q.get("favregion") or "us")
                    done += 1
            con.commit()
            need = tier["min_block_seconds"] * len(chunk) / max(1, nthreads)
            spent = time.time() - started
            if spent < need:
                time.sleep(need - spent)
            if progress and (i // BLOCK) % 20 == 0:
                print("ss_mirror: refreshed %d/%d stale games" % (done, len(rows)),
                      file=sys.stderr)
    roms_after = con.execute("SELECT COUNT(*) FROM ss_roms").fetchone()[0]
    con.close()
    return {"stale_examined": len(rows), "refreshed": done,
            "new_rom_rows": roms_after - roms_before,
            "elapsed": round(time.time() - t0, 1)}


def _Pacer_cooling(con):
    """Cooldown check, shared with walk()."""
    return max(0, int(float(get(con, "cooldown_until", 0) or 0) - time.time()))


def _really_out_of_quota(creds, tier):
    """Is the DAILY quota actually spent, or was that just a rate-limit?

    Costs one request to ask, which is the right trade: the alternative is a run that
    stops on its first transient 429 and waits half an hour to discover it could have
    carried on. Fails SAFE — if the check itself errors we assume exhaustion, because
    hammering an API that just refused us is the worse mistake."""
    try:
        q = ss.user_info(creds)
    except Exception:                            # noqa: BLE001
        return True
    used = int(q.get("requeststoday") or 0)
    limit = int(q.get("maxrequestsperday") or 0)
    if not limit:
        return False
    return used >= (limit - tier["reserve"])


def _next_utc_midnight():
    """Kept for reference and tests. NOT used to schedule a quota wait any more — see
    QUOTA_RECHECK_SECS for why guessing the upstream reset time was a mistake."""
    now = time.time()
    return now + (86400 - (now % 86400)) + 60      # a minute past, to be safe


def status():
    con = con_db()
    q = lambda s: con.execute(s).fetchone()[0]     # noqa: E731
    out = {
        "games": q("SELECT COUNT(*) FROM ss_games"),
        "names": q("SELECT COUNT(*) FROM ss_names"),
        "roms": q("SELECT COUNT(*) FROM ss_roms"),
        "systems": q("SELECT COUNT(*) FROM ss_systems"),
        "systems_mapped": q("SELECT COUNT(*) FROM ss_systems "
                            "WHERE igdb_platform IS NOT NULL"),
        "cursor": int(get(con, "cursor", 0) or 0),
        "dead_run": int(get(con, "dead_run", 0) or 0),
        "cooldown_until": float(get(con, "cooldown_until", 0) or 0),
        "walk_complete": get(con, "walk_complete"),
        "db_bytes": os.path.getsize(DB) if os.path.exists(DB) else 0,
    }
    con.close()
    return out


def main(argv):
    if "--status" in argv:
        print(json.dumps(status(), indent=2))
        return 0
    if "--systems" in argv:
        con = con_db()
        print(json.dumps(sync_systems(con, config.screenscraper_creds()), indent=2))
        con.close()
        return 0
    mx = None
    if "--max-requests" in argv:
        mx = int(argv[argv.index("--max-requests") + 1])
    if "--refresh-stale" in argv:
        i = argv.index("--refresh-stale")
        days = int(argv[i + 1]) if len(argv) > i + 1 and argv[i + 1].isdigit() else 90
        print("ss_mirror: " + json.dumps(refresh_stale(days=days, max_requests=mx)),
              file=sys.stderr)
        return 0
    until = None
    if "--until-id" in argv:
        until = int(argv[argv.index("--until-id") + 1])
    res = walk(max_requests=mx, until_id=until)
    print("ss_mirror: " + json.dumps(res), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
