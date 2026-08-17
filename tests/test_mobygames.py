#!/usr/bin/env python3
"""MobyGames rations by the HOUR, and that changes what this client has to get right.

TheGamesDB gives 12,000 requests a month, so its client is about BUDGET — a reserve, a
ceiling, a refusal to overspend. MobyGames gives 720 an hour with no monthly cap, so the
only cost of a request is TIME. There is nothing to husband. What matters instead is
pacing that a four-and-a-half-hour job can hold, and knowing which work is a quarter-hour
and which is a month.

  * THE WALK IS CHEAP AND THE SERIALS ARE NOT. /games pages 100 at a time, so all
    332,414 games are 3,325 requests. Product codes live at /games/{id}/platforms/{pid},
    ONE REQUEST PER PAIR — ~430,000 requests, about 25 days. A quarter-hour job and a
    month-long job must not share a switch, so product codes default OFF.
  * FORMAT=NORMAL IS FREE. id, brief and normal all page at 100 and cost one request.
    Asking for bare ids leaves the genres, platforms and art on the table for nothing.
  * THEIR GENRE LIST IS FLAT BUT CATEGORISED, and the categories are the useful part.
    'Perspective' is IGDB's player_perspectives; 'Setting' is its themes. Dumping all of
    them into `genres` buries 'Adventure' among '1st-person' and 'Sci-Fi / Futuristic'.
  * PACING IS EVEN, NOT BURSTY. Their 1/sec is a burst ceiling; 720/hour is the real
    limit. Firing 720 in twelve minutes then stalling for forty-eight buys nothing on a
    job measured in hours and looks far worse from their side.

No network: `_get` is replaced throughout.
"""
import os
import sys
import time

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


# A real `format=normal` record, trimmed — transcribed from their own documented sample.
REC = {
    "game_id": 1, "title": "The X-Files Game", "moby_score": 3.8, "num_votes": 53,
    "description": "As an extension of one of the most long-running...",
    "alternate_titles": [{"description": "Finnish title", "title": "Salaiset Kansiot"}],
    "genres": [
        {"genre_category": "Basic Genres", "genre_name": "Adventure"},
        {"genre_category": "Perspective", "genre_name": "1st-person"},
        {"genre_category": "Narrative Theme/Topic", "genre_name": "Detective / Mystery"},
        {"genre_category": "Setting", "genre_name": "Sci-Fi / Futuristic"},
        {"genre_category": "Other Attributes", "genre_name": "Licensed Title"}],
    "platforms": [{"first_release_date": "1998", "platform_id": 3,
                   "platform_name": "Windows"},
                  {"first_release_date": "1998-06", "platform_id": 74,
                   "platform_name": "Macintosh"},
                  {"first_release_date": "1999", "platform_id": 6,
                   "platform_name": "PlayStation"}],
    "sample_cover": {"height": 927, "width": 800,
                     "image": "http://x/covers/l/3.jpg",
                     "thumbnail_image": "http://x/covers/s/3.jpg"},
    "sample_screenshots": [{"caption": "Mulder", "height": 480, "width": 640,
                            "image": "http://x/shots/l/86087.jpg"},
                           {"caption": "Title screen", "height": 480, "width": 640,
                            "image": "http://x/shots/l/313897.jpg"}],
}

