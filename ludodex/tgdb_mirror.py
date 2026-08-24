#!/usr/bin/env python3
"""Walk TheGamesDB's id space into a local catalogue.

WHY A WALK AND NOT A SEARCH: their `ByGameName` costs one request per title and cannot be
batched, so resolving a library through it is measured in years. `ByGameID` takes a
comma-delimited list and the server pages at 20 whatever you ask for — measured — so the
ENTIRE id space is 138,000 / 20 = 6,900 requests. On the $29 Developer tier's 12,000 a
month that is one evening, once, and then the catalogue is local forever.

WE WALK EVERY ID, INCLUDING THE ~13,400 WE ALREADY HAVE POINTERS FOR. Skipping them would
save about 670 requests and cost us their metadata, because the free hash map and Wikidata
gave us IDS ONLY. `fields` and `include=boxart` ride along on a call already being made,
so a known id is not a request saved — it is a request that returns everything else for
nothing.

THE GRAIN IS FINER THAN OURS AND THAT IS THE POINT. A TheGamesDB row is one per (title,
platform, REGION) — Sonic 2 has separate NTSC-U and PAL Genesis rows with different dates.
Nothing else we mirror models region as part of identity, so the columns are kept as they
arrive and `tgdb_normalize.pick_release` decides between them later, against the filename.
Collapsing them here would throw the distinction away at the only point it is free.

Resumable: the cursor is the last id block, in the db. 6,900 requests is long enough for
something to happen, and a walk that restarts from zero spends the allowance twice.
"""
import json
import os
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config                       # noqa: E402
import thegamesdb as tgdb           # noqa: E402
import tgdb_normalize as tn         # noqa: E402
from titlenorm import norm          # noqa: E402

DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
DB = os.path.join(DATA, "tgdb-catalog.sqlite")

# Measured 2026-08-16 by batched id sampling: ~90% of the space is populated to a ceiling
# between 135,000 and 138,000, and stone dead from 138,000 on. Walking to 138,000 covers
# it with a margin; DEAD_RUN below stops earlier if the catalogue ends sooner.
CEILING = 138000
BLOCK = 20                          # their page size, and the ByGameID batch size
# Consecutive empty blocks before we accept the catalogue has ended.
#
# THIS WAS 40 (800 ids) AND IT WAS WRONG, on a claim I had not measured: "the sampled
# space has no gap anywhere near that wide". Live, there is a dead run of roughly 3,000
# consecutive ids from ~56,980 to 60,000 — and then the catalogue resumes at 20/20 alive.
# The walk stopped in that gap, declared itself COMPLETE, and left 73,000 ids unwalked
# while reporting success. An absence read as an answer, from a threshold I invented.
#
# 500 blocks = 10,000 consecutive dead ids, three times the largest gap actually
# observed. Overshooting costs at most 500 requests once, at the true end of the
# catalogue; undershooting costs most of the catalogue and says nothing.
DEAD_RUN_STOP = 500


def con_db():
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS tgdb_games(
      id INTEGER PRIMARY KEY,
      name TEXT, norm_key TEXT, platform INTEGER,
      region_id INTEGER, country_id INTEGER,
      release_date TEXT, year INTEGER,
      players INTEGER, coop TEXT, esrb TEXT,
      genres TEXT, developers TEXT, publishers TEXT,
      youtube TEXT, os TEXT, min_spec TEXT,
      seen_at INTEGER);
    CREATE INDEX IF NOT EXISTS ix_tgdb_norm ON tgdb_games(norm_key);
    CREATE INDEX IF NOT EXISTS ix_tgdb_plat ON tgdb_games(platform);
    CREATE TABLE IF NOT EXISTS tgdb_art(
      game_id INTEGER, kind TEXT, url TEXT, width INTEGER, height INTEGER,
      PRIMARY KEY(game_id, url));
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


def store(con, row, art, vocab, now):
    gid = row.get("id")
    if not gid:
        return 0
    name = (row.get("game_name") or row.get("game_title") or "").strip()
    names = tgdb.resolve_names(row, vocab)
    date = (row.get("release_date") or "").strip()[:10]
    spec = {k: (row.get(k) or "").strip()
            for k in ("processor", "ram", "hdd", "video", "sound")}
    spec = {k: v for k, v in spec.items() if v}
    con.execute(
        "INSERT INTO tgdb_games(id,name,norm_key,platform,region_id,country_id,"
        "release_date,year,players,coop,esrb,genres,developers,publishers,youtube,"
        "os,min_spec,seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, norm_key=excluded.norm_key, "
        "platform=excluded.platform, region_id=excluded.region_id, "
        "country_id=excluded.country_id, release_date=excluded.release_date, "
        "year=excluded.year, players=excluded.players, coop=excluded.coop, "
        "esrb=excluded.esrb, genres=excluded.genres, developers=excluded.developers, "
        "publishers=excluded.publishers, youtube=excluded.youtube, os=excluded.os, "
        "min_spec=excluded.min_spec, seen_at=excluded.seen_at",
        (gid, name, norm(name), row.get("platform"),
         row.get("region_id"), row.get("country_id"), date or None,
         int(date[:4]) if date[:4].isdigit() else None,
         row.get("players"), row.get("coop"), row.get("rating"),
         ",".join(names["genres"]) or None,
         ",".join(names["developers"]) or None,
         ",".join(names["publishers"]) or None,
         (row.get("youtube") or "").strip() or None,
         (row.get("os") or "").strip() or None,
         json.dumps(spec) if spec else None, now))
    for a in (art.get(str(gid)) or []):
        if a.get("url"):
            con.execute("INSERT OR IGNORE INTO tgdb_art(game_id,kind,url,width,height) "
                        "VALUES(?,?,?,?,?)",
                        (gid, a["kind"], a["url"], a.get("width"), a.get("height")))
    return 1


