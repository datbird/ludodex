"""Populate OS support (Windows/Mac/Linux) for store-owned games.

Steam's ownership export only carries the appid, but Steam's public store API
(appdetails?filters=platforms) returns per-app OS support. We already have every
appid in `sources`, so this fills a durable os.sqlite that the server reads.

Steam's store API is unauthenticated but rate-limited (~200 req / 5 min / IP), so
calls are throttled. Console/emulation entries have no OS and are skipped.

GOG reports OS too (api.gog.com/products/<id>.content_system_compatibility, where
osx == mac), so `gog` fills the same store from GOG's numeric product ids. Epic is
assumed Windows-only in the server (no Linux client), so it needs no fetch.

    python os_fetch.py steam [--limit 100] [--refresh]
    python os_fetch.py gog   [--limit 100] [--refresh]
    python os_fetch.py all
"""

import datetime
import json
import os
import sqlite3
import sys
import time
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
OS_DB = os.path.join(DIR, "os.sqlite")
LIB_DB = os.path.join(DIR, "game-library.sqlite")
UA = "ludodex/1.0 (+https://github.com/datbird/ludodex)"
APPDETAILS = "https://store.steampowered.com/api/appdetails?appids=%s&filters=platforms"
GOG_PRODUCT = "https://api.gog.com/products/%s"
COOLDOWN = float(os.environ.get("STEAM_STORE_COOLDOWN_MS", "1500")) / 1000.0
GOG_COOLDOWN = float(os.environ.get("GOG_COOLDOWN_MS", "1000")) / 1000.0


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _os():
    con = sqlite3.connect(OS_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS os_support(
        source TEXT, source_id TEXT, windows INTEGER, mac INTEGER, linux INTEGER,
        fetched_at TEXT, PRIMARY KEY(source, source_id))""")
    con.row_factory = sqlite3.Row
    return con


def _todo(lib, osdb, source, refresh):
    rows = lib.execute("SELECT DISTINCT source_id FROM sources "
                       "WHERE source=? AND source_id!=''", (source,)).fetchall()
    ids = [r["source_id"] for r in rows]
    if not refresh:
        have = {r["source_id"] for r in
                osdb.execute("SELECT source_id FROM os_support WHERE source=?", (source,))}
        ids = [i for i in ids if i not in have]
    return ids


def _store(osdb, source, source_id, win, mac, lin):
    osdb.execute("INSERT OR REPLACE INTO os_support VALUES(?,?,?,?,?,?)",
                 (source, str(source_id), int(bool(win)), int(bool(mac)),
                  int(bool(lin)), _now()))
    osdb.commit()


def _steam_os(appid):
    req = urllib.request.Request(APPDETAILS % appid, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    entry = d.get(str(appid)) or {}
    if not entry.get("success"):
        return None
    p = entry["data"].get("platforms", {})
    return (p.get("windows"), p.get("mac"), p.get("linux"))


def _gog_os(pid):
    req = urllib.request.Request(GOG_PRODUCT % pid, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    c = d.get("content_system_compatibility") or {}
    if not c:
        return None
    return (c.get("windows"), c.get("osx"), c.get("linux"))  # GOG's osx == our mac


def _fetch(source, resolver, cooldown, limit, refresh):
    lib, osdb = sqlite3.connect(LIB_DB), _os()
    lib.row_factory = sqlite3.Row
    ids = _todo(lib, osdb, source, refresh)
    if limit:
        ids = ids[:limit]
    print("fetching OS for %d %s entries…" % (len(ids), source))
    last = 0.0
    done = fail = 0
    for sid in ids:
        wait = last + cooldown - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        last = time.monotonic()
        try:
            osv = resolver(sid)
            if not osv:
                fail += 1
                continue
            _store(osdb, source, sid, *osv)
            done += 1
            if done % 25 == 0:
                print("  …%d/%d" % (done, len(ids)))
        except Exception as e:
            fail += 1
            print("  %s: %s" % (sid, e), file=sys.stderr)
    print("done: %d fetched, %d failed/no-data" % (done, fail))
    lib.close(); osdb.close()


def steam(limit=None, refresh=False):
    _fetch("steam", _steam_os, COOLDOWN, limit, refresh)


def gog(limit=None, refresh=False):
    _fetch("gog", _gog_os, GOG_COOLDOWN, limit, refresh)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "all"
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    refresh = "--refresh" in argv
    if cmd in ("steam", "all"):
        steam(limit, refresh)
    if cmd in ("gog", "all"):
        gog(limit, refresh)
    if cmd not in ("steam", "gog", "all"):
        print("unknown command %r (steam | gog | all)" % cmd, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
