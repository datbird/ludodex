#!/usr/bin/env python3
"""Store pulls must not lose games, and must not overstate what they know.

Five separate ways a pull quietly returned less, or more, than the truth:

  * ONE FAILED BATCH BLANKED A HUNDRED WISHLIST TITLES FOREVER. steam_wishlist wrote
    `cache[str(a)] = ""` for every appid in a chunk even when the call had RAISED, and
    the next run only looks up appids not already in the cache. So a single 429 turned
    100 wishlist entries into permanently unresolved names — no retry, ever.
  * PSN DEDUPED ON THE LOWERCASED TITLE, so two distinct titleIds sharing a name kept
    only the first. A regional re-release, or two unrelated games called "Hitman",
    silently became one owned game.
  * PSN'S PLATFORM MAP CLAIMED PSVITA AND PSP while the request asked for ps3/ps4/ps5
    only, so either those purchases were missing from every pull or the map was
    decoration. Deriving one from the other means they cannot disagree again.
  * XBOX RECORDED PLAY HISTORY AS OWNERSHIP, and truncated it: no maxItems, pagingInfo
    ignored. A big account's history stopped wherever the service felt like stopping,
    and a truncated library looks exactly like a smaller one.
  * NINTENDO DROPPED A CARD WITH AN UNRECOGNISED PLATFORM with no tally at all, so the
    codename after NX/OUNCE would have shrunk the library with nothing said — the
    failure that module's own header claims the design avoids.

Plus one credential-scope fix: the Nintendo GraphQL POST replayed the entire
accounts.nintendo.com cookie jar to whatever host the portal page named, although the
idToken in the variables is the actual credential and the docstring says the only header
that endpoint needs is x-nintendo-savanna-client-id.

Offline. Every network call is replaced by a local stub.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-store-correct-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import config                                                  # noqa: E402
config.set_("steam_api_key", "test-not-a-real-key")
config.set_("steam_id", "76561190000000000")

import nintendo_owned                                          # noqa: E402
import psn_owned                                               # noqa: E402
import steam_wishlist                                          # noqa: E402
import xbox_owned                                              # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    print("store pulls do not lose games or overstate what they know")

    # ---- steam_wishlist: a failed batch is retried, not cached as blank --------- #
    calls = []

    def flaky(chunk):
        calls.append(list(chunk))
        raise RuntimeError("HTTP 429 Too Many Requests")

    cache = {}
    steam_wishlist.resolve_names([1, 2, 3], cache, fetch=flaky)
    check("a failed batch caches nothing", cache == {})
    check("so the next run still sees them as missing",
          [a for a in (1, 2, 3) if str(a) not in cache] == [1, 2, 3])

    def works(chunk):
        calls.append(list(chunk))
        return {1: "Hades", 2: "Celeste"}          # 3 genuinely has no store item

    steam_wishlist.resolve_names([1, 2, 3], cache, fetch=works)
    check("the retry resolves the names it could not before",
          cache.get("1") == "Hades" and cache.get("2") == "Celeste")
    check("an appid the API ANSWERED about but did not list is remembered as blank",
          cache.get("3") == "")
    check("the failed batch was actually retried", len(calls) == 2)

    calls[:] = []
    steam_wishlist.resolve_names([1, 2, 3], cache, fetch=works)
    check("and a resolved cache makes no call at all", calls == [])

    # ---- psn: distinct ids with the same name are distinct games ---------------- #
    rows = psn_owned._dedupe([
        ("CUSA00001", "Hitman", "ps4"),
        ("CUSA09999", "Hitman", "ps4"),        # a different game that shares a name
        ("CUSA00001", "Hitman", "ps4"),        # a genuine repeat of the SAME purchase
        ("CUSA00001", "Hitman", "ps5"),        # cross-gen: a second console, kept
        ("", "", "ps4"),                       # nameless rows are not games
    ])
    check("two different titleIds with one name are two games", len(rows) == 3)
    check("an exact repeat of the same purchase collapses",
          len([r for r in rows if r[0] == "CUSA00001" and r[2] == "ps4"]) == 1)
    check("and both consoles of a cross-gen purchase survive",
          {r[2] for r in rows if r[0] == "CUSA00001"} == {"ps4", "ps5"})

    check("the platforms PSN is asked for are exactly the ones it can map",
          set(psn_owned._REQ_PLATFORMS) == {k.lower() for k in psn_owned._PLAT})
    check("psvita and psp are actually requested now",
          {"psvita", "psp"} <= set(psn_owned._REQ_PLATFORMS))

    # ---- xbox: the whole history, and an honest label on every row -------------- #
    pages = []

    def fake_get(url, headers):
        pages.append(url)
        if "continuationToken" not in url:
            return {"titles": [{"type": "Game", "name": "Halo", "titleId": "1",
                                "devices": ["XboxSeries"]}],
                    "pagingInfo": {"continuationToken": "next-page"}}
        return {"titles": [{"type": "Game", "name": "Forza", "titleId": "2",
                            "devices": ["XboxOne"]},
                           {"type": "App", "name": "Netflix", "titleId": "3"}],
                "pagingInfo": {}}

    titles = xbox_owned._titles("https://titlehub.invalid/t", {}, get=fake_get)
    check("the request bounds the page size", "maxItems=" in pages[0])
    check("a continuation token is followed", len(pages) == 2)
    check("so every page's titles are collected", len(titles) == 3)
    rows = xbox_owned._rows(titles)
    check("apps are still dropped", [r[1] for r in rows] == ["Forza", "Halo"])
    check("the evidence label says what titlehub actually proves",
          xbox_owned.EVIDENCE == "play-history")
    line = config.tsv_row("1", "Halo", "xbox series", xbox_owned.EVIDENCE)
    check("and every row carries it as a fourth column",
          line.split("\t")[3] == "play-history")

    # ---- nintendo: the cookie jar stays on the host it came from ---------------- #
    sent = {}

    class FakeResp:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            import json as _j
            return _j.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        sent[req.full_url] = dict(req.header_items())
        return FakeResp({"data": {}})

    real = nintendo_owned.urllib.request.urlopen
    nintendo_owned.urllib.request.urlopen = fake_urlopen
    try:
        nintendo_owned._post_json("https://graph.other-host.invalid/api", {},
                                  {"x-nintendo-savanna-client-id": "cid"},
                                  "session=secret")
        hdrs = {k.lower(): v for k, v in
                sent["https://graph.other-host.invalid/api"].items()}
        check("no cookie is sent to a host the cookie did not come from",
              "cookie" not in hdrs)
        check("the header that endpoint documents IS sent",
              hdrs.get("x-nintendo-savanna-client-id") == "cid")

        nintendo_owned._post_json("https://accounts.nintendo.com/graphql", {}, {},
                                  "session=secret")
        hdrs = {k.lower(): v for k, v in
                sent["https://accounts.nintendo.com/graphql"].items()}
        check("but the portal's own host still gets its session",
              hdrs.get("cookie") == "session=secret")
    finally:
        nintendo_owned.urllib.request.urlopen = real

    check("the same-host test compares hosts, not prefixes",
          nintendo_owned._same_host("https://a.example/x", "https://A.EXAMPLE/y")
          and not nintendo_owned._same_host("https://a.example.evil/x",
                                            "https://a.example/y"))

    # ---- nintendo: an unrecognised platform is reported, never dropped ---------- #
    view = {"applicationId": "0100ABC", "applicationName": "Future Game",
            "apparentPlatform": "GRAPEFRUIT", "hasApplication": True}
    check("platform_of still declines to guess", nintendo_owned.platform_of(view) is None)

    seen_pages = []

    def fake_page(params, cookie, offset):
        seen_pages.append(offset)
        return ([view, {"applicationId": "0100DEF", "applicationName": "Mario",
                        "apparentPlatform": "NX", "hasApplication": True}], 1)

    real_page, real_boot = nintendo_owned._page, nintendo_owned.bootstrap
    nintendo_owned._page = fake_page
    nintendo_owned.bootstrap = lambda cookie=None: ({}, "cookie")
    try:
        rows = nintendo_owned.fetch_owned()
    finally:
        nintendo_owned._page, nintendo_owned.bootstrap = real_page, real_boot
    check("the card with an unknown platform is KEPT", len(rows) == 2)
    check("with a blank platform, so the loader falls back to the source label",
          [r[2] for r in rows if r[0] == "0100ABC"] == [""])
    check("and the known one is unaffected",
          [r[2] for r in rows if r[0] == "0100DEF"] == ["switch"])

    # ---- and no owner's name is baked into a public repo ------------------------ #
    s = open(os.path.join(DIR, "ludodex", "nintendo_owned.py"), encoding="utf-8").read()
    check("nintendo_owned names no account holder", "datbird" not in s)
    check("and describes the card impersonally", "games he owns" not in s)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
