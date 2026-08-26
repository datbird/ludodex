#!/usr/bin/env python3
"""A local mirror of IGDB's game catalog, built once and kept current incrementally.

WHY A MIRROR. Matching a ROM library title-by-title means one rate-limited HTTP
round trip per game, down the NAME-SEARCH path — the fragile one the acceptance gate
refuses when it is unsure. A mirror inverts that: ~740 paginated requests bring the
whole catalog local (371,879 entries at the time of writing, 310,113 of them main
games), and every match after that is a local query. No rate limit, no per-title
request, and re-running a corrected matching rule costs a table scan instead of
33,000 searches.

  python3 ludodex/igdb_mirror.py --full        # first build, resumable, id-keyset
  python3 ludodex/igdb_mirror.py               # incremental: only what changed since last run
  python3 ludodex/igdb_mirror.py --status      # cursor, counts, cooldown, nothing fetched
  python3 ludodex/igdb_mirror.py --max-requests 200   # bounded chunk; resume later

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
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
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


def _ro():
    """Read-only connection to the mirror, or None when it has not been built yet.

    The write path (`con_db`) CREATEs its tables, which is wrong for a reader: a caller
    asking a question of a mirror that does not exist should get "I don't know", not a
    freshly created empty database on disk.
    """
    if not os.path.exists(DB):
        return None
    try:
        return sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    except sqlite3.Error:
        return None


def fold_graph():
    """{igdb_id: (game_type, version_parent, parent_game)} for the whole mirror.

    Feeds `cardkey.fold_root`, which decides which CARD an entry sits on. Read from the
    mirror rather than metadata-cache for the same reason `build_library._igdb_addon_parents`
    is: `igdb.GAME_FIELDS` never requested `version_parent`, so the cache cannot answer
    this at all, and `version_parent` is the column that carries editions.
    """
    con = _ro()
    if con is None:
        return {}
    # fetchall, then build. A `for row in execute(...)` that raises PART WAY THROUGH
    # leaves a HALF-BUILT dict, and returning that is worse than returning nothing: some
    # cards fold and some do not, differently on each run, with no error anywhere. This
    # answer is complete or it is empty.
    try:
        rows = con.execute(
            "SELECT id, game_type, version_parent, parent_game FROM games").fetchall()
    except sqlite3.OperationalError:
        return {}                           # mirror predates the columns
    finally:
        con.close()
    return {int(iid): (gt, vp, pg) for iid, gt, vp, pg in rows}


def names(ids=None):
    """{igdb_id: name} from the mirror, for the card-title rule.

    PASS THE IDS YOU NEED. The whole table is 371,978 rows and costs 71 MB resident,
    while a rebuild asks about the few thousand ids that are actually card roots. This
    runs on hardware people self-host on, so the unfiltered load is the fallback, not
    the default.
    """
    con = _ro()
    if con is None:
        return {}
    try:
        if ids is None:
            rows = con.execute("SELECT id, name FROM games").fetchall()
        else:
            want = sorted({int(i) for i in ids})
            if not want:
                return {}
            rows = []
            for i in range(0, len(want), 900):      # SQLite's variable limit is 999
                chunk = want[i:i + 900]
                rows += con.execute(
                    "SELECT id, name FROM games WHERE id IN (%s)"
                    % ",".join("?" * len(chunk)), chunk).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()
    return {int(iid): nm for iid, nm in rows}          # complete, or empty


def title_index():
    """{norm_key: igdb_id} for MAIN GAMES only, so an entry no provider matched can
    still find its card by title (`cardkey.card_key_for_title`).

    Restricted to `game_type=0` on purpose. The index answers "which game is this the
    edition OF", and an edition must never be the answer to that. A duplicate norm_key
    keeps the LOWEST id, which is the earliest record and in practice the original.

    This feeds the CARD only. It never binds an identity, so it is deliberately looser
    than `matchgate`, which stays untouched.
    """
    con = _ro()
    if con is None:
        return {}
    try:
        rows = con.execute(
            "SELECT norm_key, MIN(id) FROM games WHERE game_type=0 "
            "AND norm_key IS NOT NULL AND norm_key!='' GROUP BY norm_key").fetchall()
    except sqlite3.OperationalError:
        return {}                           # mirror predates the column
    finally:
        con.close()
    return {nk: int(iid) for nk, iid in rows}          # complete, or empty


def con_db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS games(
      id INTEGER PRIMARY KEY,
      name TEXT, slug TEXT, norm_key TEXT,
      game_type INTEGER, year INTEGER,
      -- The full unix timestamp, kept alongside the year. It arrives in the same
      -- payload the year is derived from, so truncating to a year at write time threw
      -- away month and day for free and would have cost a whole re-sweep to recover.
      -- Store what the source gave you; derive on the way out, not on the way in.
      first_release_date INTEGER,
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
    -- IGDB models an OS AS a platform, so "is this a system or an OS" is not a
    -- field on the game — it is platform_type on the platform. Linux and
    -- "PC (Microsoft Windows)" are Operating_system; Nintendo 64 is Console. Storing
    -- the type, family and generation is what lets a pool be sliced by any of them.
    CREATE TABLE IF NOT EXISTS platforms(
      id INTEGER PRIMARY KEY, name TEXT, abbreviation TEXT, alternative_name TEXT,
      platform_type TEXT, platform_family TEXT, generation INTEGER);
    -- The csv on games is fine for display and useless for joining. This is the
    -- indexed form, and it is what makes "every Switch game" a query instead of a scan.
    CREATE TABLE IF NOT EXISTS game_platforms(
      game_id INTEGER, platform_id INTEGER, PRIMARY KEY(game_id, platform_id));
    CREATE INDEX IF NOT EXISTS ix_gp_plat ON game_platforms(platform_id);
    -- Store identities, straight from IGDB's own join table. These are EXACT ids,
    -- not matches: a Steam appid needs no acceptance gate and cannot be wrong.
    -- Every source is kept, including stores ludodex has no importer for, because
    -- the row costs nothing now and re-pulling it later costs 1,352 requests.
    CREATE TABLE IF NOT EXISTS stores(id INTEGER PRIMARY KEY, name TEXT);
    CREATE TABLE IF NOT EXISTS external_ids(
      game_id INTEGER, source_id INTEGER, uid TEXT, name TEXT,
      PRIMARY KEY(game_id, source_id, uid));
    CREATE INDEX IF NOT EXISTS ix_ext_uid  ON external_ids(source_id, uid);
    CREATE INDEX IF NOT EXISTS ix_ext_game ON external_ids(game_id);
    CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT);
    """)
    # A table created by an earlier version keeps its old shape: CREATE TABLE IF NOT
    # EXISTS is a no-op, not a migration. Heal the columns added since, the same way
    # the media index does.
    for _tbl, _cols in (
            ("platforms", (("alternative_name", "TEXT"), ("platform_type", "TEXT"),
                           ("platform_family", "TEXT"), ("generation", "INTEGER"))),
            ("games", (("first_release_date", "INTEGER"),))):
        _have = {r[1] for r in con.execute("PRAGMA table_info(%s)" % _tbl)}
        for _c, _d in _cols:
            if _c not in _have:
                con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (_tbl, _c, _d))
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
            "INSERT INTO games(id,name,slug,norm_key,game_type,year,"
            "first_release_date,platforms,"
            "parent_game,version_parent,updated_at,seen_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "name=excluded.name, slug=excluded.slug, norm_key=excluded.norm_key, "
            "game_type=excluded.game_type, year=excluded.year, "
            "first_release_date=excluded.first_release_date, "
            "platforms=excluded.platforms, parent_game=excluded.parent_game, "
            "version_parent=excluded.version_parent, "
            "updated_at=excluded.updated_at, seen_at=excluded.seen_at",
            (gid, nm, g.get("slug"), norm(nm), g.get("game_type"),
             time.gmtime(ts).tm_year if ts else None, ts or None,
             ",".join(str(p) for p in (g.get("platforms") or [])),
             g.get("parent_game"), g.get("version_parent"),
             g.get("updated_at"), now))
        con.execute("DELETE FROM game_platforms WHERE game_id=?", (gid,))
        for p in (g.get("platforms") or []):
            con.execute("INSERT OR IGNORE INTO game_platforms(game_id,platform_id) "
                        "VALUES(?,?)", (gid, p))
        alts = [a.get("name") for a in (g.get("alternative_names") or [])
                if a.get("name")]
        con.execute("DELETE FROM alt_names WHERE game_id=?", (gid,))
        for a in alts:
            con.execute("INSERT OR IGNORE INTO alt_names(game_id,name,norm_key) "
                        "VALUES(?,?,?)", (gid, a, norm(a)))


