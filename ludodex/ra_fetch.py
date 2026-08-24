"""Match ludodex games to RetroAchievements and pull achievements + progress.

Matching is console + title based, and it goes through the SAME acceptance gate every
other provider does. It used not to: `ra_by_norm.setdefault(norm(Title), ...)` accepted a
candidate on bare norm_key equality — no platform, no year, no gate — and three things
followed from that.

  * RA'S LIST IS NOT ALL GAMES. It carries `~Hack~`, `~Prototype~`, `~Demo~`,
    `~Homebrew~` and `[Subset - ...]` entries, and `setdefault` meant whichever of them
    normalised FIRST claimed the key and the real game could not have it.
  * SEVERAL CANDIDATES IS NOT AN ANSWER. Two RA rows normalising to one key were not a
    tie to break — nothing separates them — but first-wins broke it anyway.
  * ONE ROW PER TITLE, AND TITLES SPAN CONSOLES. `ra_games` is keyed on norm_key alone
    and the per-platform loop wrote `INSERT OR REPLACE`, so a game owned on two consoles
    silently kept the LAST console's id. It is still one row per title — that is what
    `ra_ach` and `ra_progress` are keyed by, and the server reads them that way — so the
    first console to match holds the key, deterministically, and a second one is reported
    rather than allowed to overwrite it.

For matched games, GetGameInfoAndUserProgress gives the full achievement set + which ones
the configured user has earned. All RA calls are throttled by ra._throttle().

Data lands in ra.sqlite (gitignored). Usage:
    python ra_fetch.py match [--platform snes]     # build ra_games
    python ra_fetch.py pull  [--limit 50] [--refresh]  # fetch achievements
    python ra_fetch.py all   [--limit 50]          # match then pull
"""

import datetime
import os
import re
import sqlite3
import sys

import config
import matchgate
import ra
from titlenorm import norm

# Use the local repo copies (config.library_db points at the Deck producer path).
DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
RA_DB = os.path.join(DATA, "ra.sqlite")
LIB_DB = os.path.join(DATA, "game-library.sqlite")


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


# RA files things that are not the game under names that normalise like the game.
# `~Hack~ Sonic 2 Delta` and `Sonic the Hedgehog 2 [Subset - Bonus]` are a romhack and an
# achievement set, and neither is the cartridge anybody owns.
_RA_NOT_A_GAME = re.compile(r"^\s*~[^~]+~|\[Subset\b", re.I)


def ra_candidates(games):
    """RA's console game list -> {norm_key: [(id, title), ...]}, non-games removed.

    Every candidate for a key is KEPT rather than the first one winning: two rows under
    one key is the absence of an answer, and only a list can say so."""
    out = {}
    for g in (games or []):
        title = (g.get("Title") or "").strip()
        if not title or _RA_NOT_A_GAME.search(title):
            continue
        nk = norm(title)
        if not nk:
            continue
        out.setdefault(nk, []).append((g.get("ID"), title))
    return out


def ra_match(index, norm_key, title):
    """The RA (id, title) for an owned game, or None. THROUGH THE GATE.

    A normalised key that happens to be equal is not evidence — it is the input to the
    judgement, not the judgement. `matchgate.score` measures the candidate against the
    title the user actually owns, the same rule ScreenScraper, SteamGridDB and the index
    merges are held to. No year is available from RA's list, and an absent year refuses
    nothing (see matchgate.score)."""
    cands = index.get(norm_key) or []
    ok = [c for c in cands if matchgate.score([title or norm_key], c[1])[0]]
    return ok[0] if len(ok) == 1 else None


def record_match(rdb, norm_key, ra_id, ra_title, console_id):
    """Write the match unless the key already belongs to ANOTHER console. -> written?

    `ra_games`, `ra_ach` and `ra_progress` are all keyed on norm_key alone, so a title
    owned on two consoles has exactly one slot and the two RA games cannot both have it.
    The old `INSERT OR REPLACE` handed it to whichever console the loop reached last —
    a silent, order-dependent answer. The first match holds it; the platform list is
    sorted, so "first" is the same on every run."""
    row = rdb.execute("SELECT ra_id, console_id FROM ra_games WHERE norm_key=?",
                      (norm_key,)).fetchone()
    if row is not None and row[1] != console_id:
        return False
    rdb.execute("INSERT OR REPLACE INTO ra_games VALUES(?,?,?,?,?)",
                (norm_key, ra_id, ra_title, console_id, _now()))
    return True


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
        ra_by_norm = ra_candidates(games)
        owned = lib.execute(
            "SELECT DISTINCT g.norm_key, g.canonical_title FROM games g "
            "JOIN sources s ON s.game_id=g.id "
            "WHERE s.source='emulation' AND s.platform=? "
            "ORDER BY g.norm_key", (plat,)).fetchall()
        n = held = 0
        for row in owned:
            hit = ra_match(ra_by_norm, row["norm_key"], row["canonical_title"])
            if not hit:
                continue
            if record_match(rdb, row["norm_key"], hit[0], hit[1], cid):
                n += 1
            else:
                held += 1
        rdb.commit()
        total += n
        print("%-16s %5d RA games / %5d owned -> %4d matched%s"
              % (plat, len(ra_by_norm), len(owned), n,
                 "  (%d already held by another console)" % held if held else ""))
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