def walk(max_requests=None, progress=True, until_id=None):
    """Walk the id space in blocks of 20, resumable. Never raises on an allowance stop."""
    con = con_db()
    if not tgdb.api_key():
        con.close()
        return {"skipped": "no api key"}
    try:
        vocab = tgdb.vocabulary()
    except tgdb.TGDBError as e:
        con.close()
        return {"skipped": "vocabulary failed: %s" % e}

    cursor = int(get(con, "cursor", 0) or 0)
    dead = int(get(con, "dead_run", 0) or 0)
    if get(con, "walk_complete"):
        # A finished walk sits past the stop line; a fresh run means the question of
        # whether there is MORE is open again. Same fix ss_mirror needed.
        dead = 0
        put(con, "walk_complete", "")
        con.commit()

    budget = tgdb.budget()
    if max_requests:
        budget = min(budget, max_requests)
    if budget <= 0:
        con.close()
        return {"skipped": "allowance", "cursor": cursor}

    # A re-run must look ABOVE where the last one stopped, because new games get new
    # (higher) ids. If the cursor already reached the ceiling, extend far enough that
    # DEAD_RUN_STOP gets a fair chance to prove the catalogue really has ended — a walk
    # that returns "complete" without having asked anything is not evidence of anything.
    top = until_id or max(CEILING, cursor + DEAD_RUN_STOP * BLOCK + 1)
    reqs = found = 0
    t0 = time.time()
    try:
        while cursor < top and reqs < budget and dead < DEAD_RUN_STOP:
            ids = list(range(cursor + 1, min(cursor + BLOCK, top) + 1))
            if not ids:
                break
            try:
                rows, art = tgdb.by_ids(ids)
            except tgdb.TGDBError as e:
                return _finish(con, cursor, dead, reqs, found, t0, e.kind)
            reqs += 1
            now = int(time.time())
            n = 0
            for row in rows:
                n += store(con, row, art, vocab, now)
            found += n
            dead = 0 if n else dead + 1
            cursor = ids[-1]
            put(con, "cursor", cursor)
            put(con, "dead_run", dead)
            con.commit()
            if progress and reqs % 50 == 0:
                print("tgdb_mirror: id %d | %d games | %d reqs | %.0fs"
                      % (cursor, found, reqs, time.time() - t0), file=sys.stderr)
        # ONLY THE DEAD RUN PROVES THE CATALOGUE HAS ENDED. Without an explicit
        # until_id, `top` is CEILING on the first pass — a number measured by sampling,
        # i.e. my guess — so stopping there says the walk ran out of PERMISSION, not
        # that the catalogue ran out of games. That wrote walk_complete="1" while the
        # last block was still returning 20 live games. It healed itself on the next
        # --walk (which extends `top` past the cursor), and in between the status lied:
        # the same shape as the ~3,000-id gap that declared COMPLETE at 50,268 of
        # 121,454 games.
        #
        # An until_id the CALLER gave is different — reaching it is doing what was
        # asked, so the run is complete. It still proves nothing about the catalogue,
        # so it does not write the mark either.
        if dead >= DEAD_RUN_STOP:
            why = "complete"
            put(con, "walk_complete", "1")
        elif cursor >= top:
            why = "complete" if until_id else "ceiling"
        else:
            why = "budget"
        return _finish(con, cursor, dead, reqs, found, t0, why)
    finally:
        con.close()


def _finish(con, cursor, dead, reqs, found, t0, why):
    put(con, "cursor", cursor)
    put(con, "dead_run", dead)
    put(con, "last_run", int(time.time()))
    put(con, "last_reason", why)
    con.commit()
    return {"stopped": why, "cursor": cursor, "requests": reqs, "games": found,
            "elapsed": round(time.time() - t0, 1)}


def status():
    if not os.path.exists(DB):
        return {"exists": False}
    con = con_db()
    try:
        return {
            "exists": True,
            "games": con.execute("SELECT COUNT(*) FROM tgdb_games").fetchone()[0],
            "art_refs": con.execute("SELECT COUNT(*) FROM tgdb_art").fetchone()[0],
            "with_region": con.execute(
                "SELECT COUNT(*) FROM tgdb_games WHERE region_id > 0").fetchone()[0],
            "cursor": get(con, "cursor"),
            "dead_run": get(con, "dead_run"),
            "complete": bool(get(con, "walk_complete")),
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
        print("tgdb_mirror: no catalogue yet — run --walk")
        return 0
    for k in ("games", "art_refs", "with_region", "cursor", "dead_run", "complete",
              "last_reason"):
        print("  %-14s %s" % (k, s[k]))
    print("  %-14s %.1f MB" % ("db", s["db_bytes"] / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
