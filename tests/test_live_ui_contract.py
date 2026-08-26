#!/usr/bin/env python3
"""The contract between the UI and the API, driven against a RUNNING instance.

WHY THIS EXISTS. On 2026-08-26 this repo had 180 passing test files while the app was
visibly broken three separate times in one day. Every one of those bugs lived in the SEAM
the unit tests cannot see: the UI started sending a new key shape (a CARD key,
"igdb:2155") and three server paths still expected the old one.

  * the single-game magic wand scanned a key matching no game, and reported 0 findings
  * the hero preference wrote against that key
  * every media lookup asked for art belonging to a game called "igdb:2155", so detail
    pages rendered no hero and no background

None of them raised. All three returned an empty, well-formed, 200 answer.

A unit test cannot catch this, because a unit test builds its own fixture and can only
prove what its author already believed. So this test refuses to invent anything. It asks
the live API for a game the way the GRID does, takes the key the API itself hands back,
and feeds that key to every endpoint the detail panel calls. If the UI can reach a state
the API does not understand, this fails.

READ-ONLY, AND FREE. It performs no writes and touches no AI endpoint, deliberately: the
wand is checked by proving the key it WOULD send resolves to a real game, never by
running a scan. See docs/TESTING.md.

    LUDODEX_LIVE_TESTS=1 \\
    LUDODEX_URL=http://<host>:8001 \\
    LUDODEX_USER=<user> LUDODEX_PASS=<pass> \\
      python3 tests/test_live_ui_contract.py
"""
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

if os.environ.get("LUDODEX_LIVE_TESTS") != "1":
    sys.exit("SKIPPED: live test. It talks to a RUNNING ludodex over HTTP with real "
             "credentials. Read-only and free, but still not something a routine sweep "
             "should do. Re-run with LUDODEX_LIVE_TESTS=1, LUDODEX_URL, LUDODEX_USER "
             "and LUDODEX_PASS.")

BASE = os.environ.get("LUDODEX_URL", "http://localhost:8001").rstrip("/")
USER = os.environ.get("LUDODEX_USER")
PASS = os.environ.get("LUDODEX_PASS")
if not (USER and PASS):
    sys.exit("SKIPPED: LUDODEX_USER and LUDODEX_PASS are required.")

PASSED = []
_jar = http.cookiejar.CookieJar()
_open = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_jar)).open


def check(label, cond, detail=""):
    PASSED.append((label, bool(cond)))
    print("  %s   %s%s" % ("ok " if cond else "FAIL", label,
                           "" if cond else "   <- " + str(detail)[:160]))
    if not cond:
        sys.exit("FAILED: " + label)


def api(path, body=None, raw=False):
    """GET, or POST when `body` is given. Returns (status, parsed-or-bytes)."""
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with _open(req, timeout=60) as r:
            payload = r.read()
            if raw:
                return r.status, payload, r.headers.get("Content-Type", "")
            return r.status, json.loads(payload or b"null")
    except urllib.error.HTTPError as e:
        payload = e.read()
        if raw:
            return e.code, payload, e.headers.get("Content-Type", "")
        try:
            return e.code, json.loads(payload or b"null")
        except ValueError:
            return e.code, payload


