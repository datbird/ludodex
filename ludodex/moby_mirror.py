#!/usr/bin/env python3
"""Walk MobyGames into a local catalogue, so 4.6 hours of subscription buys something
permanent.

WHY A MIRROR AND NOT JUST WRITING IDS STRAIGHT INTO THE INDEX: `matchindex.build()` opens
with `DELETE FROM identity_key` and re-derives everything from local sources — the file
calls itself "a rebuildable supplement" and means it. Ids written directly into the index
are gone the next time it is rebuilt, and getting them back costs another four and a half
hours against a paid key. The mirror is simply the durable copy that makes the walk a
ONE-TIME cost, exactly as `igdb-catalog.sqlite` and `ss-catalog.sqlite` already do.

IT IS DELIBERATELY SLIM. ScreenScraper's mirror is 316 MB because it carries 766,000 ROM
hashes, which is its whole value. MobyGames' value here is identity, so this stores id,
title, alternate titles, platforms, year and score — and NOT the descriptions or art
URLs, which are most of the bytes and can be re-fetched per game on demand. Roughly
20-30 MB for 332,414 games. `mobygames_store_payload` keeps the raw record instead, for
anyone who would rather spend disk than a second walk.

THE WALK IS PER PLATFORM, and that is not a preference. Unfiltered paging silently
returns an empty list past offset ~205,000 with no error — measured live — so a global
walk would stop 124,000 games short and report success. Platform windows do not hit it,
and they hand back (game, platform) pairs directly, which is the grain ludodex wants
anyway.

RESUMABLE ACROSS RESTARTS, because 4.6 hours is long enough for something to happen. The
cursor is (platform_id, offset) in the db, and finished platforms are recorded, so a
relaunch continues rather than starting the subscription clock again.
"""
import json
import os
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config                       # noqa: E402
import mobygames as mg              # noqa: E402
from titlenorm import norm          # noqa: E402

DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
DB = os.path.join(DATA, "moby-catalog.sqlite")


def con_db():
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS moby_games(
      id INTEGER PRIMARY KEY,
      title TEXT, norm_key TEXT, year INTEGER, score REAL,
      genres TEXT, payload TEXT, seen_at INTEGER);
    CREATE INDEX IF NOT EXISTS ix_moby_norm ON moby_games(norm_key);
    -- One row per (game, platform): the grain the walk returns and the grain ludodex
    -- keys entries on. Collapsing it here would throw away the fact both sides want.
    CREATE TABLE IF NOT EXISTS moby_platforms(
      game_id INTEGER, platform_id INTEGER, platform_name TEXT, first_release TEXT,
      PRIMARY KEY(game_id, platform_id));
    CREATE INDEX IF NOT EXISTS ix_mp_plat ON moby_platforms(platform_id);
    CREATE TABLE IF NOT EXISTS moby_alt(
      game_id INTEGER, name TEXT, norm_key TEXT,
      PRIMARY KEY(game_id, name));
    CREATE INDEX IF NOT EXISTS ix_ma_norm ON moby_alt(norm_key);
    CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT);
    """)
    con.commit()
    return con


def get(con, k, d=None):
    r = con.execute("SELECT v FROM state WHERE k=?", (k,)).fetchone()
    return r["v"] if r else d


def put(con, k, v):
    con.execute("INSERT INTO state(k,v) VALUES(?,?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def _year_of(rec):
    """The EARLIEST release year across platforms — the same rule extract_metadata uses,
    so the mirror and the attribute merge cannot disagree about a game's year."""
    ys = sorted(str(p.get("first_release_date") or "")[:4]
                for p in (rec.get("platforms") or []))
    ys = [y for y in ys if len(y) == 4 and y.isdigit()]
    return int(ys[0]) if ys else None


def store(con, rec, now, keep_payload=False):
    gid = rec.get("game_id")
    if not gid:
        return 0
    title = (rec.get("title") or "").strip()
    genres = [g.get("genre_name") for g in (rec.get("genres") or [])
              if (g.get("genre_category") or "").strip().lower() == "basic genres"
              and g.get("genre_name")]
    try:
        score = float(rec.get("moby_score")) if rec.get("moby_score") not in (None, "") \
            else None
    except (TypeError, ValueError):
        score = None
    con.execute(
        "INSERT INTO moby_games(id,title,norm_key,year,score,genres,payload,seen_at) "
        "VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
        "norm_key=excluded.norm_key, year=excluded.year, score=excluded.score, "
        "genres=excluded.genres, payload=excluded.payload, seen_at=excluded.seen_at",
        (gid, title, norm(title), _year_of(rec), score,
         ",".join(genres) if genres else None,
         json.dumps(rec, ensure_ascii=False) if keep_payload else None, now))
    for p in (rec.get("platforms") or []):
        if p.get("platform_id") is None:
            continue
        con.execute(
            "INSERT OR REPLACE INTO moby_platforms(game_id,platform_id,platform_name,"
            "first_release) VALUES(?,?,?,?)",
            (gid, p["platform_id"], p.get("platform_name"),
             str(p.get("first_release_date") or "") or None))
    for a in (rec.get("alternate_titles") or []):
        nm = (a.get("title") or "").strip()
        if nm:
            con.execute("INSERT OR IGNORE INTO moby_alt(game_id,name,norm_key) "
                        "VALUES(?,?,?)", (gid, nm, norm(nm)))
    return 1