def sync_platforms(con, cid, tok, force=False):
    """~250 rows that change about never — fetched when empty, when a new column has
    not been filled yet, or on demand."""
    have = con.execute("SELECT COUNT(*) FROM platforms").fetchone()[0]
    typed = con.execute("SELECT COUNT(*) FROM platforms "
                        "WHERE platform_type IS NOT NULL").fetchone()[0]
    if have and typed and not force:
        return 0
    n, last = 0, 0
    while True:
        rows = igdb.query("platforms", "fields id,name,abbreviation,alternative_name,"
                          "generation,platform_type.name,platform_family.name; "
                          "where id > %d; sort id asc; limit 500;" % last, cid, tok)
        if not rows:
            break
        for p in rows:
            con.execute(
                "INSERT OR REPLACE INTO platforms(id,name,abbreviation,"
                "alternative_name,platform_type,platform_family,generation) "
                "VALUES(?,?,?,?,?,?,?)",
                (p["id"], p.get("name"), p.get("abbreviation"),
                 p.get("alternative_name"),
                 (p.get("platform_type") or {}).get("name"),
                 (p.get("platform_family") or {}).get("name"),
                 p.get("generation")))
        last, n = rows[-1]["id"], n + len(rows)
    con.commit()
    return n


def backfill_game_platforms(con):
    """Derive the indexed join table from the csv already on `games`. Local only —
    the data was fetched once and re-fetching it to reshape it would be silly."""
    n = 0
    for gid, pl in con.execute("SELECT id, platforms FROM games WHERE platforms!=''"):
        for x in (pl or "").split(","):
            if x.strip().isdigit():
                con.execute("INSERT OR IGNORE INTO game_platforms(game_id,platform_id) "
                            "VALUES(?,?)", (gid, int(x)))
                n += 1
    con.commit()
    return n


