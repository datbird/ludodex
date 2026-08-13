#!/usr/bin/env python3
"""A local mirror of IGDB's game catalog, built once and kept current incrementally.

WHY A MIRROR. Matching a ROM library title-by-title means one rate-limited HTTP
round trip per game, down the NAME-SEARCH path — the fragile one the acceptance gate
refuses when it is unsure. A mirror inverts that: ~740 paginated requests bring the
whole catalog local (371,879 entries at the time of writing, 310,113 of them main
games), and every match after that is a local query. No rate limit, no per-title
request, and re-running a corrected matching rule costs a table scan instead of
33,000 searches.

  python3 igdb_mirror.py --full        # first build, resumable, id-keyset
  python3 igdb_mirror.py               # incremental: only what changed since last run
  python3 igdb_mirror.py --status      # cursor, counts, cooldown, nothing fetched
  python3 igdb_mirror.py --max-requests 200   # bounded chunk; resume later

PAGINATION IS KEYSET, NOT OFFSET. IGDB's `offset` degrades and is capped well below
370k, so every sweep walks `where id > <cursor>; sort id asc;` and persists the
cursor after each page. An interrupted run resumes exactly where it stopped, which
is what makes `--max-requests` a usable way to spread a build over several sittings.

INCREMENTAL USES A WATERMARK, CONSERVATIVELY. The next run asks for
`updated_at >= <the previous run's START>`, not its finish and not the newest
`updated_at` it saw. A record edited WHILE a sweep is running would otherwise fall in
the gap between "already passed that id" and "newer than the newest thing I saw".
Overlapping instead of gapping means some rows are re-fetched; an upsert makes that
free, and a missed row would not be.
"""
import json
import os
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config          # noqa: E402
import igdb           # noqa: E402
from titlenorm import norm   # noqa: E402

DB = os.path.join(DATA, "igdb-catalog.sqlite")
PAGE = 500                      # IGDB's per-request maximum
TARGET_PACE = 0.26              # ~4 req/s, IGDB's documented ceiling
MAX_PACE = 8.0                  # never crawl slower than this, even after many 429s
COOLDOWN_AFTER = 6              # consecutive 429s in one run that mean "stop today"
COOLDOWN_SECS = 30 * 60

# Deliberately lean. This is an IDENTITY mirror, not a metadata cache: igdb_meta
# already holds full records for games we own. Everything here exists to answer
# "which IGDB id is this title, on this platform, from this year" offline.
FIELDS = ("id,name,slug,game_type,first_release_date,updated_at,platforms,"
          "parent_game,version_parent,alternative_names.name")


