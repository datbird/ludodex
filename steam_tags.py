#!/usr/bin/env python3
"""Pull Steam community tags for owned Steam games from SteamSpy — the 'steam'
tag provider. SteamSpy's API is keyless and public (no key, no login); its
appdetails response carries the crowd-sourced Steam store tags with vote weights.

Tags are cached (keyed by norm_key) in steam-tags.sqlite; build_library merges
them into game_tags with origin 'steam' (their own badge style). This is a
durable fetched cache — it survives catalog rebuilds like scores.sqlite, and is
re-fetched incrementally (games older than the TTL) on each run.

  python3 steam_tags.py                 # fetch tags for owned steam games (stale only)
  python3 steam_tags.py --refresh       # re-fetch everything
  python3 steam_tags.py --limit 50      # cap this run
  python3 steam_tags.py --top 20        # keep the top-N tags per game (default 12)

SteamSpy asks for <=1 request/second; the per-request cooldown is configurable
via Settings > Services > Limits (service 'steamspy'), default 1100 ms. No auth.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config

DB = os.path.join(DIR, "steam-tags.sqlite")
API = "https://steamspy.com/api.php?request=appdetails&appid=%s"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")
TOP_DEFAULT = 12
TTL_DAYS = 30                    # re-fetch a game's tags at most this often
DEFAULT_COOLDOWN_MS = 1100      # SteamSpy: <=1 req/sec

import sqlite3


def _con():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS steam_tags("
                "norm_key TEXT, tag TEXT, votes INTEGER, updated INTEGER, "
                "PRIMARY KEY(norm_key, tag))")
    con.execute("CREATE INDEX IF NOT EXISTS ix_st_nk ON steam_tags(norm_key)")
    con.execute("PRAGMA journal_mode=WAL")       # server reads while we write
    return con


def steam_games():
    """[(appid, norm_key)] for owned Steam games (numeric appids only)."""
    lib = config.get("library_db")
    if not (lib and os.path.exists(lib)):
        return []
    c = sqlite3.connect(lib)
    rows = []
    for nk, sid in c.execute(
            "SELECT g.norm_key, s.source_id FROM games g JOIN sources s "
            "ON s.game_id=g.id WHERE s.source='steam'"):
        if sid and str(sid).isdigit():
            rows.append((str(sid), nk))
    c.close()
    return rows


def _cooldown():
    try:
        ms = config.rate_limits("steamspy").get("cooldown_ms") or DEFAULT_COOLDOWN_MS
    except Exception:
        ms = DEFAULT_COOLDOWN_MS
    return max(0.0, ms / 1000.0)


def fetch_tags(appid):
    """Top Steam community tags for an appid: {tag: votes}. {} on failure."""
    req = urllib.request.Request(API % appid)
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    tags = data.get("tags")
    # SteamSpy returns tags as {name: votes}, or [] when it has none for the app
    if isinstance(tags, dict):
        return {str(k): int(v) for k, v in tags.items() if str(k)}
    return {}


def main(argv):
    if not config.metadata_enabled("steamspy"):
        print("steam_tags: steamspy provider disabled — nothing to do",
              file=sys.stderr)
        return
    refresh = "--refresh" in argv
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    top = int(argv[argv.index("--top") + 1]) if "--top" in argv else TOP_DEFAULT

    con = _con()
    fresh_cut = int(time.time()) - TTL_DAYS * 86400
    fresh = set()
    if not refresh:
        fresh = {nk for (nk,) in con.execute(
            "SELECT DISTINCT norm_key FROM steam_tags WHERE updated>=?",
            (fresh_cut,))}

    games = steam_games()
    todo_all = [(a, nk) for (a, nk) in games if nk not in fresh]
    todo = todo_all[:limit] if limit else todo_all
    print("steam_tags: %d owned steam games, %d already fresh, fetching %d%s"
          % (len(games), len(games) - len(todo_all), len(todo),
             " (limited)" if limit and len(todo_all) > len(todo) else ""),
          file=sys.stderr)

    cooldown = _cooldown()
    done = errs = 0
    for i, (appid, nk) in enumerate(todo):
        try:
            tags = fetch_tags(appid)
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            errs += 1
            print("steam_tags: appid %s failed: %s" % (appid, e), file=sys.stderr)
            time.sleep(cooldown)
            continue
        now = int(time.time())
        chosen = sorted(tags.items(), key=lambda x: -x[1])[:top]
        con.execute("DELETE FROM steam_tags WHERE norm_key=?", (nk,))
        con.executemany(
            "INSERT OR REPLACE INTO steam_tags(norm_key,tag,votes,updated) "
            "VALUES(?,?,?,?)", [(nk, t, v, now) for t, v in chosen])
        con.commit()
        done += 1
        if done % 50 == 0:
            print("steam_tags: %d/%d…" % (done, len(todo)), file=sys.stderr)
        if i < len(todo) - 1:
            time.sleep(cooldown)
    con.close()
    print("steam_tags: tagged %d games (%d errors)" % (done, errs), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