def walk(max_requests=None, progress=True, platform_ids=None):
    """Walk the catalogue platform by platform, resumable. Never raises on a rate limit.

    The cursor is (platform, offset) and finished platforms are recorded, so a relaunch
    picks up where it stopped rather than spending the subscription again."""
    con = con_db()
    keep = config.get_bool("mobygames_store_payload", False)
    fmt = (config.get("mobygames_walk_format") or "normal").strip() or "normal"
    if not mg.api_key():
        con.close()
        return {"skipped": "no api key"}

    try:
        plats = platform_ids or [p["platform_id"] for p in mg.platforms()]
    except mg.MobyError as e:
        con.close()
        return {"skipped": "platform list failed: %s" % e}
    done = set(json.loads(get(con, "done_platforms", "[]")))
    cur_plat = int(get(con, "cursor_platform", 0) or 0)
    cur_off = int(get(con, "cursor_offset", 0) or 0)

    reqs = games = 0
    failed = []                 # platforms that did not ANSWER; never marked finished
    t0 = time.time()
    mg.bulk_mode(True)          # a full walk is the long job the burst reserve guards
    try:
        for pid in plats:
            if pid in done:
                continue
            off = cur_off if pid == cur_plat else 0
            while True:
                if max_requests and reqs >= max_requests:
                    return _finish(con, pid, off, done, reqs, games, t0, "budget",
                                   failed)
                try:
                    rows = mg.games(offset=off, limit=mg.PAGE, fmt=fmt, platform=pid)
                except mg.MobyError as e:
                    if e.kind == "notfound":
                        # THE PLATFORM DID NOT ANSWER, so nothing is known about whether
                        # it is finished. Recording it as done would truncate the walk
                        # silently — which is what a bare 404 used to do. Leave it out of
                        # `done`, say so, and move to the next one.
                        failed.append(pid)
                        print("moby_mirror: platform %s did not answer (%s) — left "
                              "unfinished for the next run" % (pid, e), file=sys.stderr)
                        break
                    if e.kind in ("quota", "error"):
                        # Stopping is free: the cursor is durable, so this costs time and
                        # nothing else. Recording where we were is the whole point.
                        return _finish(con, pid, off, done, reqs, games, t0, e.kind,
                                       failed)
                    raise
                reqs += 1
                if not rows:
                    break
                for rec in rows:
                    if isinstance(rec, dict):
                        games += store(con, rec, int(time.time()), keep)
                off += len(rows)
                put(con, "cursor_platform", pid)
                put(con, "cursor_offset", off)
                con.commit()
                if progress and reqs % 20 == 0:
                    print("moby_mirror: platform %s off %d | %d games | %d reqs | %.0fs"
                          % (pid, off, games, reqs, time.time() - t0), file=sys.stderr)
                if len(rows) < mg.PAGE:
                    break
            if pid in failed:
                continue                  # it never answered; it is not finished
            done.add(pid)
            put(con, "done_platforms", json.dumps(sorted(done)))
            put(con, "cursor_offset", 0)
            con.commit()
        # "complete" is a claim about the WHOLE catalogue, so a platform that never
        # answered forfeits it — otherwise the one silently-truncated platform reads
        # exactly like a finished walk.
        return _finish(con, None, 0, done, reqs, games, t0,
                       "complete" if not failed else "incomplete", failed)
    finally:
        mg.bulk_mode(False)     # the reserve is for interactive callers again
        con.close()


def _finish(con, pid, off, done, reqs, games, t0, why, failed=()):
    if pid is not None:
        put(con, "cursor_platform", pid)
        put(con, "cursor_offset", off)
    put(con, "done_platforms", json.dumps(sorted(done)))
    put(con, "last_run", int(time.time()))
    put(con, "last_reason", why)
    con.commit()
    out = {"stopped": why, "requests": reqs, "games": games,
           "platforms_done": len(done), "elapsed": round(time.time() - t0, 1)}
    if failed:
        # Named, not counted: the next run needs to know WHICH platform to re-ask.
        out["platform_errors"] = sorted(failed)
    return out


def status():
    if not os.path.exists(DB):
        return {"exists": False}
    con = con_db()
    try:
        return {
            "exists": True,
            "games": con.execute("SELECT COUNT(*) FROM moby_games").fetchone()[0],
            "game_platforms": con.execute(
                "SELECT COUNT(*) FROM moby_platforms").fetchone()[0],
            "alt_titles": con.execute("SELECT COUNT(*) FROM moby_alt").fetchone()[0],
            "platforms_done": len(json.loads(get(con, "done_platforms", "[]"))),
            "cursor_platform": get(con, "cursor_platform"),
            "cursor_offset": get(con, "cursor_offset"),
            "last_reason": get(con, "last_reason"),
            "db_bytes": os.path.getsize(DB),
        }
    finally:
        con.close()


def main(argv):
    if "--walk" in argv:
        n = None
        if "--max-requests" in argv:
            n = int(argv[argv.index("--max-requests") + 1])
        print(json.dumps(walk(max_requests=n), indent=2))
        return 0
    s = status()
    if not s.get("exists"):
        print("moby_mirror: no catalogue yet — run --walk")
        return 0
    for k in ("games", "game_platforms", "alt_titles", "platforms_done",
              "cursor_platform", "cursor_offset", "last_reason"):
        print("  %-18s %s" % (k, s[k]))
    print("  %-18s %.1f MB" % ("db", s["db_bytes"] / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