def con_db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS games(
      id INTEGER PRIMARY KEY,
      name TEXT, slug TEXT, norm_key TEXT,
      game_type INTEGER, year INTEGER,
      platforms TEXT,                 -- csv of IGDB platform ids
      parent_game INTEGER, version_parent INTEGER,
      updated_at INTEGER, seen_at INTEGER);
    CREATE INDEX IF NOT EXISTS ix_games_norm ON games(norm_key);
    CREATE INDEX IF NOT EXISTS ix_games_upd  ON games(updated_at);
    -- Alternative names are why this mirror is worth having: 'Akumajou Dracula X'
    -- and 'Rondo of Blood' are the same id, and an offline matcher can only know
    -- that if the aliases are local too.
    CREATE TABLE IF NOT EXISTS alt_names(
      game_id INTEGER, name TEXT, norm_key TEXT,
      PRIMARY KEY(game_id, name));
    CREATE INDEX IF NOT EXISTS ix_alt_norm ON alt_names(norm_key);
    CREATE TABLE IF NOT EXISTS platforms(
      id INTEGER PRIMARY KEY, name TEXT, abbreviation TEXT);
    CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT);
    """)
    con.commit()
    return con


def get(con, k, default=None):
    r = con.execute("SELECT v FROM state WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def put(con, k, v):
    con.execute("INSERT OR REPLACE INTO state(k,v) VALUES(?,?)", (k, str(v)))


def _auth():
    cid, csec = config.igdb_creds()
    if not (cid and csec):
        raise SystemExit("igdb_mirror: no IGDB credentials configured")
    tok, _ttl = igdb.get_token(cid, csec)
    return cid, csec, tok


def _upsert(con, rows, now):
    """Write a page. Alt names are replaced per game, never merged: IGDB removing an
    alias must remove it here too, or the mirror accumulates names the source has
    disowned and starts matching on them."""
    for g in rows:
        gid = g.get("id")
        if not gid:
            continue
        nm = g.get("name") or ""
        ts = g.get("first_release_date")
        con.execute(
            "INSERT INTO games(id,name,slug,norm_key,game_type,year,platforms,"
            "parent_game,version_parent,updated_at,seen_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "name=excluded.name, slug=excluded.slug, norm_key=excluded.norm_key, "
            "game_type=excluded.game_type, year=excluded.year, "
            "platforms=excluded.platforms, parent_game=excluded.parent_game, "
            "version_parent=excluded.version_parent, "
            "updated_at=excluded.updated_at, seen_at=excluded.seen_at",
            (gid, nm, g.get("slug"), norm(nm), g.get("game_type"),
             time.gmtime(ts).tm_year if ts else None,
             ",".join(str(p) for p in (g.get("platforms") or [])),
             g.get("parent_game"), g.get("version_parent"),
             g.get("updated_at"), now))
        alts = [a.get("name") for a in (g.get("alternative_names") or [])
                if a.get("name")]
        con.execute("DELETE FROM alt_names WHERE game_id=?", (gid,))
        for a in alts:
            con.execute("INSERT OR IGNORE INTO alt_names(game_id,name,norm_key) "
                        "VALUES(?,?,?)", (gid, a, norm(a)))


def sync_platforms(con, cid, tok):
    """The platform table is ~250 rows and changes about never, so it is fetched
    whenever it is empty and otherwise left alone."""
    if con.execute("SELECT COUNT(*) FROM platforms").fetchone()[0]:
        return 0
    n, last = 0, 0
    while True:
        rows = igdb.query("platforms", "fields id,name,abbreviation; "
                          "where id > %d; sort id asc; limit 500;" % last, cid, tok)
        if not rows:
            break
        for p in rows:
            con.execute("INSERT OR REPLACE INTO platforms(id,name,abbreviation) "
                        "VALUES(?,?,?)", (p["id"], p.get("name"),
                                          p.get("abbreviation")))
        last, n = rows[-1]["id"], n + len(rows)
    con.commit()
    return n


def sweep(full=False, max_requests=None, pace=TARGET_PACE, progress=True):
    """Pull pages until the source is exhausted or the request budget runs out.

    Returns a dict of what happened. Never raises on a throttle: a run that gets
    rate-limited records a cooldown and stops cleanly, because the cursor is durable
    and stopping early costs nothing but time."""
    con = con_db()
    cool = float(get(con, "cooldown_until", 0) or 0)
    if time.time() < cool:
        left = int(cool - time.time())
        print("igdb_mirror: cooling down for another %dm%02ds (a previous run was "
              "throttled hard)" % (left // 60, left % 60), file=sys.stderr)
        return {"skipped": "cooldown", "seconds_left": left}

    cid, _csec, tok = _auth()

    def _reauth(_cid):
        t, _ = igdb.get_token(cid, _csec)
        return t

    # Adaptive: start at the requested pace, and if this account is being throttled
    # anyway, slow the SUSTAINED rate rather than just backing off one request.
    # Persisted, so the next run starts at the pace that was actually working.
    pace = max(float(get(con, "pace", pace) or pace), TARGET_PACE)
    igdb.set_pace(pace)
    igdb._throttled[0] = 0

    started = int(time.time())
    if full:
        put(con, "cursor", 0)
        put(con, "full_started", started)
    cursor = int(get(con, "cursor", 0) or 0)
    since = 0 if full else int(get(con, "watermark", 0) or 0)
    sync_platforms(con, cid, tok)

    def _where(cur):
        # id keyset ALWAYS; the incremental filter rides alongside it so paging stays
        # on the unique, sorted column even when the result set is a slice.
        w = "id > %d" % cur
        return w + (" & updated_at >= %d" % since if since else "")

    reqs = seen = strikes = 0
    t0 = time.time()
    try:
        while max_requests is None or reqs < max_requests:
            body = ("fields %s; where %s; sort id asc; limit %d;"
                    % (FIELDS, _where(cursor), PAGE))
            rows = igdb.query("games", body, cid, tok, reauth=_reauth)
            reqs += 1
            if not rows:
                # Exhausted. Only NOW is the watermark advanced, and to this run's
                # START — see the module docstring on overlapping rather than gapping.
                put(con, "watermark", started)
                put(con, "cursor", 0)
                put(con, "last_full" if full else "last_incremental", started)
                con.commit()
                break
            _upsert(con, rows, started)
            cursor = rows[-1]["id"]
            seen += len(rows)
            put(con, "cursor", cursor)
            con.commit()
            if progress and reqs % 20 == 0:
                rate = seen / max(0.001, time.time() - t0)
                print("igdb_mirror: %d rows, %d requests, %.0f rows/s, cursor %d"
                      % (seen, reqs, rate, cursor), file=sys.stderr)
            # STRIKES ARE PER PAGE, and the counter has to be drained each page or it
            # can never accumulate: reading and clearing in the same breath is what
            # makes "six CONSECUTIVE throttled pages" mean that, rather than "six in
            # one page", which is a thing a single page cannot do.
            hits, igdb._throttled[0] = igdb._throttled[0], 0
            if hits:
                strikes += 1
                pace = min(MAX_PACE, pace * 1.5)
                igdb.set_pace(pace)
                put(con, "pace", pace)
            else:
                strikes = 0                  # a clean page ends the streak
            if strikes >= COOLDOWN_AFTER:
                put(con, "cooldown_until", time.time() + COOLDOWN_SECS)
                put(con, "pace", min(MAX_PACE, pace * 2))
                con.commit()
                print("igdb_mirror: throttled on %d pages in a row — stopping and "
                      "cooling down for %d minutes. Cursor %d is saved; just run it "
                      "again after." % (strikes, COOLDOWN_SECS // 60, cursor),
                      file=sys.stderr)
                break
    finally:
        con.commit()

    # A clean run earns its pace back, slowly. Nothing here ever goes below the
    # documented ceiling.
    if not strikes:
        put(con, "pace", max(TARGET_PACE, pace * 0.9))
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    con.close()
    return {"rows_seen": seen, "requests": reqs, "cursor": cursor,
            "elapsed": round(time.time() - t0, 1), "total_in_mirror": total,
            "mode": "full" if full else ("incremental" if since else "initial")}


def status():
    con = con_db()
    g = con.execute("SELECT COUNT(*) n, MAX(updated_at) u FROM games").fetchone()
    a = con.execute("SELECT COUNT(*) FROM alt_names").fetchone()[0]
    p = con.execute("SELECT COUNT(*) FROM platforms").fetchone()[0]
    out = {"games": g["n"], "alt_names": a, "platforms": p,
           "newest_updated_at": g["u"],
           "cursor": int(get(con, "cursor", 0) or 0),
           "watermark": int(get(con, "watermark", 0) or 0),
           "pace": float(get(con, "pace", TARGET_PACE) or TARGET_PACE),
           "cooldown_until": float(get(con, "cooldown_until", 0) or 0),
           "db_bytes": os.path.getsize(DB) if os.path.exists(DB) else 0}
    con.close()
    return out


def main(argv):
    if "--status" in argv:
        print(json.dumps(status(), indent=2))
        return 0
    mx = None
    if "--max-requests" in argv:
        mx = int(argv[argv.index("--max-requests") + 1])
    pace = TARGET_PACE
    if "--rps" in argv:
        pace = max(TARGET_PACE, 1.0 / max(0.1, float(argv[argv.index("--rps") + 1])))
    res = sweep(full="--full" in argv, max_requests=mx, pace=pace)
    print("igdb_mirror: " + json.dumps(res), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