PLATREC = {"game_id": 31910, "platform_id": 5, "first_release_date": "1995",
           "releases": [{"countries": ["United States"], "release_date": "1995",
                         "product_codes": [{"product_code": "SLUS-00594",
                                            "product_code_type": "Company Code"},
                                           {"product_code": "  "}]},
                        {"countries": ["Japan"], "product_codes": []}]}


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_support
    test_support.isolate("ludodex-moby-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import config
    import media
    import provider_caps as PC
    import mobygames as MG

    calls = []
    REAL_GET = MG._get

    def fake(path, params=None, timeout=60, attempts=3):
        calls.append((path, dict(params or {})))
        if path == "/games":
            if params.get("format") == "id":
                base = int(params.get("offset") or 0)
                n = min(int(params.get("limit") or 100), fake.remaining - base)
                return {"games": list(range(base + 1, base + max(0, n) + 1))}
            return {"games": [REC]}
        if path.startswith("/games/") and "/platforms/" in path:
            return PLATREC
        if path == "/platforms":
            return {"platforms": [{"platform_id": 3, "platform_name": "Windows"}]}
        return {}
    fake.remaining = 250
    MG._get = fake

    print("1. the limit is PER HOUR, and pacing is derived from it")
    check("720 is the documented non-commercial default",
          MG.hourly_limit() == 720 and config.DEFAULTS["mobygames_hourly_limit"] == "720")
    check("which is 5.0 s between requests", abs(MG._interval() - 5.0) < 0.01)
    config.set_("mobygames_hourly_limit", "360")
    check("a legacy key paces at 10 s", abs(MG._interval() - 10.0) < 0.01)
    config.set_("mobygames_hourly_limit", "36000")
    check("and the burst FLOOR still holds at 1 s, whatever the hourly says",
          abs(MG._interval() - 1.0) < 0.01)
    config.set_("mobygames_hourly_limit", "720")

    print()
    print("2. what a full walk costs — in hours, because that is the real currency")
    s = MG.status()
    check("3,325 pages for 332,414 games", s["pages_for_full_walk"] == 3325)
    check("4.62 hours at 720/hour: %s" % s["hours_for_full_walk"],
          4.5 < s["hours_for_full_walk"] < 4.7)
    config.set_("mobygames_hourly_limit", "360")
    check("and twice that on a legacy key", MG.status()["hours_for_full_walk"] > 9)
    config.set_("mobygames_hourly_limit", "720")

    print()
    print("3. paging is 100, and the walk is RESUMABLE from an offset")
    calls.clear()
    MG._pace = lambda: None                          # do not actually sleep in a test
    seen, offsets = [], []
    for off, rows in MG.walk_ids(fmt="id"):
        offsets.append(off)
        seen.extend(rows)
    check("250 ids over 3 pages: %s" % offsets, offsets == [0, 100, 200])
    check("every id came back once", len(seen) == 250 and len(set(seen)) == 250)
    check("each request asked for exactly 100",
          all(c[1].get("limit") == 100 for c in calls))
    check("a short page ends the walk — no endless final request",
          len(calls) == 3)
    check("it yields the OFFSET, so a caller can checkpoint a 4-hour job",
          offsets[1] == 100)

    print()
    print("3b. THE SILENT CEILING — measured live, and refused rather than trusted")
    # Verified with a real key on 2026-08-17: offset 205,000 returns 100 rows, 210,000
    # returns an empty list with NO error and NO 429. A walk that trusts the empty page
    # stops ~124,000 games short of 332,414 and reports success.
    check("the measured ceiling is recorded, not rediscovered",
          MG.GLOBAL_OFFSET_CEILING == 205000)
    try:
        list(MG.walk_ids(fmt="id", start_offset=MG.GLOBAL_OFFSET_CEILING + 1))
        check("unfiltered paging past it RAISED", False)
    except MG.MobyError as e:
        check("unfiltered paging past it raises rather than returning []",
              "stop short" in str(e))
    calls.clear()
    got = [rows for _pid, _off, rows in MG.walk_all(fmt="id", platform_ids=[3])]
    check("but a PLATFORM window pages freely", sum(len(r) for r in got) == 250)
    check("and every request carried the platform filter",
          all(c[1].get("platform") == [3] for c in calls))
    check("walk_all yields the platform id, so (game, platform) survives the walk",
          [p for p, _o, _r in MG.walk_all(fmt="id", platform_ids=[3, 5])][:1] == [3])

    print()
    print("4. FORMAT=NORMAL IS FREE — asking for ids alone wastes the request")
    check("the default walk format is normal",
          config.DEFAULTS["mobygames_walk_format"] == "normal")
    check("all three formats are accepted", set(MG.FORMATS) == {"id", "brief", "normal"})
    try:
        MG.games(fmt="enormous")
        check("an unknown format raised", False)
    except ValueError:
        check("an unknown format raises rather than silently defaulting", True)

    print()
    print("5. THE GENRE CATEGORIES ARE THE POINT — a flat list, split three ways")
    m = MG.extract_metadata(REC)
    check("Basic Genres -> genres", m["genres"] == ["Adventure"])
    check("Perspective -> player_perspectives", m["player_perspectives"] == ["1st-person"])
    check("Setting + Narrative Theme -> themes",
          set(m["themes"]) == {"Detective / Mystery", "Sci-Fi / Futuristic"})
    check("'Other Attributes' is dropped, not guessed into a facet we do not own",
          "Licensed Title" not in str(m))
    check("Moby Score 3.8/10 rescales to 38/100 like every other community_score",
          m["community_score"] == 38)
    check("release_year is the EARLIEST across platforms, not the first listed",
          m["release_year"] == "1998")

    print()
    print("6. art arrives already MEASURED, so the picker can rank it immediately")
    mm = MG.extract_media(REC)
    cov = [x for x in mm if x["kind"] == "cover"][0]
    check("cover 800x927 — a real scan, not a thumbnail",
          (cov["width"], cov["height"]) == (800, 927))
    check("the large image is taken, never the thumbnail",
          "/l/" in cov["url"] and "/s/" not in cov["url"])
    shots = [x for x in mm if x["kind"] == "screenshot"]
    check("both screenshots, with dimensions", len(shots) == 2
          and shots[0]["width"] == 640)
    check("every kind claimed is a real media kind",
          all(x["kind"] in media.KINDS for x in mm)
          and all(v in media.KINDS for v in MG.COVER_KIND.values()))

    print()
    print("7. PRODUCT CODES ARE THE MONTH-LONG JOB, and are gated because of it")
    check("off by default", config.DEFAULTS["mobygames_product_codes"] == "0"
          and not config.get_bool("mobygames_product_codes", False))
    check("full cover sets too — same one-request-per-pair shape",
          config.DEFAULTS["mobygames_full_covers"] == "0")
    codes = MG.product_codes(31910, 5)
    check("the serial is extracted from the release record",
          codes[0]["code"] == "SLUS-00594")
    check("with its type", codes[0]["type"] == "Company Code")
    check("a blank code is skipped, not stored as an empty serial", len(codes) == 1)
    check("the cost is written down where someone will read it before enabling it",
          "25 DAYS" in MG.__doc__ or "25 days" in MG.game_platform.__doc__)

    print()
    print("8. failures are classified; only 429 is worth retrying")
    MG._get = REAL_GET
    import urllib.error
    import io as _io
    real_urlopen = MG.urllib.request.urlopen
    config.set_("mobygames_api_key", "k")
    for code, kind, tries in ((401, "badkey", 1), (500, "error", 1), (429, "quota", 3)):
        n = {"c": 0}

        def boom(req, timeout=0, _c=code, _n=n):
            _n["c"] += 1
            raise urllib.error.HTTPError("u", _c, "e", {},
                                         _io.BytesIO(b'{"error":"x","message":"y"}'))
        MG.urllib.request.urlopen = boom
        MG._pace = lambda: None
        try:
            REAL_GET("/games", {"limit": 1}, attempts=3)
            check("HTTP %d raised" % code, False)
        except MG.MobyError as e:
            check("HTTP %-3d -> %-7s" % (code, kind), e.kind == kind)
        check("HTTP %d tried %d time(s)" % (code, tries), n["c"] == tries)

    def notfound(req, timeout=0):
        raise urllib.error.HTTPError("u", 404, "e", {}, _io.BytesIO(b"{}"))
    MG.urllib.request.urlopen = notfound
    check("404 is a RESULT, not an error", REAL_GET("/games/0") is None)
    MG.urllib.request.urlopen = real_urlopen
    config.set_("mobygames_api_key", "")

    print()
    print("9. no key is refused loudly")
    os.environ.pop("MOBYGAMES_API_KEY", None)
    check("api_key() is empty", MG.api_key() == "")
    try:
        REAL_GET("/platforms")
        check("it raised", False)
    except MG.MobyError as e:
        check("badkey, not an empty result", e.kind == "badkey")

    print()
    print("10. it ships OFF, and every knob is declared")
    check("disabled by default — it needs a PAID key",
          config.DEFAULTS["metadata_mobygames_enabled"] == "0" and not MG.enabled())
    for k in ("mobygames_api_key", "mobygames_hourly_limit", "mobygames_min_interval_ms",
              "mobygames_walk_format", "mobygames_media", "mobygames_product_codes",
              "mobygames_full_covers"):
        check("setting %-28s exists" % k, k in config.DEFAULTS)
    check("it is a metadata provider", "mobygames" in config.METADATA_PROVIDERS)
    check("and a remote media provider", "mobygames" in media.REMOTE_PROVIDERS)
    check("with a label and a toggle",
          PC.LABEL.get("mobygames") and "mobygames" in PC.ENABLED_KEY)
    check("claiming at least 5 attribute kinds",
          len([k for k, v in PC.CAPS.items() if "mobygames" in v]) >= 5)
    entry = [i for i in config.INTEGRATIONS if i["id"] == "mobygames"]
    check("the setup guide covers it", len(entry) == 1)
    check("and warns that it is non-commercial only",
          "NON-COMMERCIAL" in " ".join(entry[0]["steps"]).upper())

    print()
    print("11. the OTHER recent integrations got settings too")
    for k, d in (("arcadedb_media", "1"), ("zxinfo_media", "1"),
                 ("libretro_dats_collections", "redump,no-intro"),
                 ("libretro_dats_cache_days", "30")):
        check("%-28s = %r" % (k, d), config.DEFAULTS.get(k) == d)
    import libretro_dats as L
    check("and the DAT module actually READS its collections setting",
          L.wanted_collections() == ("redump", "no-intro"))
    config.set_("libretro_dats_collections", "redump")
    check("...changing it changes behaviour", L.wanted_collections() == ("redump",))
    config.set_("libretro_dats_collections", "")
    check("blank falls back to the default, never to nothing",
          L.wanted_collections() == L.DEFAULT_COLLECTIONS)
    config.set_("libretro_dats_cache_days", "7")
    check("and the cache ttl is honoured", L.cache_ttl() == 7 * 86400)

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
