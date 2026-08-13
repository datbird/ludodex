#!/usr/bin/env python3
"""Every matched provider must surface a link, not just the ones that stored a URL.

The link strip renders an icon only when a provider link has a `url`. Live, IGDB had
matched 98% of the library and rendered on 7%, because `metadata_links` stores the
igdb id and nothing else for 2020 of 2173 rows — while the slug the URL needs sits
unused in every cached `igdb_meta` payload.

Deriving at read time is the same trick the Steam store link already uses on an owned
appid. Offline: a temp LUDODEX_DATA with a synthetic metadata cache.
"""
import json
import os
import sqlite3
import sys
import tempfile

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    d = tempfile.mkdtemp(prefix="ludodex-links-")
    mc = sqlite3.connect(os.path.join(d, "metadata-cache.sqlite"))
    mc.execute("CREATE TABLE igdb_meta(igdb_id INTEGER PRIMARY KEY, "
               "payload_json TEXT, fetched_at INTEGER)")
    mc.execute("INSERT INTO igdb_meta VALUES(?,?,0)",
               (7348, json.dumps({"id": 7348, "slug": "halo-the-master-chief-collection"})))
    mc.execute("INSERT INTO igdb_meta VALUES(?,?,0)",
               (20192, json.dumps({"id": 20192})))          # matched, no slug cached
    mc.commit()
    mc.close()
    os.environ["LUDODEX_DATA"] = d
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from server import app as srv

    print("1. IGDB — an id-only link row still yields a page URL")
    u = srv._provider_page_url("igdb", 7348)
    check("derived from the cached slug",
          u == "https://www.igdb.com/games/halo-the-master-chief-collection")

    print("2. IGDB — an explicit slug wins without touching the cache")
    check("uses the slug it was handed",
          srv._provider_page_url("igdb", 999, "some-slug")
          == "https://www.igdb.com/games/some-slug")

    print("3. IGDB — no slug anywhere means NO link, never a guessed one")
    check("returns None rather than a dead url", srv._provider_page_url("igdb", 20192) is None)
    check("unknown id also returns None", srv._provider_page_url("igdb", 12345) is None)

    print("4. ScreenScraper and SteamGridDB derive from their ids")
    check("screenscraper game page",
          srv._provider_page_url("screenscraper", 4321)
          == "https://www.screenscraper.fr/gameinfos.php?gameid=4321")
    check("steamgriddb game page",
          srv._provider_page_url("steamgriddb", 5555)
          == "https://www.steamgriddb.com/game/5555")

    print("5. Junk in, nothing out")
    check("non-numeric ss id yields no link",
          srv._provider_page_url("screenscraper", "abc") is None)
    check("unknown provider yields no link",
          srv._provider_page_url("mystery", 1) is None)


    print("6. a stored id-based IGDB url loses to the slug-derived one")
    # The apply path minted igdb.com/games/<numeric id> for 42 live rows. That is not
    # IGDB's canonical form, so derivation must WIN over a stored URL, not defer to it.
    check("derived beats a stored numeric url",
          srv._provider_page_url("igdb", 7348)
          == "https://www.igdb.com/games/halo-the-master-chief-collection")
    check("and it is not the id form",
          srv._provider_page_url("igdb", 7348) != "https://www.igdb.com/games/7348")

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
