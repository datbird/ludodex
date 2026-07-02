"""Match ludodex games to RetroAchievements and pull achievements + progress.

No ROM hashes on this host, so matching is console + normalized-title based:
for each RA-supported platform in the catalog we pull RA's game list (only
titles that actually have achievements) and match by norm_key. Then, for matched
games, GetGameInfoAndUserProgress gives the full achievement set + which ones the
configured user has earned. All RA calls are throttled by ra._throttle().

Data lands in ra.sqlite (gitignored). Usage:
    python ra_fetch.py match [--platform snes]     # build ra_games
    python ra_fetch.py pull  [--limit 50] [--refresh]  # fetch achievements
    python ra_fetch.py all   [--limit 50]          # match then pull
"""

import datetime
import os
import sqlite3
import sys

import config
import ra
from titlenorm import norm

# Use the local repo copies (config.library_db points at the Deck producer path).
DIR = os.path.dirname(os.path.abspath(__file__))
RA_DB = os.path.join(DIR, "ra.sqlite")
LIB_DB = os.path.join(DIR, "game-library.sqlite")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _lib():
    con = sqlite3.connect(LIB_DB)
    con.row_factory = sqlite3.Row
    return con


def _ra():
    con = sqlite3.connect(RA_DB)
    con.executescript("""
      CREATE TABLE IF NOT EXISTS ra_games(
        norm_key TEXT PRIMARY KEY, ra_id INTEGER, ra_title TEXT,
        console_id INTEGER, matched_at TEXT);
      CREATE TABLE IF NOT EXISTS ra_ach(
        norm_key TEXT, ra_ach_id INTEGER, title TEXT, description TEXT,
        points INTEGER, badge TEXT, earned INTEGER, earned_date TEXT,
        PRIMARY KEY(norm_key, ra_ach_id));
      CREATE TABLE IF NOT EXISTS ra_progress(
        norm_key TEXT PRIMARY KEY, ra_id INTEGER, num_ach INTEGER,
        num_earned INTEGER, pulled_at TEXT);
      CREATE INDEX IF NOT EXISTS ix_ach_nk ON ra_ach(norm_key);
    """)
    con.row_factory = sqlite3.Row
    return con


def platforms_in_catalog(lib):
    rows = lib.execute(
        "SELECT DISTINCT platform FROM sources WHERE source='emulation' "
        "AND platform!=''")
    return sorted(r["platform"] for r in rows if r["platform"] in ra.CONSOLE_ID)


def match(only_platform=None):
    lib, rdb = _lib(), _ra()
    total = 0
    plats = [only_platform] if only_platform else platforms_in_catalog(lib)
    for plat in plats:
        cid = ra.CONSOLE_ID.get(plat)
        if not cid:
            print("skip %r — no RA console mapping" % plat, file=sys.stderr)
            continue
        try:
            games = ra.get_game_list(cid)
        except Exception as e:
            print("RA game list %s (%s): %s" % (plat, cid, e), file=sys.stderr)
            continue
        ra_by_norm = {}
        for g in games:
            ra_by_norm.setdefault(norm(g.get("Title", "")), (g["ID"], g["Title"]))
        owned = lib.execute(
            "SELECT DISTINCT g.norm_key, g.canonical_title FROM games g "
            "JOIN sources s ON s.game_id=g.id "
            "WHERE s.source='emulation' AND s.platform=?", (plat,)).fetchall()
        n = 0
        for row in owned:
            hit = ra_by_norm.get(row["norm_key"])
            if hit:
                rdb.execute("INSERT OR REPLACE INTO ra_games VALUES(?,?,?,?,?)",
                            (row["norm_key"], hit[0], hit[1], cid, _now()))
                n += 1
        rdb.commit()
        total += n
        print("%-16s %5d RA games / %5d owned -> %4d matched"
              % (plat, len(ra_by_norm), len(owned), n))
    print("matched %d games total" % total)
    lib.close(); rdb.close()


def pull(limit=None, refresh=False):
    rdb = _ra()
    q = ("SELECT g.norm_key, g.ra_id FROM ra_games g "
         + ("" if refresh else
            "LEFT JOIN ra_progress p ON p.norm_key=g.norm_key WHERE p.norm_key IS NULL ")
         + "ORDER BY g.norm_key")
    todo = rdb.execute(q).fetchall()
    if limit:
        todo = todo[:limit]
    print("pulling achievements for %d games…" % len(todo))
    done = 0
    for row in todo:
        nk, rid = row["norm_key"], row["ra_id"]
        try:
            info = ra.get_user_progress(rid)
        except Exception as e:
            print("  %s (RA %s): %s" % (nk, rid, e), file=sys.stderr)
            continue
        achs = info.get("Achievements") or {}
        earned = 0
        rdb.execute("DELETE FROM ra_ach WHERE norm_key=?", (nk,))
        for aid, a in achs.items():
            got = bool(a.get("DateEarned") or a.get("DateEarnedHardcore"))
            earned += int(got)
            rdb.execute("INSERT OR REPLACE INTO ra_ach VALUES(?,?,?,?,?,?,?,?)",
                        (nk, int(aid), a.get("Title", ""), a.get("Description", ""),
                         int(a.get("Points") or 0), a.get("BadgeName", ""),
                         int(got), a.get("DateEarned") or a.get("DateEarnedHardcore")))
        rdb.execute("INSERT OR REPLACE INTO ra_progress VALUES(?,?,?,?,?)",
                    (nk, rid, len(achs), earned, _now()))
        rdb.commit()
        done += 1
        if done % 10 == 0:
            print("  …%d/%d" % (done, len(todo)))
    print("pulled %d games" % done)
    rdb.close()


def main(argv):
    if not ra.creds()[0]:
        print("RetroAchievements not configured (Settings > Services)", file=sys.stderr)
        return 2
    cmd = argv[1] if len(argv) > 1 else "all"
    plat = argv[argv.index("--platform") + 1] if "--platform" in argv else None
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    refresh = "--refresh" in argv
    if cmd in ("match", "all"):
        match(plat)
    if cmd in ("pull", "all"):
        pull(limit, refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