def main():
    st, me = api("/api/auth/login", {"username": USER, "password": PASS})
    check("a user can sign in", st == 200 and me.get("ok"), me)

    # ---------------------------------------------------------------- the grid
    st, page = api("/api/games?limit=40")
    check("the library lists games", st == 200 and page.get("items"), st)
    items = page["items"]
    check("the count agrees with the page", page["total"] >= len(items),
          (page["total"], len(items)))

    # THE KEY THE UI ACTUALLY NAVIGATES BY. Taken from the API's own answer, never
    # constructed here — constructing it is how a test ends up proving only what its
    # author assumed.
    def nav_key(it):
        return it.get("card_key") or it.get("entry_key") or it["norm_key"]

    check("every row carries a key the UI can navigate by",
          all(nav_key(it) for it in items))

    # prefer a row that spans several copies: that is where the collapse can go wrong
    multi = [it for it in items if (it.get("platforms") or "").count(",") >= 1]
    subjects = (multi[:2] + items[:3])[:4]

    for it in subjects:
        key = nav_key(it)
        title = (it.get("title") or "")[:28]
        tag = "%s (%s)" % (title, key)

        # ------------------------------------------------------------ detail
        st, d = api("/api/games/" + urllib.request.quote(key, safe=""))
        check("detail opens by the key the grid gives: " + tag, st == 200, (st, d))
        check("detail knows its title: " + tag, bool(d.get("title")), d.get("title"))

        # THE WAND BUG. aimeta resolves by BARE norm_key and reports zero findings with
        # no error when it misses, so a wrong key here is invisible. Prove the key the
        # panel would send names a real game. Never run the scan: that spends money.
        base = d.get("norm_key")
        check("detail returns a real norm_key, not the key it was called with: " + tag,
              bool(base) and base != key and not base.startswith(("igdb:", "title:")),
              base)
        # Resolve it the way aimeta does: by the BARE norm_key. Not by title search,
        # which normalization defeats ("10,000,000" -> "10 000 000").
        st, byname = api("/api/games/" + urllib.request.quote(base, safe=""))
        check("and that norm_key opens a real game: " + tag,
              st == 200 and byname.get("title"), (st, base))

        # ------------------------------------------------------------ media list
        st, lib = api("/api/games/" + urllib.request.quote(key, safe="") + "/media")
        check("the media library answers by that key: " + tag, st == 200, st)
        assets = lib.get("assets") or []

        # A game the grid says HAS a cover must expose one here. This is the exact
        # assertion the media regression failed: 200, well-formed, and empty.
        if it.get("has_cover"):
            check("a game the grid shows art for HAS art: " + tag, len(assets) > 0,
                  "0 assets while the grid painted a cover")
            kinds = {a["kind"] for a in assets}
            check("and a cover is among them: " + tag, "cover" in kinds, sorted(kinds))

            # ------------------------------------------------------- media bytes
            for kind in ("cover",) + tuple(
                    k for k in ("hero", "background", "logo") if k in kinds):
                st, blob, ctype = api(
                    "/api/media/%s/%s" % (urllib.request.quote(key, safe=""), kind),
                    raw=True)
                check("%s streams real bytes: %s" % (kind, tag),
                      st == 200 and ctype.startswith("image/") and len(blob) > 1024,
                      (st, ctype, len(blob)))

    # ---------------------------------------------------------------- copies
    # A collapsed card must list its copies, and each must be separately addressable:
    # publish targets one platform, so a card that cannot name its entries breaks it.
    for it in multi[:1]:
        key = nav_key(it)
        st, d = api("/api/games/" + urllib.request.quote(key, safe=""))
        copies = d.get("copies") or d.get("also_owned_on") or []
        check("a multi-platform card lists its copies", len(copies) >= 1, copies)
        for c in copies:
            ek = c.get("entry_key")
            check("each copy is addressable on its own: " + str(ek),
                  bool(ek) and "@" in ek, ek)
            st, cd = api("/api/games/" + urllib.request.quote(ek, safe=""))
            check("and its entry key opens a detail page: " + str(ek), st == 200, st)

    # ---------------------------------------------------------------- the canary
    # A REGRESSION TEST NOBODY HAS SEEN FAIL IS WORTH LITTLE. Every check above asserts
    # "not empty", and the failure it guards against was a 200 with an empty body. So
    # prove the detectors have teeth: ask the same endpoints for a game that cannot
    # exist, and confirm they answer the way the BROKEN state answered. If these three
    # stop holding, the checks above have gone blind and are passing for free.
    ghost = "igdb:999999999"
    st, gd = api("/api/games/" + ghost)
    check("canary: an unresolvable key does not fabricate a game",
          st == 404 or not (gd or {}).get("title"), (st, gd))
    st, gl = api("/api/games/" + ghost + "/media")
    check("canary: and its media library is empty, which is what the bug looked like",
          st != 200 or not (gl or {}).get("assets"), (st, gl))
    st, gb, gct = api("/api/media/%s/hero" % ghost, raw=True)
    check("canary: and no image streams for it",
          st != 200 or not gct.startswith("image/"), (st, gct, len(gb)))

    # ---------------------------------------------------------------- facets
    st, f = api("/api/facets")
    check("facets answer", st == 200 and isinstance(f.get("platforms"), list), st)

    print("RESULT: %d checks, all passed" % len(PASSED))


if __name__ == "__main__":
    main()
