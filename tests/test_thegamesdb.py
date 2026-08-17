#!/usr/bin/env python3
"""TheGamesDB's whole risk is the budget, so that is what most of this file is about.

A free key is 1,000 requests PER MONTH. Every reflex learned from ScreenScraper — which
grants roughly twenty thousand a DAY — overspends here by two orders of magnitude, and
the failure is quiet: the month simply ends early and the next four weeks of scraping
return nothing with no obvious reason why. So:

  * THE CEILING AND THE TRUTH ARE DIFFERENT CLAIMS. The configured limit is what WE are
    willing to spend; the allowance the server reports is what the key actually has.
    Taking the smaller is the only safe reading, and the two must not be conflated —
    conflating them fails in BOTH directions, overspending a small key and under-using a
    paid one.
  * A PAID TIER LEFT UNUSED IS A BUG WORTH REPORTING. If the server grants more than the
    configured limit the user bought something they are not getting, and silence there
    costs them money.
  * ONE CALL PER GAME IS THE BUG THIS PROVIDER IS MOST LIKELY TO GROW. There is no
    single-id fetch on purpose; these tests assert the batching holds and that art rides
    on the same request rather than costing a second one.
  * A MISS IS A RESULT. TheGamesDB not having a game is a fact to cache, never an
    exception to retry — retrying a miss against a monthly budget is how the budget dies.

None of this touches the network: `_request` is replaced, so every number below is a
property of our own arithmetic rather than of a third party's uptime.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_support
    test_support.isolate("ludodex-tgdb-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import config
    import media
    import thegamesdb as T

    calls = []
    # Grab the genuine transport BEFORE anything replaces it. Capturing it later would
    # capture the stub, and the classifier tests would then be asserting that the fake
    # raises — which it never does, so they would pass by not running.
    REAL_REQUEST = T._request

    def fake(path, params=None, timeout=30, attempts=3, key=None):
        """Stand-in transport. Records what would have gone out, and answers with the
        shape the live API actually returns (verified against it on 2026-08-16)."""
        calls.append((path, dict(params or {})))
        ids = [x for x in (params or {}).get("id", "").split(",") if x]
        gid = ids or ["1"]
        payload = {
            "code": 200, "status": "Success",
            "remaining_monthly_allowance": fake.remaining,
            "extra_allowance": fake.extra,
            "allowance_refresh_timer": 0,
            "data": {"count": len(gid),
                     "games": [{"id": int(g), "game_title": "Game %s" % g,
                                "platform": 20} for g in gid]},
            "include": {"boxart": {
                "base_url": {"original": "https://cdn.thegamesdb.net/images/original/",
                             "thumb": "https://cdn.thegamesdb.net/images/thumb/"},
                "data": {g: [{"id": 1, "type": "boxart", "side": "front",
                              "filename": "boxart/front/%s-1.jpg" % g,
                              "resolution": "1529x2156"}] for g in gid}}},
        }
        T._record_allowance(payload)
        return payload
    fake.remaining, fake.extra = 900, 0
    T._request = fake

    print("1. the default budget is a FREE key — 1,000 a month, not a day")
    check("configured_limit defaults to 1000", T.configured_limit() == 1000)
    check("and the module agrees with the config schema",
          config.DEFAULTS["thegamesdb_monthly_limit"] == str(T.FREE_MONTHLY_LIMIT))
    check("reserve is 5%% of it, min 10: got %d" % T.reserve(), T.reserve() == 50)
    check("the provider ships OFF — a metered key should be a choice",
          config.DEFAULTS["metadata_thegamesdb_enabled"] == "0"
          and not config.metadata_enabled("thegamesdb"))

    print()
    print("2. THE CEILING AND THE TRUTH — the smaller one decides")
    fake.remaining = 400                        # key smaller than our ceiling
    s = T.limit_status(force=True)
    check("server 400 under a 1000 ceiling -> budget 350", s["budget"] == 400 - 50)
    check("and that is not flagged as underconfigured", not s["underconfigured"])

    config.set_("thegamesdb_monthly_limit", "5000")
    fake.remaining = 900                        # ceiling raised past the real key
    s = T.limit_status(force=True)
    check("ceiling 5000 but key says 900 -> the KEY wins",
          s["budget"] == 900 - T.reserve())
    check("raising the setting alone never invents allowance",
          s["budget"] < 5000)

    fake.remaining = 9000                       # key bigger than our ceiling
    s = T.limit_status(force=True)
    check("ceiling 5000 under a 9000 key -> we spend only the ceiling",
          s["budget"] == 5000 - T.reserve())
    check("AND WE SAY SO — a bought tier sitting unused is worth reporting",
          s["underconfigured"] is True)
    config.set_("thegamesdb_monthly_limit", "1000")

    print()
    print("3. the reserve scales with the tier instead of assuming a small one")
    check("free tier holds 50 back", T.reserve() == 50)
    config.set_("thegamesdb_monthly_limit", "40000")
    check("a paid tier holds proportionally more (%d)" % T.reserve(),
          T.reserve() == 2000)
    config.set_("thegamesdb_reserve", "7")
    check("an explicit reserve is honoured verbatim", T.reserve() == 7)
    config.set_("thegamesdb_reserve", "")
    config.set_("thegamesdb_monthly_limit", "1000")

    print()
    print("4. BATCHING — 45 games must not cost 45 requests")
    calls.clear()
    fake.remaining = 900
    games, art = T.by_ids(range(1, 46))
    check("3 requests for 45 ids, not 45: got %d" % len(calls), len(calls) == 3)
    check("no chunk exceeds the server's page size of %d" % T.CHUNK,
          all(len(c[1]["id"].split(",")) <= T.CHUNK for c in calls))
    check("every id was asked for exactly once",
          sorted(int(x) for c in calls for x in c[1]["id"].split(",")) == list(range(1, 46)))
    check("all 45 games came back", len(games) == 45)
    check("there is no single-id fetch to regress into",
          not hasattr(T, "by_id"))

    print()
    print("5. ART RIDES ALONG — never a second request for what one could carry")
    check("by_ids asks for boxart on the same call",
          all("boxart" in c[1].get("include", "") for c in calls))
    check("and it came back parsed", len(art) == 45)
    calls.clear()
    T.search("sonic")
    check("search does too", "boxart" in calls[0][1].get("include", ""))
    check("search uses v1.1, which handles `mode` correctly",
          calls[0][0].startswith("/v1.1/"))

    print()
    print("6. every response teaches us the allowance, without a separate call")
    fake.remaining = 123
    calls.clear()
    T.search("anything")
    rem, extra, checked = T.cached_allowance()
    check("cached from the response body: %s" % rem, rem == 123)
    check("no extra /API/Limit request was needed",
          not any(c[0].endswith("/API/Limit") for c in calls))

    print()
    print("7. a MISS is a result, not an error")
    def empty(path, params=None, timeout=30, attempts=3, key=None):
        return {"code": 200, "data": {"count": 0, "games": []},
                "remaining_monthly_allowance": 500}
    T._request = empty
    games, art = T.search("no such game anywhere")
    check("empty list, no exception", games == [] and art == {})
    T._request = fake

    print()
    print("8. failures are classified, and the unretryable ones are not retried")
    import urllib.error
    import io as _io

    real_urlopen = T.urllib.request.urlopen
    for code, kind in ((401, "badkey"), (403, "quota"), (429, "quota"), (500, "error")):
        attempts = {"n": 0}

        def counting(req, timeout=0, _c=code, _a=attempts):
            _a["n"] += 1
            raise urllib.error.HTTPError(
                "u", _c, "e", {}, _io.BytesIO(b'{"status":"nope"}'))
        T.urllib.request.urlopen = counting
        try:
            REAL_REQUEST("/v1/Platforms", key="k", attempts=3)
            check("HTTP %d raised" % code, False)
        except T.TGDBError as e:
            check("HTTP %d -> %s" % (code, kind), e.kind == kind)
        check("HTTP %d tried once, not three times" % code, attempts["n"] == 1)
    T.urllib.request.urlopen = real_urlopen

    print()
    print("9. no key is refused loudly — never mistaken for 'not found'")
    saved = config.get("thegamesdb_api_key")
    config.set_("thegamesdb_api_key", "")
    os.environ.pop("TGDB_API_KEY", None)
    check("api_key() is empty", T.api_key() == "")
    try:
        REAL_REQUEST("/v1/Platforms")
        check("it raised", False)
    except T.TGDBError as e:
        check("badkey, not a silent empty result", e.kind == "badkey")
    if saved:
        config.set_("thegamesdb_api_key", saved)

    print()
    print("10. image types map to OUR kinds, and nothing is ever dropped")
    base = {"original": "https://x/", "thumb": "https://t/"}
    cases = [({"type": "boxart", "side": "front"}, "cover"),
             ({"type": "boxart", "side": "back"}, "box_back"),
             ({"type": "fanart"}, "background"),
             ({"type": "banner"}, "header"),
             ({"type": "screenshot"}, "screenshot"),
             ({"type": "clearlogo"}, "logo"),
             ({"type": "titlescreen"}, "title_screen"),
             ({"type": "something-new-they-added"}, "other")]
    for row, want in cases:
        got = T._asset(dict(row, filename="f.jpg"), base)
        check("%-28s -> %s" % (row["type"] + ("/" + row["side"] if row.get("side") else ""),
                               want), got["kind"] == want)
    check("every mapped kind is a real media kind",
          all(k in media.KINDS for k in T.MEDIA_KIND.values()))

    print()
    print("11. an unmeasured image is UNKNOWN, not small")
    a = T._asset({"type": "boxart", "side": "front", "filename": "f.jpg",
                  "resolution": "1529x2156"}, base)
    check("resolution parsed", (a["width"], a["height"]) == (1529, 2156))
    b = T._asset({"type": "boxart", "side": "front", "filename": "f.jpg"}, base)
    check("missing resolution -> None, so ranking treats it as unknown",
          b["width"] is None and b["height"] is None)
    check("url is built from the base, not guessed",
          a["url"] == "https://x/f.jpg" and a["thumb"] == "https://t/f.jpg")

    print()
    print("12. it is RANKED honestly — below IGDB and ScreenScraper everywhere")
    listed = [k for k, v in media.PRIORITY.items() if "thegamesdb" in v]
    check("it appears for the kinds it actually supplies: %s" % sorted(listed),
          set(listed) == {"cover", "box_back", "background", "header", "logo",
                          "screenshot", "title_screen"})
    for kind in listed:
        pri = media.PRIORITY[kind]
        i = pri.index("thegamesdb")
        for better in ("igdb", "screenscraper"):
            if better in pri:
                check("%-13s %s outranks thegamesdb" % (kind, better),
                      pri.index(better) < i)
    check("and it is a registered remote media provider",
          "thegamesdb" in media.REMOTE_PROVIDERS)
    check("and a known metadata provider",
          "thegamesdb" in config.METADATA_PROVIDERS)

    print()
    print("13. it is discoverable — setup guide + identity cache")
    import provider_ids
    check("provider_ids accepts it", "thegamesdb" in provider_ids.PROVIDERS)
    entry = [i for i in config.INTEGRATIONS if i["id"] == "thegamesdb"]
    check("the setup guide has an entry", len(entry) == 1)
    check("naming the config keys the UI must render",
          set(entry[0]["config_keys"]) >= {"thegamesdb_api_key",
                                           "thegamesdb_monthly_limit"})

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
