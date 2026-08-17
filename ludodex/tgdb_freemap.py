#!/usr/bin/env python3
"""The free SHA1 -> TheGamesDB-id map, so ids cost nothing instead of costing the month.

A TheGamesDB key is 1,000 requests PER MONTH and name search cannot be batched — one
request per title. Resolving a library the obvious way is therefore measured in YEARS,
which is the same as saying it cannot be done.

It does not have to be done. `sselph/scraper` — the classic RetroPie scraper — ships a
prebuilt `hash.csv` under the MIT licence. It has 58,843 lines, but only 32,045 of them
carry a TheGamesDB id: 26,383 rows have a hash and a name and an EMPTY id column, and 415
are keyed by a MAME/ScummVM/Daphne set name rather than a hash. Counting lines instead of
usable rows overstates this file by 45%, which is exactly the mistake made when it was
first measured here. The real figures are 32,045 hashes -> 10,688 distinct game ids across
31 platforms.

Verified against the live API rather than taken on trust: the file's ids 160 and 238 come
back as "GoldenEye 007" and "007: The World Is Not Enough", both on platform 3 = Nintendo
64, exactly as claimed.

Joined against this deployment's ScreenScraper catalog on 2026-08-16: 23,650 of 724,487
SHA1s hit, resolving 10,701 distinct games. Ten thousand ids for zero requests.

DO NOT MISTAKE THIS MAP FOR THE DATABASE. It covers 10,688 games; TheGamesDB itself holds
roughly 123,000. Measured 2026-08-17 by batched id sampling (20 ids per request, 20
requests): the id space is ~90% populated up to a ceiling between 135,000 and 138,000,
and stone dead from 138,000 on. So this file is about 9% of the catalog — unsurprising,
since it is a ROM-hash map for 31 emulated console platforms and can never cover the PC
and modern-console bulk. For scale: IGDB's mirror here holds 371,978 games, roughly three
times TheGamesDB.

The corollary that matters for planning: 99.1% of the ids this map contains are ALREADY
used by this library, so there is almost nothing left in it to gain. Everything beyond it
costs API requests — ByPlatformID at 20 games per request, which is ~6,150 requests for
the whole catalog, or far fewer if scoped to the platforms actually owned.

THE ONE RULE HERE: A HASH IS EVIDENCE, A NAME IS NOT. This file also carries ROM names,
and they are tempting — matching on them would multiply the hit rate. They are used only
to LABEL an identity the hash created, never to find one. A hash collision is a
cryptographic event; a name collision is Tuesday, and name-matching out of a file with no
platform gate is the fail-open shape this codebase keeps getting bitten by.

LICENCE: sselph's repository is MIT, but the DATA is derived from TheGamesDB, whose own
redistribution terms are unresolved. So the file is DOWNLOADED ON THE USER'S MACHINE at
build time and never vendored into this repo or into a published supplement — fetching
something onto your own computer is a different act from redistributing it.
"""
import csv
import io
import os
import sys
import time
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config                       # noqa: E402

DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
CACHE = os.path.join(DATA, "tgdb-freemap.csv")
DEFAULT_URL = "https://raw.githubusercontent.com/sselph/scraper/master/hash.csv"

# The file changes about never (sselph's last release predates most of this catalog), so
# a long cache is right — and it means a rebuild does not depend on GitHub being up.
CACHE_TTL = 30 * 24 * 3600

SOURCE = {
    "name": "sselph/scraper hash.csv",
    "url": "https://github.com/sselph/scraper",
    "license": "MIT (code); the data it carries is derived from TheGamesDB",
    "license_url": "https://github.com/sselph/scraper/blob/master/LICENSE",
    "provides": "SHA1 -> TheGamesDB game id, for ROM hashes",
}


def enabled():
    return config.get_bool("matchindex_tgdb_freemap", True)


def url():
    return (config.get("matchindex_tgdb_freemap_url") or "").strip() or DEFAULT_URL


def fetch(force=False, timeout=120):
    """Ensure the cached copy exists and is fresh enough. Returns its path, or None.

    NEVER RAISES on a network failure. This is an optional enrichment of an optional
    index; a rebuild that dies because GitHub was slow would be a worse outcome than a
    rebuild without it, and the previous cached copy is still perfectly good."""
    if os.path.exists(CACHE) and not force:
        if (time.time() - os.path.getmtime(CACHE)) < CACHE_TTL:
            return CACHE
    tmp = CACHE + ".part"
    try:
        req = urllib.request.Request(url(), headers={"User-Agent": "ludodex"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
        if len(body) < 100_000:                    # a truncated or error body
            raise ValueError("suspiciously small (%d bytes)" % len(body))
        with open(tmp, "wb") as fh:
            fh.write(body)
        os.replace(tmp, CACHE)
        return CACHE
    except Exception as e:                          # noqa: BLE001
        try:
            os.remove(tmp)
        except OSError:
            pass
        print("tgdb_freemap: fetch failed (%s)" % str(e)[:120], file=sys.stderr)
        # A stale copy beats no copy: the mapping is historical data, not a live feed.
        return CACHE if os.path.exists(CACHE) else None


def rows(path=None):
    """Yield (sha1, tgdb_id, tgdb_platform_id, name). Malformed lines are skipped.

    A row with an EMPTY id column is skipped too, and that is most of what gets skipped:
    26,383 of the file's 58,843 lines carry a hash and a name but no TheGamesDB id. They
    are not malformed — they are simply not what this map is for.

    Skipped rather than raised on: this is a third-party file we do not control, and one
    bad line must not cost the other thirty-two thousand."""
    p = path or CACHE
    if not p or not os.path.exists(p):
        return
    with io.open(p, encoding="utf-8", errors="replace", newline="") as fh:
        for rec in csv.reader(fh):
            if len(rec) < 4:
                continue
            sha1 = (rec[0] or "").strip().lower()
            gid, plat, name = rec[1].strip(), rec[2].strip(), rec[3].strip()
            if len(sha1) != 40 or not gid.isdigit():
                continue
            yield sha1, int(gid), (int(plat) if plat.isdigit() else None), name


def stats(path=None):
    n = 0
    ids, plats = set(), set()
    for _sha, gid, plat, _nm in rows(path):
        n += 1
        ids.add(gid)
        if plat:
            plats.add(plat)
    return {"rows": n, "games": len(ids), "platforms": len(plats),
            "cached": os.path.exists(CACHE),
            "cached_at": int(os.path.getmtime(CACHE)) if os.path.exists(CACHE) else 0}


def main(argv):
    if "--fetch" in argv:
        p = fetch(force="--force" in argv)
        print("tgdb_freemap: %s" % (p or "unavailable"))
    s = stats()
    print("rows      : %s" % "{:,}".format(s["rows"]))
    print("games     : %s" % "{:,}".format(s["games"]))
    print("platforms : %s" % s["platforms"])
    print("cached    : %s" % (time.strftime("%Y-%m-%d", time.localtime(s["cached_at"]))
                              if s["cached_at"] else "no"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