def sweep_external(max_requests=None, pace=TARGET_PACE, progress=True, full=False):
    """Pull IGDB's game<->store join table — ~676k rows across every store it knows,
    including ones ludodex has no importer for.

    This is the cheapest identity in the whole system: a store id is EXACT. It needs
    no name search, no acceptance gate, and cannot be a wrong bind — which is why
    2,070 of the library's 2,210 identities already come from one, resolved 200 at a
    time. Pulled whole, every game the user will ever buy on these stores is matched
    before they own it.

    One caveat this table makes visible: a store id identifies an EDITION, and one
    ludodex norm_key can span several. 'bioshock' owns appid 7670 (IGDB 20, 2007) and
    409710 (IGDB 34293, Remastered). Both are correct; they are answers to different
    questions. Resolve from a SOURCE ROW, never from a norm_key.

    INCREMENTAL, like the games sweep — `full=True` re-pulls everything. What it still
    cannot see is a pairing DELETED upstream: IGDB reports those only through its own
    deleted-entries endpoint, which nothing here calls, so a removed store id survives
    in the mirror until a rebuild."""
    con = con_db()
    left = _Pacer.cooling(con)
    if left:
        print("igdb_mirror: cooling down for another %dm%02ds (a previous run was "
              "throttled hard)" % (left // 60, left % 60), file=sys.stderr)
        return {"skipped": "cooldown", "seconds_left": left}

    cid, csec, tok = _auth()

    def _reauth(_cid):
        t, _ = igdb.get_token(cid, csec)
        return t

    pacer = _Pacer(con, pace)
    for s in igdb.query("external_game_sources", "fields id,name; limit 100;",
                        cid, tok, reauth=_reauth):
        con.execute("INSERT OR REPLACE INTO stores(id,name) VALUES(?,?)",
                    (s["id"], s.get("name")))
    con.commit()

    started = int(time.time())
    cursor = int(get(con, "ext_cursor", 0) or 0)
    # SAME WATERMARK DISCIPLINE AS THE GAMES SWEEP, which this had none of. Without it
    # `ext_cursor` reset to 0 on completion and the next run re-pulled all ~1,352 pages
    # of a table it already held — the exact cost the schema comment warns about — while
    # still being unable to see that a pairing had CHANGED. The pass's start is recorded
    # at cursor 0 and carried across resumes, for the reason sweep() explains.
    if cursor == 0:
        put(con, "ext_pass_started", started)
        con.commit()
    pass_started = int(get(con, "ext_pass_started", started) or started)
    since = 0 if full else int(get(con, "ext_watermark", 0) or 0)
    reqs = rows_seen = 0
    t0 = time.time()
    try:
        while max_requests is None or reqs < max_requests:
            where = "id > %d" % cursor + (
                " & updated_at >= %d" % since if since else "")
            rows = igdb.query(
                "external_games",
                "fields id,game,uid,external_game_source,name; where %s; "
                "sort id asc; limit %d;" % (where, PAGE), cid, tok, reauth=_reauth)
            reqs += 1
            if not rows:
                put(con, "ext_watermark", pass_started)
                put(con, "ext_cursor", 0)
                put(con, "ext_done", int(time.time()))
                con.commit()
                break
            for r in rows:
                if not (r.get("game") and r.get("uid")):
                    continue
                # REPLACE, not IGNORE: a row already held could never be corrected, so a
                # store entry that was renamed upstream kept our first copy forever.
                # (A pairing that was DELETED upstream still cannot be seen — IGDB only
                # reports that through its own deleted-entries endpoint.)
                con.execute(
                    "INSERT OR REPLACE INTO external_ids(game_id,source_id,uid,name) "
                    "VALUES(?,?,?,?)",
                    (r["game"], r.get("external_game_source"), str(r["uid"]),
                     r.get("name")))
            cursor = rows[-1]["id"]
            rows_seen += len(rows)
            put(con, "ext_cursor", cursor)
            con.commit()
            if progress and reqs % 40 == 0:
                print("igdb_mirror: %d external ids, %d requests, cursor %d"
                      % (rows_seen, reqs, cursor), file=sys.stderr)
            if not pacer.page():
                break
    finally:
        con.commit()
    pacer.done()
    total = con.execute("SELECT COUNT(*) FROM external_ids").fetchone()[0]
    con.close()
    return {"rows_seen": rows_seen, "requests": reqs, "cursor": cursor,
            "elapsed": round(time.time() - t0, 1), "total_external_ids": total}


class _Pacer:
    """The throttle discipline both sweeps run under, in one place.

    Two sweeps hammering the same rate limit with two copies of this logic is how
    one of them quietly ends up without the cooldown — so the games sweep and the
    external-ids sweep share it. State lives in the db, not the object: the pace
    that was actually working survives the process that discovered it."""

    def __init__(self, con, pace=TARGET_PACE):
        self.con = con
        # Adaptive: start at the requested pace, and if this account is being
        # throttled anyway, slow the SUSTAINED rate rather than just backing off one
        # request. Persisted, so the next run starts where this one ended up.
        self.pace = max(float(get(con, "pace", pace) or pace), TARGET_PACE)
        self.strikes = 0
        igdb.set_pace(self.pace)
        igdb._throttled[0] = 0

    @staticmethod
    def cooling(con):
        """Seconds left on a cooldown a previous run earned; 0 when clear."""
        return max(0, int(float(get(con, "cooldown_until", 0) or 0) - time.time()))

    def page(self):
        """Call after each page. -> False when the run should stop and cool down.

        STRIKES ARE PER PAGE, and the counter has to be drained each page or it can
        never accumulate: reading and clearing in the same breath is what makes "six
        CONSECUTIVE throttled pages" mean that, rather than "six in one page", which
        is a thing a single page cannot do."""
        hits, igdb._throttled[0] = igdb._throttled[0], 0
        if hits:
            self.strikes += 1
            self.pace = min(MAX_PACE, self.pace * 1.5)
            igdb.set_pace(self.pace)
            put(self.con, "pace", self.pace)
        else:
            self.strikes = 0                 # a clean page ends the streak
        if self.strikes >= COOLDOWN_AFTER:
            put(self.con, "cooldown_until", time.time() + COOLDOWN_SECS)
            put(self.con, "pace", min(MAX_PACE, self.pace * 2))
            self.con.commit()
            print("igdb_mirror: throttled on %d pages in a row — stopping and cooling "
                  "down for %d minutes. The cursor is saved; just run it again after."
                  % (self.strikes, COOLDOWN_SECS // 60), file=sys.stderr)
            return False
        return True

    def done(self):
        """A clean run earns its pace back, slowly. Never below the documented
        ceiling."""
        if not self.strikes:
            put(self.con, "pace", max(TARGET_PACE, self.pace * 0.9))
        self.con.commit()


def sweep(full=False, max_requests=None, pace=TARGET_PACE, progress=True):
    """Pull pages until the source is exhausted or the request budget runs out.

    Returns a dict of what happened. Never raises on a throttle: a run that gets
    rate-limited records a cooldown and stops cleanly, because the cursor is durable
    and stopping early costs nothing but time."""
    con = con_db()
    left = _Pacer.cooling(con)
    if left:
        print("igdb_mirror: cooling down for another %dm%02ds (a previous run was "
              "throttled hard)" % (left // 60, left % 60), file=sys.stderr)
        return {"skipped": "cooldown", "seconds_left": left}

    cid, _csec, tok = _auth()

    def _reauth(_cid):
        t, _ = igdb.get_token(cid, _csec)
        return t

    pacer = _Pacer(con, pace)
    started = int(time.time())
    if full:
        put(con, "cursor", 0)
        put(con, "full_started", started)
    cursor = int(get(con, "cursor", 0) or 0)
    # THE WATERMARK BELONGS TO THE PASS, NOT TO THE RUN THAT HAPPENS TO FINISH IT.
    #
    # A pass is one walk of id 0 -> exhaustion under one `since` filter, and it may take
    # several runs. Run 1 (start T1) covers ids 0..C and is interrupted; run 2 (start T2)
    # resumes at C. A row with id < C edited between T1 and T2 was invisible to run 1 —
    # it had already walked past that id — and is below run 2's cursor. Closing the
    # window at T2 asks for `>= T2` next time and loses that edit until a --full. So the
    # pass's start is recorded when the cursor is at 0, carried across resumes, and THAT
    # is what the watermark advances to. Overlapping re-reads a few rows; gapping loses
    # them.
    if cursor == 0:
        put(con, "pass_started", started)
        con.commit()
    pass_started = int(get(con, "pass_started", started) or started)
    since = 0 if full else int(get(con, "watermark", 0) or 0)
    sync_platforms(con, cid, tok)

    def _where(cur):
        # id keyset ALWAYS; the incremental filter rides alongside it so paging stays
        # on the unique, sorted column even when the result set is a slice.
        w = "id > %d" % cur
        return w + (" & updated_at >= %d" % since if since else "")

    reqs = seen = 0
    t0 = time.time()
    try:
        while max_requests is None or reqs < max_requests:
            body = ("fields %s; where %s; sort id asc; limit %d;"
                    % (FIELDS, _where(cursor), PAGE))
            rows = igdb.query("games", body, cid, tok, reauth=_reauth)
            reqs += 1
            if not rows:
                # Exhausted. Only NOW is the watermark advanced, and to the PASS's
                # start — see the module docstring on overlapping rather than gapping,
                # and `pass_started` above for why the run's own start is not it.
                put(con, "watermark", pass_started)
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
            if not pacer.page():
                break
    finally:
        con.commit()

    pacer.done()
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
    ext = con.execute("SELECT COUNT(*) FROM external_ids").fetchone()[0]
    gp = con.execute("SELECT COUNT(*) FROM game_platforms").fetchone()[0]
    frd = con.execute("SELECT COUNT(*) FROM games "
                      "WHERE first_release_date IS NOT NULL").fetchone()[0]
    out = {"games": g["n"], "alt_names": a, "platforms": p,
           "first_release_dates": frd,
           "external_ids": ext, "game_platforms": gp,
           "ext_cursor": int(get(con, "ext_cursor", 0) or 0),
           "ext_watermark": int(get(con, "ext_watermark", 0) or 0),
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
    if "--external" in argv:
        # --full re-pulls the whole join table (1,352 requests); without it the sweep
        # asks only for what changed since the last completed pass.
        res = sweep_external(max_requests=mx, full="--full" in argv)
        print("igdb_mirror: " + json.dumps(res), file=sys.stderr)
        return 0
    if "--backfill-platforms" in argv:
        con = con_db()
        print("igdb_mirror: %d game-platform rows" % backfill_game_platforms(con),
              file=sys.stderr)
        con.close()
        return 0
    res = sweep(full="--full" in argv, max_requests=mx, pace=pace)
    print("igdb_mirror: " + json.dumps(res), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
