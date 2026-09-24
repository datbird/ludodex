#!/usr/bin/env python3
"""The whole web UI, driven in a real browser against a RUNNING ludodex.

The two live tests beside this one prove narrow things: the API contract, and that a
game page paints its art. This one walks EVERY screen a user can reach and checks that
what the screen shows is what the API says, with the API as the oracle:

  * sign-in, session survives a reload, the profile menu names the user, sign-out
  * the dashboard numbers against /api/stats, and each "needs attention" card against
    the library view it opens
  * the library: posters and table, a set of real searches (exact, partial, platform,
    no-hit, special characters, a very long query, the query language), filters, sort,
    status scope, paging, per-page and layout persistence, select mode
  * game pages of several kinds: title, art that really decoded, sources, store links,
    metadata-provider chips, the provider and "Fetch from" menus, media overlays,
    related games and Back, Escape closing the right layer
  * every Settings section and sub-tab renders without a panel crash, secrets masked
  * the job monitor, the sync and server menus, Files and Publish
  * at a desktop AND a phone viewport, with screenshots of each main screen

SAFETY. It runs as whoever you sign in as, so every request the PAGE makes goes through
a guard: anything that is not a GET is ABORTED unless the step running right now armed
that exact endpoint (and, for /api/prefs, those exact keys). An aborted request is
logged and reported as a finding: it means a screen fired a write the moment it opened.
It never presses anything that syncs, downloads, deletes, merges, resets, restarts,
spends AI money or edits users, keys or access rules.

Two modes:

  default                  read-only. The only writes are the sign-in and sign-out.
  LUDODEX_UI_WRITES=1      also round-trips a set of REVERSIBLE settings through the UI
                           (media storage mode, hide non-games, Xbox platform, file-op
                           apply mode, spotlight seconds and categories, one game's
                           metadata-provider toggle). Each one: read the value from the
                           API, change it in the UI, prove the API persisted it, put it
                           back, prove the restore. A failed restore is reported loudly
                           and fails the run.

It CONNECTS to an existing Chromium over CDP and works only inside a context it creates
itself, so a shared, logged-in browser keeps its tabs, cookies and profile untouched.
It never closes the browser, only its own context.

Environment:
    LUDODEX_LIVE_TESTS=1            required, same gate as the other live tests
    LUDODEX_URL                     the running instance, e.g. http://<host>:8001
    LUDODEX_USER / LUDODEX_PASS     a login (writes mode needs an admin)
    LUDODEX_CDP                     CDP endpoint of the browser (default 127.0.0.1:9222)
    LUDODEX_SHOTS                   screenshot + report.json dir (default /tmp/ludodex-ui-shots)
    LUDODEX_UI_WRITES=1             enable the reversible round trips (see above)
    LUDODEX_UI_VIEWPORTS            comma list of desktop,phone (default both)

Use scripts/run_live_ui.sh to run it inside a browser container without putting the
password on any command line. See docs/TESTING.md.
"""
import json
import os
import re
import sys
import time
import traceback
from urllib.parse import parse_qs, quote, unquote, urlparse

if os.environ.get("LUDODEX_LIVE_TESTS") != "1":
    sys.exit("SKIPPED: live test. It drives a real browser against a RUNNING ludodex. "
             "Re-run with LUDODEX_LIVE_TESTS=1, LUDODEX_URL, LUDODEX_USER and LUDODEX_PASS.")

URL = (os.environ.get("LUDODEX_URL") or "").rstrip("/")
USER = os.environ.get("LUDODEX_USER") or ""
PASS = os.environ.get("LUDODEX_PASS") or ""
if not (URL and USER and PASS):
    sys.exit("SKIPPED: LUDODEX_URL, LUDODEX_USER and LUDODEX_PASS are required.")
CDP = os.environ.get("LUDODEX_CDP", "http://127.0.0.1:9222")
SHOTS = os.environ.get("LUDODEX_SHOTS", "/tmp/ludodex-ui-shots")
WRITES = os.environ.get("LUDODEX_UI_WRITES") == "1"
VIEWPORTS = {"desktop": {"width": 1440, "height": 900},
             "phone": {"width": 390, "height": 844}}
WANT_VP = [v.strip() for v in os.environ.get("LUDODEX_UI_VIEWPORTS", "desktop,phone").split(",")
           if v.strip() in VIEWPORTS]

from playwright.sync_api import sync_playwright  # noqa: E402  (after the skip gate)

# The seven metadata providers the detail page draws a disable toggle for. Must match
# META_PROVIDERS in web/src/App.tsx.
META_PROVIDERS = {"igdb", "screenscraper", "steamgriddb", "thegamesdb", "arcadedb",
                  "zxinfo", "mobygames"}
STRIP_KINDS = ["screenshot", "video", "manual"]
# A store link the server builds must point at the store, and for Steam at the very app
# id the source row carries. Stores missing here are only required to be https.
STORE_URL = {
    "steam": r"^https://store\.steampowered\.com/app/(\d+)",
    "gog": r"^https://(www\.)?gog\.com/",
    "itch": r"^https://[a-z0-9-]+\.itch\.io/",
    "epic": r"^https://store\.epicgames\.com/",
}

# ------------------------------------------------------------------ result bookkeeping
RESULTS = []          # (vp, area, label, ok, detail)
FINDINGS = {"aborted": [], "console": [], "pageerror": [], "http": [], "reqfail": [],
            "covered": [], "overflow": []}
RESTORES = []         # one row per reversible setting
ALLOWED_WRITES = []   # writes the guard let through because a step armed them
STATE = {"vp": "", "where": "", "armed": [], "expect_http": set(), "aborted_urls": set()}


def scrub(s):
    s = str(s)
    return s.replace(PASS, "***") if PASS else s


def check(area, label, ok, detail=""):
    ok = bool(ok)
    RESULTS.append((STATE["vp"], area, label, ok, "" if ok else scrub(detail)[:400]))
    print("  %s  [%s/%s] %s%s" % ("ok  " if ok else "FAIL", STATE["vp"], area, label,
                                   "" if ok else "   <- " + scrub(detail)[:300]), flush=True)
    return ok


def at(where):
    STATE["where"] = where


def num(text):
    d = re.sub(r"[^\d]", "", text or "")
    return int(d) if d else None


# ------------------------------------------------------------------ the write guard
# A GET that would call a model or start work. None exist today (every AI call and
# every job start is a POST), so this is empty on purpose: add to it the day one does.
BLOCKED_GET = [
    # (path regex, reason)
]


def guard(route, request):
    u = urlparse(request.url)
    path = unquote(u.path)
    m = request.method
    entry = {"vp": STATE["vp"], "where": STATE["where"], "method": m, "path": path,
             "query": u.query[:160]}
    if m in ("GET", "HEAD", "OPTIONS"):
        for rx, why in BLOCKED_GET:
            if re.search(rx, path):
                entry["why"] = why
                FINDINGS["aborted"].append(entry)
                STATE["aborted_urls"].add(request.url)
                return route.abort()
        return route.continue_()
    if m == "POST" and path == "/api/auth/login":
        return route.continue_()
    body = None
    try:
        body = request.post_data_json
    except Exception:  # noqa: BLE001  (not JSON)
        body = None
    for am, apath, keys in STATE["armed"]:
        if am == m and apath == path:
            if keys is None or (isinstance(body, dict) and set(body) <= set(keys)):
                ALLOWED_WRITES.append(dict(entry, body=body))
                return route.continue_()
    entry["body_keys"] = sorted(body) if isinstance(body, dict) else None
    FINDINGS["aborted"].append(entry)
    STATE["aborted_urls"].add(request.url)
    print("  ..  guard aborted %s %s (at %s)" % (m, path, STATE["where"]), flush=True)
    return route.abort()


class Armed:
    """`with Armed(("POST", "/api/prefs", {"media_mode"})):` lets exactly that through."""

    def __init__(self, *rules):
        self.rules = list(rules)

    def __enter__(self):
        STATE["armed"].extend(self.rules)

    def __exit__(self, *a):
        for r in self.rules:
            STATE["armed"].remove(r)


def watch(page):
    def on_console(msg):
        if msg.type != "error":
            return
        loc = (msg.location or {}).get("url", "")
        if loc in STATE["aborted_urls"]:
            return               # the browser reporting our own abort
        txt = msg.text
        if "status of 401" in txt and "login" in STATE["expect_http"]:
            return
        if "net::ERR_FAILED" in txt and any(a in txt for a in STATE["aborted_urls"]):
            return
        FINDINGS["console"].append({"vp": STATE["vp"], "where": STATE["where"],
                                    "text": scrub(txt)[:300], "url": loc[-120:]})

    def on_pageerror(err):
        FINDINGS["pageerror"].append({"vp": STATE["vp"], "where": STATE["where"],
                                      "text": scrub(err)[:300]})

    def on_response(r):
        if r.status < 400:
            return
        p = urlparse(r.url).path
        if p == "/api/auth/login" and "login" in STATE["expect_http"]:
            return
        FINDINGS["http"].append({"vp": STATE["vp"], "where": STATE["where"],
                                 "status": r.status, "method": r.request.method,
                                 "url": r.url.replace(URL, "")[:200]})

    def on_reqfail(req):
        if req.url in STATE["aborted_urls"]:
            return
        f = (req.failure or "")
        if "ERR_ABORTED" in f:          # navigation or an AbortController, not an error
            return
        FINDINGS["reqfail"].append({"vp": STATE["vp"], "where": STATE["where"],
                                    "failure": f, "url": req.url.replace(URL, "")[:200]})

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("response", on_response)
    page.on("requestfailed", on_reqfail)


# ------------------------------------------------------------------ physical checks
ON_TOP_JS = """(el) => {
  el.scrollIntoView({block: 'center', inline: 'center'})
  const r = el.getBoundingClientRect()
  if (r.width < 2 || r.height < 2) return {ok: false, why: 'zero-sized'}
  const x = r.left + r.width / 2, y = r.top + r.height / 2
  if (x < 0 || y < 0 || x > innerWidth || y > innerHeight)
    return {ok: false, why: 'off screen at ' + Math.round(x) + ',' + Math.round(y)}
  const hit = document.elementFromPoint(x, y)
  const ok = hit === el || el.contains(hit) || (hit && hit.contains(el))
  const d = (n) => n ? n.tagName.toLowerCase() + (n.className && n.className.baseVal === undefined
    ? '.' + String(n.className).trim().replace(/\\s+/g, '.') : '') : 'null'
  return {ok, why: ok ? '' : 'covered by ' + d(hit)}
}"""

DECODED_JS = """(el) => new Promise((resolve) => {
  if (el.tagName !== 'IMG') return resolve({ok: true, nat: null})
  const done = () => resolve({ok: el.naturalWidth > 0, nat: el.naturalWidth, src: el.currentSrc.slice(-90)})
  if (el.complete && el.naturalWidth > 0) return done()
  el.loading = 'eager'
  el.addEventListener('load', done, {once: true})
  el.addEventListener('error', done, {once: true})
  setTimeout(done, 15000)
})"""


def on_top(area, label, loc):
    """Is the element really the thing at its own centre? isVisible() cannot say."""
    try:
        if not loc.count():
            return check(area, label + " exists", False, "no element")
        r = loc.first.evaluate(ON_TOP_JS)
    except Exception as e:  # noqa: BLE001
        return check(area, label + " is on top", False, e)
    if not r["ok"]:
        FINDINGS["covered"].append({"vp": STATE["vp"], "where": STATE["where"],
                                    "element": label, "why": r["why"]})
    return check(area, label + " is on top (elementFromPoint)", r["ok"], r["why"])


def decoded(area, label, loc):
    try:
        loc.first.scroll_into_view_if_needed(timeout=5000)
        r = loc.first.evaluate(DECODED_JS)
    except Exception as e:  # noqa: BLE001
        return check(area, label, False, e)
    return check(area, label, r["ok"], r)


def no_overflow(area, page, label):
    w = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    ok = w[0] <= w[1] + 1
    if not ok:
        FINDINGS["overflow"].append({"vp": STATE["vp"], "where": label,
                                     "scrollWidth": w[0], "innerWidth": w[1]})
    return check(area, "%s: no horizontal page scroll" % label, ok,
                 "scrollWidth %d > innerWidth %d" % tuple(w))


def shot(page, name):
    os.makedirs(SHOTS, exist_ok=True)
    p = os.path.join(SHOTS, "%s-%s.png" % (STATE["vp"], name))
    try:
        page.screenshot(path=p)
    except Exception as e:  # noqa: BLE001
        print("  ..  screenshot %s failed: %s" % (name, e))
    return p


def settle(page, ms=350):
    page.wait_for_timeout(ms)


# ------------------------------------------------------------------ API oracle
class Api:
    def __init__(self, ctx):
        self.req = ctx.request      # shares the context's cookies; not routed by the guard

    def get(self, path):
        r = self.req.get(URL + path, timeout=60000)
        if r.status >= 400:
            raise RuntimeError("GET %s -> %d" % (path, r.status))
        return r.json()

    def post(self, path, body):
        r = self.req.post(URL + path, data=json.dumps(body),
                          headers={"Content-Type": "application/json"}, timeout=60000)
        if r.status >= 400:
            raise RuntimeError("POST %s -> %d" % (path, r.status))
        return r.json()


def qs_of(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query, keep_blank_values=True).items()}


def is_games(r):
    return urlparse(r.url).path == "/api/games" and r.request.method == "GET"


def ui_games(page, action, pred, timeout=30000):
    """Run `action` and return (params, body) of the /api/games call it caused."""
    with page.expect_response(lambda r: is_games(r) and pred(qs_of(r.url)),
                              timeout=timeout) as info:
        action()
    r = info.value
    return qs_of(r.url), r.json()


def shown_titles(page):
    if page.locator(".game-table").count():
        return page.locator(".game-table tbody td.gt-title").all_inner_texts()
    return page.locator(".grid .card .title").all_inner_texts()


def wait_count(page, total):
    want = "{:,} result{}".format(total, "" if total == 1 else "s")
    page.wait_for_function(
        "(t) => ((document.querySelector('.results-bar .count') || {}).textContent || '')"
        ".startsWith(t)", arg=want, timeout=20000)


def grid_matches(area, label, page, body):
    """The screen shows exactly the rows and count the API answered."""
    try:
        wait_count(page, body["total"])
        ok_count = True
    except Exception:  # noqa: BLE001
        ok_count = False
    txt = page.locator(".results-bar .count").first.inner_text() if \
        page.locator(".results-bar .count").count() else ""
    check(area, label + ": the count shown is the API total", ok_count,
          "shown %r, API total %d" % (txt, body["total"]))
    want = [i["title"] for i in body["items"]]
    try:
        page.wait_for_function(
            "(n) => document.querySelectorAll('.grid .card, .game-table tbody tr').length >= n",
            arg=min(len(want), 1) if want else 0, timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    settle(page, 250)
    got = shown_titles(page)
    check(area, label + ": the titles shown are the API's, in order", got[:len(want)] == want,
          {"first_diff": next(((i, a, b) for i, (a, b) in enumerate(zip(got, want)) if a != b),
                              None), "shown": len(got), "api": len(want)})


# ------------------------------------------------------------------ navigation helpers
def load_app(page):
    page.goto(URL + "/?cb=%d" % int(time.time() * 1000), wait_until="domcontentloaded",
              timeout=45000)
    page.wait_for_selector('input[type="password"], .pt-tab', timeout=45000)


def sign_in(page, password=None):
    inputs = page.locator(".auth-card input")
    inputs.nth(0).click()
    page.keyboard.type(USER)
    inputs.nth(1).click()
    page.keyboard.type(password if password is not None else PASS)
    page.locator(".auth-submit").click()


def tab(page, name):
    page.locator(".main-tabs .pt-tab", has_text=name).first.click()


def to_library(page):
    tab(page, "Library")
    page.wait_for_selector("[data-reveal-key], .game-table", timeout=45000)


def esc(page, n=1):
    for _ in range(n):
        page.keyboard.press("Escape")
        settle(page, 200)


def close_everything(page):
    """Best effort back to a clean app, used after a step crashed."""
    for _ in range(4):
        if not page.locator(".overlay, .filter-menu, .profile-menu").count():
            break
        esc(page)
    if page.locator(".overlay").count():
        load_app(page)


SEARCH = ".controls-search input.search"


def search(page, text):
    """Type a basic search and return the /api/games answer it caused."""
    box = page.locator(SEARCH)
    if box.input_value() == text:
        return None
    return ui_games(page, lambda: box.fill(text), lambda qs: qs.get("q", "") == text)


def clear_search(page):
    box = page.locator(SEARCH)
    if box.input_value():
        ui_games(page, lambda: box.fill(""), lambda qs: "q" not in qs)


def open_filters(page):
    if not page.locator(".filter-menu:not(.view-menu)").count():
        page.locator(".filter-wrap .filter-btn", has_text="Filters").first.click()
        page.wait_for_selector(".filter-menu .filter-grid", timeout=10000)


def close_menu(page):
    if page.locator(".filter-menu").count():
        esc(page)
    if page.locator(".filter-menu").count():          # Escape did not close it
        page.mouse.click(5, 5)
        settle(page)


def open_view(page, section):
    if not page.locator(".view-menu").count():
        page.locator(".filter-wrap .filter-btn", has_text="View").first.click()
        page.wait_for_selector(".view-menu", timeout=10000)
    t = page.locator(".view-menu .vm-sec-toggle", has_text=section).first
    if t.get_attribute("aria-expanded") != "true":
        t.click()


def css_attr(v):
    return v.replace("\\", "\\\\").replace('"', '\\"')


def open_game(page, api, row):
    """Find a game through the search box, the way a user would, and open it."""
    key = row.get("card_key") or row.get("entry_key") or row["norm_key"]
    to_library(page)
    close_menu(page)
    res = search(page, row["title"])
    card = page.locator('[data-reveal-key="%s"]' % css_attr(key))
    card.first.wait_for(timeout=20000)
    card.first.click()
    page.wait_for_selector(".game-panel .hero-sub", timeout=30000)
    return key, res


# ------------------------------------------------------------------ areas
def area_auth(page, api):
    A = "auth"
    at("auth/login")
    load_app(page)
    check(A, "the sign-in form renders (after its fetch)",
          page.locator('input[type="password"]').count() == 1)
    shot(page, "login")
    # a wrong password is refused, and says so
    STATE["expect_http"].add("login")
    try:
        with page.expect_response(lambda r: urlparse(r.url).path == "/api/auth/login") as info:
            sign_in(page, password="definitely-not-the-password")
        check(A, "a wrong password is refused with 401", info.value.status == 401,
              info.value.status)
        page.wait_for_selector(".auth-err", timeout=10000)
        check(A, "and the form shows why", bool(page.locator(".auth-err").inner_text().strip()))
    finally:
        STATE["expect_http"].discard("login")
    load_app(page)
    sign_in(page)
    page.wait_for_selector(".pt-tab", timeout=45000)
    st = api.get("/api/auth/status")
    check(A, "signing in makes the session authenticated",
          st.get("authenticated") and (st.get("user") or {}).get("username") == USER, st)
    tabs = page.locator(".main-tabs .pt-tab").all_inner_texts()
    want = ["Dashboard", "Library", "Publish"] + (["Files"] if st["user"]["role"] == "admin" else [])
    check(A, "the main tabs are the ones this role gets", sorted(tabs) == sorted(want),
          {"shown": tabs, "want": want})
    active = page.locator(".main-tabs .pt-tab.active")
    check(A, "the app opens on the Dashboard",
          active.count() == 1 and active.first.inner_text() == "Dashboard",
          active.all_inner_texts())
    at("auth/reload")
    load_app(page)
    check(A, "the session survives a reload (no sign-in form)",
          page.locator('input[type="password"]').count() == 0 and
          page.locator(".pt-tab").count() > 0)
    at("auth/profile")
    page.locator("button.profile").click()
    page.wait_for_selector(".profile-menu", timeout=5000)
    check(A, "the profile menu names the user",
          page.locator(".profile-menu .pm-name").inner_text().strip() == USER,
          page.locator(".profile-menu .pm-name").inner_text())
    want_sub = "Administrator" if st["user"]["role"] == "admin" else "Signed in"
    check(A, "and states the role", page.locator(".profile-menu .pm-sub").inner_text().strip()
          == want_sub, page.locator(".profile-menu .pm-sub").inner_text())
    shot(page, "profile-menu")
    esc(page)
    check(A, "Escape closes the profile menu", page.locator(".profile-menu").count() == 0)
    return st["user"]


def area_dashboard(page, api):
    A = "dashboard"
    at("dashboard")
    tab(page, "Dashboard")
    page.wait_for_selector(".dashboard .dash-cards .dc-num", timeout=30000)
    s = api.get("/api/stats")
    nums = [num(t) for t in page.locator(".dashboard .dash-cards:not(.attn-cards) .dc-num")
            .all_inner_texts()]
    srcs = len([n for n in s["by_source"].values() if n > 0])
    want = [s.get("identified", s["games"]), s["media"]["games_with_art"], s["cross_source"], srcs]
    check(A, "the four headline numbers are /api/stats", nums == want, {"shown": nums, "want": want})
    lib_total = api.get("/api/games?limit=1")["total"]
    # The dashboard counts games; the grid also lists add-ons whose base game is not
    # owned, and /api/stats reports how many of those it left out.
    check(A, "the Games number equals the library's default (Owned) total, less add-ons",
          want[0] == lib_total - s.get("addons", 0),
          {"dashboard": want[0], "library_owned_total": lib_total, "addons": s.get("addons")})
    hdr = page.locator("header .stats").inner_text() if page.locator("header .stats").count() else ""
    check(A, "the header line carries the same identified count",
          "{:,}".format(want[0]) in hdr, hdr)
    rows = page.locator(".dash-panel").first.locator(".dash-bar-row")
    # names are compared lower-cased: the panel capitalises them in CSS
    shown_src = {r.locator(".dbr-name").inner_text().lower(): num(r.locator(".dbr-val").inner_text())
                 for r in rows.all()}
    want_src = {k: v for k, v in s["by_source"].items() if v > 0}
    check(A, "By source matches /api/stats.by_source", shown_src == want_src,
          {"shown": shown_src, "want": want_src})
    kinds = page.locator(".dash-panel").nth(1).locator(".dash-bar-row")
    shown_k = {r.locator(".dbr-name").inner_text().lower(): num(r.locator(".dbr-val").inner_text())
               for r in kinds.all()}
    check(A, "Media by kind matches /api/stats.media.by_kind",
          shown_k == s["media"]["by_kind"], {"shown": shown_k, "want": s["media"]["by_kind"]})
    # the spotlight paints real covers
    try:
        page.wait_for_selector(".spotlight .sl-card", timeout=20000)
        n = page.locator(".spotlight .sl-card").count()
        check(A, "the Spotlight renders cards", n > 0, n)
        img = page.locator(".spotlight .sl-card img")
        if img.count():
            decoded(A, "a Spotlight cover really decoded", img)
    except Exception as e:  # noqa: BLE001
        check(A, "the Spotlight renders", False, e)
    no_overflow(A, page, "dashboard")
    on_top(A, "the Settings button", page.locator('button[title="Settings"]'))
    on_top(A, "the profile button", page.locator("button.profile"))
    shot(page, "dashboard")
    return s


def area_dashboard_cards(page, api, stats):
    """Each 'needs attention' card opens the library view it counts."""
    A = "dashboard"
    cards = [("Unmatched", "exclude", "matched", stats.get("unmatched")),
             ("No media", "exclude", "has_media", stats.get("no_media")),
             ("Cover undecided", "include", "cover_undecided", stats.get("cover_undecided")),
             ("Low confidence", "include", "low_confidence", stats.get("low_confidence"))]
    for label, mode, flag, n in cards:
        at("dashboard/card:" + label)
        tab(page, "Dashboard")
        card = page.locator(".attn-cards .dash-card", has_text=label)
        if not card.count():
            check(A, "card %r present when stats has it" % label, not n, n)
            continue
        qs, body = ui_games(page, lambda: card.first.click(),
                            lambda q: flag in q.get(mode, "").split(","))
        check(A, "%r card opens the library filtered to %s=%s" % (label, mode, flag),
              qs.get(mode) == flag, qs)
        check(A, "%r card number equals the count of the view it opens" % label,
              body["total"] == n, {"card": n, "view_total": body["total"], "params": qs})
        grid_matches(A, "%r view" % label, page, body)
        # back to no filters
        open_filters(page)
        clr = page.locator(".filter-menu .filter-clear")
        if clr.count():
            ui_games(page, lambda: clr.first.click(),
                     lambda q: "include" not in q and "exclude" not in q)
        close_menu(page)


def all_owned(api):
    first = api.get("/api/games?limit=1000&offset=0")
    items = list(first["items"])
    while len(items) < first["total"]:
        items += api.get("/api/games?limit=1000&offset=%d" % len(items))["items"]
    return items


def area_library(page, api, owned, full=True):
    A = "library"
    at("library/open")
    # The first library fetch happens when the APP mounts, not when the tab is picked,
    # so start from a fresh load to see it.
    with page.expect_response(lambda r: is_games(r), timeout=45000) as info:
        load_app(page)
    tab(page, "Library")
    body = info.value.json()
    page.wait_for_selector("[data-reveal-key]", timeout=45000)
    qs = qs_of(info.value.url)
    check(A, "the first load asks for the default page (50, owned, identified only)",
          qs.get("limit") == "50" and "status" not in qs and "identified" not in qs, qs)
    grid_matches(A, "default view", page, body)
    covers = page.locator(".grid .card img")
    check(A, "posters carry cover images", covers.count() > 0, covers.count())
    for i in range(min(4, covers.count())):
        decoded(A, "poster %d cover decoded" % (i + 1), covers.nth(i))
    on_top(A, "the first poster", page.locator(".grid .card").first)
    on_top(A, "the search box", page.locator(SEARCH))
    on_top(A, "the Filters button", page.locator(".filter-btn", has_text="Filters"))
    no_overflow(A, page, "library")
    shot(page, "library")

    # ---- searches, each against an oracle computed from the whole owned list ----
    titles = [g["title"] for g in owned]
    exact = next((t for t in titles if len(t) > 8 and sum(t == x for x in titles) == 1), titles[0])
    word = next((w for t in titles for w in re.findall(r"[A-Za-z]{5,}", t)
                 if 3 <= sum(w.lower() in x.lower() for x in titles) <= 40), "Star")
    apos = next((t for t in titles if "'" in t), None)
    colon = next((t for t in titles if ":" in t), None)
    nonascii = next((t for t in titles if re.search(r"[^\x00-\x7f]", t)), None)
    queries = [("exact title", exact), ("partial word", word), ("platform name", "Switch"),
               ("no hit", "zqxjv no such game 7731"), ("percent sign", "%"),
               ("underscore", "_"), ("ampersand", "&"), ("long query", "legend " * 45)]
    if apos:
        queries.append(("apostrophe", apos.split("'")[0][-4:] + "'" + apos.split("'")[1][:3]))
    if colon:
        queries.append(("colon", colon))
    if nonascii:
        queries.append(("non-ASCII", nonascii))
    if not full:
        queries = queries[:2]
    for label, q in queries:
        at("library/search:" + label)
        try:
            res = search(page, q)
            if res is None:
                continue
            qs, body = res
            oracle = sum(q.lower() in t.lower() for t in titles)
            check(A, "search %s (%r) sends exactly that q" % (label, q[:40]), qs.get("q") == q,
                  qs.get("q"))
            check(A, "search %s: API total equals titles literally containing it" % label,
                  body["total"] == oracle,
                  {"q": q[:40], "api_total": body["total"], "literal_matches": oracle,
                   "sample_nonmatching": [i["title"] for i in body["items"]
                                          if q.lower() not in i["title"].lower()][:4]})
            grid_matches(A, "search %s" % label, page, body)
            if body["total"] == 0:
                check(A, "search %s: an empty result shows no stale cards" % label,
                      len(shown_titles(page)) == 0, shown_titles(page)[:3])
        except Exception as e:  # noqa: BLE001
            check(A, "search %s ran" % label, False, e)
    clear_search(page)
    if not full:
        return

    # ---- the query language ----
    at("library/query-mode")
    try:
        pill = page.locator(".mode-pill")
        pill.locator(".mp-seg.cur").click()
        pill.locator(".mp-seg", has_text="Query").click()
        box = page.locator(SEARCH)
        qs, body = ui_games(page, lambda: box.fill("platform:switch"),
                            lambda q: q.get("query") == "platform:switch")
        check(A, "Query mode sends query= not q=", "q" not in qs, qs)
        # field:value is a CONTAINS match, so platform:switch also takes switch2
        oracle = sum(any("switch" in p for p in (g.get("platforms") or "").split(","))
                     for g in owned)
        check(A, "platform:switch returns the games on any *switch* platform",
              body["total"] == oracle, {"api": body["total"], "oracle": oracle})
        grid_matches(A, "query platform:switch", page, body)
        ui_games(page, lambda: box.fill(""), lambda q: "query" not in q and "q" not in q)
        pill.locator(".mp-seg.cur").click()
        pill.locator(".mp-seg", has_text="Basic").click()
        settle(page)
        check(A, "back in Basic mode", "Basic" in pill.locator(".mp-seg.cur").inner_text())
    except Exception as e:  # noqa: BLE001
        check(A, "query mode round trip", False, e)

    # ---- filters ----
    at("library/filters")
    open_filters(page)
    secs = [t.lower() for t in
            page.locator(".filter-menu .fg-section-toggle span:nth-child(2)").all_inner_texts()]
    check(A, "the filter panel has Status, Sources and Systems sections",
          {"status", "sources", "systems"} <= set(secs), secs)
    nattr = len((api.get("/api/facets").get("attributes") or {}))
    check(A, "and one section per facet attribute", len(secs) == 3 + nattr,
          {"sections": len(secs), "attributes": nattr})
    shot(page, "library-filters")
    platforms = sorted({p for g in owned for p in (g.get("platforms") or "").split(",") if p})
    plat = "switch" if "switch" in platforms else platforms[0]
    facets = api.get("/api/facets")
    genre = sorted((facets.get("attributes") or {}).get("genres") or [""])[0]
    plan = [("Has cover", "include", "has_cover",
             lambda g: g.get("has_cover")),
            (plat, "include", "system:" + plat,
             lambda g: plat in (g.get("platforms") or "").split(",")),
            ("Steam", "exclude", "source:steam", None)]
    if genre:
        plan.append((genre, "include", "attr:genres:" + genre, None))
    fsearch = page.locator(".filter-menu .filter-search")
    for name, mode, fid, pred in plan:
        try:
            fsearch.fill(name)
            cell = page.locator('.filter-menu button[aria-label="%s %s"]' % (mode, css_attr(name)))
            cell.first.wait_for(timeout=5000)
            qs, body = ui_games(page, lambda: cell.first.click(),
                                lambda q, fid=fid, mode=mode: fid in q.get(mode, "").split(","))
            check(A, "filter %s %s sends %s=%s" % (mode, name, mode, fid),
                  fid in qs.get(mode, "").split(","), qs)
            if pred:
                oracle = sum(1 for g in owned if pred(g))
                check(A, "filter %s %s: API total equals the oracle" % (mode, name),
                      body["total"] == oracle, {"api": body["total"], "oracle": oracle})
            else:
                check(A, "filter %s %s answers without error" % (mode, name),
                      isinstance(body.get("total"), int), body.get("total"))
            chip = page.locator(".filter-menu .fa-chip." + mode, has_text=name)
            check(A, "filter %s %s shows an Applied chip" % (mode, name), chip.count() > 0)
            grid_matches(A, "filter %s %s" % (mode, name), page, body)
            # remove it again through its chip
            ui_games(page, lambda: chip.first.click(),
                     lambda q, fid=fid, mode=mode: fid not in q.get(mode, "").split(","))
        except Exception as e:  # noqa: BLE001
            check(A, "filter %s %s round trip" % (mode, name), False, e)
    fsearch.fill("")

    # ---- ownership scope ----
    at("library/status")
    stats = api.get("/api/stats")
    for scope in ("Wanted", "All", "Utilities", "Owned"):
        try:
            btn = page.locator(".filter-menu .own-seg-btn", has_text=scope).first
            want = scope.lower()
            qs, body = ui_games(page, lambda: btn.click(),
                                lambda q, w=want: q.get("status", "owned") == w)
            check(A, "Show %s sends status=%s" % (scope, want),
                  qs.get("status", "owned") == want, qs)
            ref = api.get("/api/games?limit=1&status=%s" % want)
            check(A, "Show %s total matches the API" % scope, body["total"] == ref["total"],
                  {"ui": body["total"], "api": ref["total"]})
            if scope == "Wanted" and stats.get("wanted") is not None:
                badge = btn.locator(".own-seg-n")
                check(A, "the Wanted badge equals the Wanted view total",
                      badge.count() and num(badge.inner_text()) == body["total"],
                      {"badge": badge.inner_text() if badge.count() else None,
                       "view": body["total"], "stats.wanted": stats.get("wanted")})
        except Exception as e:  # noqa: BLE001
            check(A, "Show %s" % scope, False, e)
    close_menu(page)
    check(A, "Escape closes the filter panel", page.locator(".filter-menu").count() == 0)

    # ---- sort ----
    at("library/sort")
    for name, key in (("Title", "title"), ("Ludodex score", "ludodex_score")):
        try:
            open_view(page, "Sort")
            cell = page.locator('.view-menu button[aria-label="sort priority 1 by %s"]' % name)
            qs, body = ui_games(page, lambda: cell.first.click(),
                                lambda q, k=key: q.get("sort", "").split(",")[:1] == [k])
            check(A, "sort by %s sends sort=%s" % (name, key), qs.get("sort") == key, qs)
            grid_matches(A, "sort by %s" % name, page, body)
            if key == "ludodex_score":
                sc = [i.get("ludodex_score") for i in body["items"] if i.get("ludodex_score") is not None]
                mono = sc == sorted(sc) or sc == sorted(sc, reverse=True)
                check(A, "sort by score is monotonic on the first page", mono, sc[:12])
            else:
                t = [i["title"].lower() for i in body["items"]]      # COLLATE NOCASE
                check(A, "sort by title is alphabetical on the first page", t == sorted(t),
                      next(((a, b) for a, b in zip(t, t[1:]) if a > b), None))
            ui_games(page, lambda: cell.first.click(), lambda q: "sort" not in q)
        except Exception as e:  # noqa: BLE001
            check(A, "sort by %s" % name, False, e)
    close_menu(page)

    # ---- paging ----
    at("library/load-more")
    try:
        more = page.locator("button.more")
        n0 = len(shown_titles(page))
        qs, body = ui_games(page, lambda: more.click(), lambda q: q.get("offset") == str(n0))
        page.wait_for_function("(n) => document.querySelectorAll('.grid .card').length > n",
                               arg=n0, timeout=15000)
        got = shown_titles(page)
        check(A, "Load more appends the next page", got[n0:n0 + len(body["items"])] ==
              [i["title"] for i in body["items"]], {"shown": len(got), "offset": n0})
        ref = api.get("/api/games?limit=%s&offset=%d" % (qs.get("limit"), n0))
        check(A, "and that page is the API's offset page",
              [i["title"] for i in ref["items"]] == [i["title"] for i in body["items"]])
    except Exception as e:  # noqa: BLE001
        check(A, "Load more", False, e)

    # ---- per page + layout, both persisted across a reload ----
    at("library/per-page")
    try:
        open_view(page, "Per page")
        qs, body = ui_games(page, lambda: page.locator(".view-menu select.vm-perpage")
                            .select_option("25"), lambda q: q.get("limit") == "25")
        close_menu(page)
        page.wait_for_function("() => document.querySelectorAll('.grid .card').length === 25",
                               timeout=15000)
        check(A, "Per page 25 shows 25 posters", True)
        load_app(page)
        with page.expect_response(lambda r: is_games(r)) as info:
            tab(page, "Library")
        check(A, "per page persists across a reload",
              qs_of(info.value.url).get("limit") == "25", qs_of(info.value.url))
        page.wait_for_selector("[data-reveal-key]", timeout=30000)
        open_view(page, "Per page")
        ui_games(page, lambda: page.locator(".view-menu select.vm-perpage").select_option("50"),
                 lambda q: q.get("limit") == "50")
        close_menu(page)
    except Exception as e:  # noqa: BLE001
        check(A, "per page round trip", False, e)
        close_everything(page)

    at("library/table")
    try:
        open_view(page, "Layout")
        page.locator(".view-menu .view-toggle button", has_text="Table").click()
        page.wait_for_selector(".game-table tbody tr", timeout=15000)
        close_menu(page)
        check(A, "Table view renders one row per game",
              page.locator(".game-table tbody tr").count() == len(shown_titles(page)) > 0)
        on_top(A, "the first table row", page.locator(".game-table tbody tr").first)
        shot(page, "library-table")
        open_view(page, "Columns")
        score = page.locator(".view-menu .col-item", has_text="Score").first
        score.locator("input").click()
        check(A, "unticking Score removes the Score column",
              page.locator(".game-table th", has_text="Score").count() == 0)
        score.locator("input").click()
        check(A, "ticking it brings it back",
              page.locator(".game-table th", has_text="Score").count() == 1)
        close_menu(page)
        load_app(page)
        tab(page, "Library")
        page.wait_for_selector(".game-table, [data-reveal-key]", timeout=30000)
        check(A, "the table layout persists across a reload",
              page.locator(".game-table").count() == 1)
        open_view(page, "Layout")
        page.locator(".view-menu .view-toggle button", has_text="Posters").click()
        page.wait_for_selector("[data-reveal-key]", timeout=15000)
        close_menu(page)
        check(A, "back to posters", page.locator(".game-table").count() == 0)
    except Exception as e:  # noqa: BLE001
        check(A, "table view round trip", False, e)
        close_everything(page)

    # ---- select mode, and the two overlays that only OPEN here ----
    at("library/select")
    try:
        page.locator(".controls-right .filter-btn", has_text="Select").click()
        page.wait_for_selector(".select-bar", timeout=5000)
        page.locator(".grid .card").first.click()
        check(A, "select mode counts a picked game",
              page.locator(".select-bar .sel-count").inner_text().startswith("1 "))
        on_top(A, "the select bar", page.locator(".select-bar .sel-count"))
        page.locator(".controls-right .filter-btn", has_text="Cancel select").click()
        check(A, "Cancel select leaves select mode", page.locator(".select-bar").count() == 0)
    except Exception as e:  # noqa: BLE001
        check(A, "select mode", False, e)
        close_everything(page)
    at("library/add-game")
    try:
        page.locator(".controls-right .add-game").click()
        page.wait_for_selector(".overlay", timeout=5000)
        check(A, "Add game opens", page.locator(".overlay").count() > 0)
        shot(page, "add-game")
        esc(page)
        check(A, "Escape closes Add game", page.locator(".overlay").count() == 0)
    except Exception as e:  # noqa: BLE001
        check(A, "Add game overlay", False, e)
        close_everything(page)
    at("library/tools")
    try:
        page.locator(".controls-right .wand-btn").first.click()
        settle(page, 500)
        shot(page, "library-tools")
        esc(page)
        check(A, "the Tools menu opens and Escape closes it",
              page.locator(".overlay, .bt-menu, .filter-menu").count() == 0)
    except Exception as e:  # noqa: BLE001
        check(A, "Tools menu", False, e)
        close_everything(page)


def pick_games(api, owned):
    """Varied games, each taken from the API rather than invented."""
    picks = []
    steam = next((g for g in owned if "steam" in g.get("sources_summary", "")), None)
    if steam:
        picks.append(("store (steam)", steam))
    for src in ("gog", "itch", "epic", "nintendo"):
        g = next((g for g in owned if src in g.get("sources_summary", "")), None)
        if g:
            picks.append(("store (%s)" % src, g))
    retro = next((g for g in owned if set((g.get("platforms") or "").split(","))
                  & {"nes", "snes", "genesis", "ps1", "n64", "gba", "gb"}), None)
    if retro:
        picks.append(("retro platform", retro))
    multi = api.get("/api/games?include=cross_source&limit=5")["items"]
    if multi:
        picks.append(("multi-platform / cross-source", multi[0]))
    for c in api.get("/api/collections").get("collections", [])[:6]:
        hit = next((g for g in owned if g["norm_key"] == c["coll_key"]), None)
        if hit:
            picks.append(("compilation", hit))
            break
    seen, out = set(), []
    for label, g in picks:
        if g["norm_key"] not in seen:
            seen.add(g["norm_key"])
            out.append((label, g))
    return out


def detail_checks(page, api, label, row, deep=True):
    A = "detail"
    at("detail/" + label)
    key, _ = open_game(page, api, row)
    ek = row.get("entry_key") or key
    d = api.get("/api/games/" + quote(ek, safe=""))
    sub = page.locator(".game-panel .hero-sub").inner_text().strip()
    check(A, "%s: the page title is the API title" % label, sub == d["title"],
          {"shown": sub, "api": d["title"]})
    try:
        page.wait_for_selector(".game-panel .media-strip, .game-panel .art-strip", timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    hero = page.locator(".game-panel .hero-bg-frame img, .game-panel .hero-logo img, "
                        ".game-panel img.hero-logo")
    if hero.count():
        decoded(A, "%s: the hero art decoded" % label, hero)
    else:
        check(A, "%s: a game with a hero asset shows one" % label,
              page.locator(".game-panel .hero-marquee, .game-panel .hero-plain").count() > 0,
              "no hero image, no marquee, no plain fallback")
    rows = page.locator(".game-panel .sources-table tbody tr").count()
    check(A, "%s: one library row per API source" % label, rows == len(d["sources"]),
          {"rows": rows, "api": len(d["sources"])})

    # store chips: the server builds `url`, the UI must use it and it must be right
    for s in d["sources"]:
        if not s.get("url"):
            continue
        rx = STORE_URL.get(s["source"], r"^https://")
        m = re.match(rx, s["url"])
        good = bool(m) and (s["source"] != "steam" or m.group(1) == str(s.get("source_id")))
        check(A, "%s: the %s store URL is well formed and matches the id" % (label, s["source"]),
              good, {"url": s["url"], "source_id": s.get("source_id")})
        link = page.locator('.game-panel .idvia-stores a.idchip[href="%s"]' % css_attr(s["url"]))
        check(A, "%s: the %s store link %s has a chip" % (label, s["source"], s.get("source_id")),
              link.count() > 0,
              page.locator(".game-panel .idvia-stores a.idchip").evaluate_all(
                  "(as) => as.map((a) => a.href)"))
        if link.count():
            check(A, "%s: the %s link opens in a new tab safely" % (label, s["source"]),
                  link.first.get_attribute("target") == "_blank" and
                  "noreferrer" in (link.first.get_attribute("rel") or ""))

    # metadata-provider chips: one per matched provider, each with its disable toggle
    metas = [m for m in d.get("metadata_links", []) if m["provider"] in META_PROVIDERS]
    chips = page.locator(".game-panel .idvia .idchip-meta")
    check(A, "%s: one metadata chip per matched provider" % label, chips.count() == len(metas),
          {"chips": chips.count(), "api": [m["provider"] for m in metas]})
    check(A, "%s: every metadata chip carries its toggle" % label,
          all(c.locator(".idchip-act", has_text=re.compile(r"⊘|off")).count() == 1
              for c in chips.all()))
    plinks = {p["provider"]: p["url"] for p in d.get("provider_links", []) if p.get("url")}
    unlinked = [m["provider"] for m in metas if not m.get("url") and plinks.get(m["provider"])]
    check(A, "%s: a metadata chip links out when provider_links has its page" % label,
          not unlinked, {"chip_has_no_url_but_provider_links_does": unlinked})
    if not deep:
        on_top(A, "%s: the close button" % label, page.locator(".game-panel > button.close").last)
        no_overflow(A, page, "detail " + label)
        shot(page, "detail-" + re.sub(r"[^a-z]+", "-", label.lower()).strip("-"))
        esc(page)
        return d

    # the provider links menu, and Escape closing only it
    at("detail/%s/provider-menu" % label)
    pbtn = page.locator(".game-panel .media-strip > .prov-links:not(.mw-wrap) .prov-menu-btn")
    if plinks:
        try:
            pbtn.first.click()
            page.wait_for_selector(".prov-menu a.prov-menu-row", timeout=5000)
            hrefs = page.locator(".prov-menu a.prov-menu-row").evaluate_all(
                "(as) => as.map((a) => a.getAttribute('href'))")
            check(A, "%s: the providers menu lists provider_links" % label,
                  sorted(hrefs) == sorted(plinks.values()), {"menu": hrefs, "api": plinks})
            esc(page)
            check(A, "%s: Escape closes the providers menu and only it" % label,
                  page.locator(".prov-menu").count() == 0 and
                  page.locator(".game-panel").count() == 1)
        except Exception as e:  # noqa: BLE001
            check(A, "%s: providers menu" % label, False, e)

    # media strip counts
    lib = api.get("/api/games/%s/media" % quote(ek, safe=""))
    kinds = [k["kind"] for k in api.get("/api/media-kinds")["kinds"]]
    counts = {k: sum(1 for a in lib.get("assets", []) if a["kind"] == k) for k in STRIP_KINDS}
    for k, lbl in zip(STRIP_KINDS, ("Screenshots", "Videos", "Manuals")):
        if k not in kinds:
            continue
        b = page.locator(".game-panel .media-strip .ms-btn", has_text=lbl)
        n = num(b.locator(".ms-count").inner_text()) if b.count() else None
        check(A, "%s: %s count is the API's" % (label, lbl), n == counts[k],
              {"shown": n, "api": counts[k]})
    if counts.get("screenshot"):
        at("detail/%s/screenshots" % label)
        try:
            before = page.locator(".overlay").count()
            page.locator(".game-panel .media-strip .ms-btn", has_text="Screenshots").click()
            page.wait_for_function("(n) => document.querySelectorAll('.overlay').length > n",
                                   arg=before, timeout=8000)
            decoded(A, "%s: a screenshot decoded" % label, page.locator(".overlay").last.locator("img"))
            esc(page)
            check(A, "%s: Escape closes the screenshots overlay and only it" % label,
                  page.locator(".overlay").count() == before and page.locator(".game-panel").count())
        except Exception as e:  # noqa: BLE001
            check(A, "%s: screenshots overlay" % label, False, e)

    # All Media, and the "Fetch from" menu (listing only: never pressed)
    at("detail/%s/all-media" % label)
    try:
        page.locator(".game-panel .media-strip .ms-all").click()
        page.wait_for_selector(".allmedia-panel .am-grid", timeout=10000)
        cards = page.locator(".allmedia-panel .am-grid > *").count()
        check(A, "%s: All Media has one card per media kind" % label, cards == len(kinds),
              {"cards": cards, "kinds": len(kinds)})
        mp = api.get("/api/media/matched-providers/" + quote(d["norm_key"], safe=""))["providers"]
        page.locator(".allmedia-panel .mw-fetch").click()
        page.wait_for_selector(".mw-menu .mw-row", timeout=10000)
        rows_ = page.locator(".mw-menu .mw-row")
        enabled = sum(1 for r in rows_.all() if r.is_enabled())
        check(A, "%s: Fetch from lists every per-game provider" % label, rows_.count() == len(mp),
              {"menu": rows_.count(), "api": [p["provider"] for p in mp]})
        check(A, "%s: and enables exactly the matched ones" % label,
              enabled == sum(1 for p in mp if p["matched"]),
              {"enabled": enabled, "matched": [p["provider"] for p in mp if p["matched"]]})
        shot(page, "detail-fetch-menu")
        esc(page)
        check(A, "%s: Escape closes Fetch from and leaves All Media open" % label,
              page.locator(".mw-menu").count() == 0 and page.locator(".allmedia-panel").count() == 1)
        esc(page)
        check(A, "%s: a second Escape closes All Media and leaves the game open" % label,
              page.locator(".allmedia-panel").count() == 0 and page.locator(".game-panel").count() == 1)
    except Exception as e:  # noqa: BLE001
        check(A, "%s: All Media" % label, False, e)
        close_everything(page)
        return d

    # the tools menu opens, and Escape closes the menu before the page
    at("detail/%s/tools" % label)
    try:
        on_top(A, "%s: the game tools button" % label, page.locator(".game-panel .hero-tools-btn"))
        page.locator(".game-panel .hero-tools-btn").click()
        page.wait_for_selector(".hero-tools-menu", timeout=5000)
        check(A, "%s: the tools menu offers the wand and Resolve" % label,
              page.locator(".hero-tools-menu button").count() == 2)
        esc(page)
        check(A, "%s: Escape closes the tools menu, not the game" % label,
              page.locator(".hero-tools-menu").count() == 0 and page.locator(".game-panel").count())
    except Exception as e:  # noqa: BLE001
        check(A, "%s: tools menu" % label, False, e)

    # related / also-owned-on navigation, and Back
    at("detail/%s/navigate" % label)
    nav = page.locator(".game-panel .rel-section .rel-chip:not(.rel-more), "
                       ".game-panel .hero .also-on-chip")
    if nav.count():
        try:
            title0 = page.locator(".game-panel .hero-sub").inner_text()
            ent0 = d.get("entry_key")
            nav.first.click()
            page.wait_for_function(
                "(t) => { const e = document.querySelector('.game-panel .hero-sub');"
                " return e && e.textContent !== t }", arg=title0, timeout=15000)
            moved = page.locator(".game-panel .hero-sub").inner_text()
            check(A, "%s: a related/also-on chip navigates inside the overlay" % label,
                  page.locator(".game-panel").count() == 1, moved)
            back = page.locator(".game-panel .detail-back")
            check(A, "%s: and a Back button appears" % label, back.count() == 1)
            back.first.click()
            page.wait_for_function(
                "(t) => { const e = document.querySelector('.game-panel .hero-sub');"
                " return e && e.textContent === t }", arg=title0, timeout=15000)
            check(A, "%s: Back returns to the game it came from" % label, True, ent0)
        except Exception as e:  # noqa: BLE001
            check(A, "%s: related navigation and Back" % label, False, e)
    on_top(A, "%s: the close button" % label, page.locator(".game-panel > button.close").last)
    shot(page, "detail-" + re.sub(r"[^a-z]+", "-", label.lower()).strip("-"))
    no_overflow(A, page, "detail " + label)
    esc(page)
    try:
        page.wait_for_selector(".game-panel", state="detached", timeout=5000)
        check(A, "%s: Escape closes the game" % label, True)
    except Exception:  # noqa: BLE001
        check(A, "%s: Escape closes the game" % label, False, "still open")
        close_everything(page)
    return d


def area_details(page, api, owned, full=True):
    picks = pick_games(api, owned)
    check("detail", "found varied games to open", len(picks) >= 3, [p[0] for p in picks])
    if not full:
        picks = picks[:1]
    out = []
    for label, row in picks:
        try:
            out.append((label, row, detail_checks(page, api, label, row, deep=full)))
        except Exception as e:  # noqa: BLE001
            check("detail", "%s: opened and checked" % label, False,
                  "%s: %s" % (type(e).__name__, str(e)[:200]))
            shot(page, "detail-crash")
            close_everything(page)
    clear_search(page) if page.locator(SEARCH).count() else None
    return out


# ------------------------------------------------------------------ settings
def open_settings(page):
    if not page.locator(".settings-window").count():
        page.locator('button[title="Settings"]').click()
        page.wait_for_selector(".settings-window", timeout=10000)


def settings_goto(page, section, sub=None):
    open_settings(page)
    page.locator(".settings-nav .nav-item", has_text=section).first.click()
    if sub:
        page.locator(".settings-tabs .tab", has_text=sub).first.click()
    wait_panel(page)


def wait_panel(page, timeout=20000):
    try:
        page.wait_for_function(
            "() => { const c = document.querySelector('.settings-content');"
            " return c && !c.querySelector('.loading') && c.textContent.trim().length > 0 }",
            timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def area_settings(page, api, user, full=True):
    A = "settings"
    at("settings/open")
    open_settings(page)
    names = [t.strip() for t in page.locator(".settings-nav .nav-item").all_inner_texts()]
    names = [re.sub(r"^\W+\s*", "", n).strip() for n in names]
    want = {"AI settings", "Connections", "Library", "Dashboard", "AI Metadata"}
    if user["role"] == "admin":
        # admin-only: every panel in these reads an API the server refuses a user
        want |= {"Account & Users", "Database"}
    check(A, "the nav lists every section this role gets", set(names) == want,
          {"shown": names, "want": sorted(want)})
    check(A, "and in alphabetical order", names == sorted(names, key=str.lower), names)
    for i in range(page.locator(".settings-nav .nav-item").count()):
        nav = page.locator(".settings-nav .nav-item").nth(i)
        sec = re.sub(r"^\W+\s*", "", nav.inner_text()).strip()
        nav.click()
        subs = page.locator(".settings-tabs .tab")
        subnames = subs.all_inner_texts()
        for j, sub in enumerate(subnames):
            at("settings/%s/%s" % (sec, sub))
            subs.nth(j).click()
            ok = wait_panel(page)
            check(A, "%s > %s finishes loading" % (sec, sub), ok,
                  page.locator(".settings-content").inner_text()[:160])
            err = page.locator(".settings-content .errbound")
            check(A, "%s > %s renders without a panel crash" % (sec, sub), err.count() == 0,
                  err.first.inner_text()[:200] if err.count() else "")
            if full or j == 0:
                shot(page, "settings-%s-%s" % (re.sub(r"\W+", "-", sec.lower()),
                                               re.sub(r"\W+", "-", sub.lower())))
            if STATE["vp"] == "phone" and j == 0:
                no_overflow(A, page, "settings " + sec)
            if not full:
                break
    at("settings/search")
    s = page.locator(".settings-search")
    s.fill("cloudflare")
    res = page.locator(".settings-nav .nav-result")
    if user["role"] == "admin":
        check(A, "search 'cloudflare' finds Cloudflare Access",
              res.count() >= 1 and "Cloudflare Access" in " ".join(res.all_inner_texts()),
              res.all_inner_texts())
        if res.count():
            res.first.click()
            wait_panel(page)
            check(A, "and a hit jumps to it",
                  page.locator(".settings-tabs .tab.sel").inner_text() == "Cloudflare Access")
    s.fill("zzqqxx")
    check(A, "a search with no hits says so", page.locator(".settings-nav .nav-none").count() == 1)
    s.fill("")
    if full:
        secrets_masked(page, api)
        users_listed(page, api, user)
    esc(page)
    check(A, "Escape closes Settings", page.locator(".settings-window").count() == 0)


def secrets_masked(page, api):
    A = "settings"
    at("settings/AI settings/API Keys")
    settings_goto(page, "AI settings", "API Keys")
    masks = page.locator(".settings-content code.masked:not(.empty)").all_inner_texts()
    bad = [m for m in masks if not re.fullmatch(r".{1,3}….{0,4}", m.strip())]
    check(A, "configured AI keys show only first 3 + last 4", not bad,
          {"unmasked_count": len(bad)})
    pw = page.locator(".settings-content .key-row input")
    check(A, "AI key inputs are password fields",
          all(i.get_attribute("type") == "password" for i in pw.all()))
    svc = api.get("/api/services")
    leaks = []

    def walk(o, path=""):
        if isinstance(o, dict):
            if o.get("secret") and o.get("value") and "…" not in str(o["value"]):
                leaks.append(path + "/" + str(o.get("key")))
            for k, v in o.items():
                walk(v, path + "/" + str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + "/" + str(i))
    walk(svc)
    check(A, "/api/services returns every secret masked", not leaks, leaks[:5])


def users_listed(page, api, user):
    if user["role"] != "admin":
        return
    A = "settings"
    at("settings/Account & Users/Users")
    settings_goto(page, "Account & Users", "Users")
    u = api.get("/api/auth/users")
    txt = page.locator(".settings-content").inner_text()
    missing = [r["username"] for r in u["users"] if r["username"] not in txt]
    check(A, "the Users panel lists every user the API has", not missing, missing)


# ------------------------------------------------------------------ reversible writes
def restore_row(name, before, changed, after, ok, how):
    RESTORES.append({"setting": name, "before": before, "changed_to": changed,
                     "after_restore": after, "restored": ok, "how": how})
    if not ok:
        print("  !!!!  RESTORE FAILED for %s: before=%r after=%r" % (name, before, after),
              flush=True)


def pref_roundtrip(page, api, key, section, sub, click_to, label, expect_no_job=False):
    """click_to(value) clicks the UI control that sets `key` to value."""
    A = "writes"
    at("writes/" + key)
    before = api.get("/api/prefs")[key]
    target = label(before)
    after = None
    restored_via = "ui"
    jobs0 = api.get("/api/jobs")["jobs"]
    mj0 = api.get("/api/media/materialize")["media_job"]
    try:
        settings_goto(page, section, sub)
        with Armed(("POST", "/api/prefs", (key,))):
            with page.expect_response(lambda r: urlparse(r.url).path == "/api/prefs"
                                      and r.request.method == "POST", timeout=10000):
                click_to(target)
            now = api.get("/api/prefs")[key]
            check(A, "%s: the UI change persisted (%r -> %r)" % (key, before, target),
                  now == target, now)
            if expect_no_job:
                settle(page, 2500)
                jobs1 = api.get("/api/jobs")["jobs"]
                mj1 = api.get("/api/media/materialize")["media_job"]
                new = [j for j in jobs1 if j["id"] not in {x["id"] for x in jobs0}]
                check(A, "%s: changing it started no job and no download" % key,
                      not new and mj1 == mj0, {"new_jobs": new, "media_job": mj1})
            with page.expect_response(lambda r: urlparse(r.url).path == "/api/prefs"
                                      and r.request.method == "POST", timeout=10000):
                click_to(before)
    except Exception as e:  # noqa: BLE001
        check(A, "%s round trip through the UI" % key, False, e)
    finally:
        after = api.get("/api/prefs")[key]
        if after != before:
            restored_via = "api (UI restore did not take)"
            try:
                api.post("/api/prefs", {key: before})
            except Exception as e:  # noqa: BLE001
                print("  !!!!  restore POST failed: %s" % e)
            after = api.get("/api/prefs")[key]
        ok = after == before
        restore_row(key, before, target, after, ok, restored_via)
        check(A, "%s restored to %r" % (key, before), ok, after)


def radio(page, name, value_index):
    page.locator('.settings-content input[name="%s"]' % name).nth(value_index) \
        .locator("xpath=ancestor::label[1]").click()


def area_writes(page, api, owned):
    modes = ["ondemand", "chosen", "all"]
    pref_roundtrip(
        page, api, "media_mode", "Library", "Preferences",
        lambda v: radio(page, "media_mode", modes.index(v)),
        lambda b: "ondemand" if b != "ondemand" else "chosen", expect_no_job=True)
    pref_roundtrip(
        page, api, "xbox_platform", "Library", "Preferences",
        lambda v: radio(page, "xbox_platform", ["xbox", "pc"].index(v)),
        lambda b: "pc" if b == "xbox" else "xbox")
    pref_roundtrip(
        page, api, "fileops_apply_mode", "Library", "Preferences",
        lambda v: radio(page, "fileops_apply_mode", ["preview", "immediate"].index(v)),
        lambda b: "immediate" if b == "preview" else "preview")

    def hide_click(v):
        row = page.locator(".settings-content .pref-row", has_text="Hide non-games").first
        if row.locator("input").is_checked() != v:
            row.locator("label.switch").click()
    pref_roundtrip(page, api, "hide_non_games", "Library", "Preferences", hide_click,
                   lambda b: not b)

    def secs_click(v):
        presets = page.locator(".settings-content .pref-presets .preset")
        hit = presets.filter(has_text=re.compile(r"^%ds$" % v))
        if hit.count():
            hit.first.click()
        else:
            box = page.locator(".settings-content input.pref-num")
            box.fill(str(v))
            box.press("Enter")
    pref_roundtrip(page, api, "spotlight_seconds", "Dashboard", "Spotlight", secs_click,
                   lambda b: 20 if b != 20 else 30)
    area_spotlight_extras(page, api)
    area_local_prefs(page, api)
    area_identity_toggle(page, api, owned)


def area_spotlight_extras(page, api):
    A = "writes"
    # a spotlight category off and back on
    at("writes/spotlight_disabled")
    themes = api.get("/api/spotlight/themes")["themes"]
    before = sorted(api.get("/api/prefs")["spotlight_disabled"])
    target = next((t for t in themes if t["enabled"]), None)
    try:
        settings_goto(page, "Dashboard", "Spotlight")
        page.locator(".settings-content .pref-collapse", has_text="Spotlight categories").click()
        page.wait_for_selector(".settings-content .spot-theme", timeout=10000)
        n = page.locator(".settings-content .spot-theme").count()
        check(A, "Spotlight categories lists every theme", n == len(themes),
              {"shown": n, "api": len(themes)})
        if target:
            box = page.locator(".settings-content .spot-theme", has_text=target["title"]) \
                .first.locator("input")
            with Armed(("POST", "/api/prefs", ("spotlight_disabled",))):
                with page.expect_response(lambda r: urlparse(r.url).path == "/api/prefs"
                                          and r.request.method == "POST"):
                    box.click()
                now = api.get("/api/prefs")["spotlight_disabled"]
                check(A, "turning a theme off persisted", target["id"] in now, now)
                with page.expect_response(lambda r: urlparse(r.url).path == "/api/prefs"
                                          and r.request.method == "POST"):
                    box.click()
    except Exception as e:  # noqa: BLE001
        check(A, "spotlight theme round trip", False, e)
    finally:
        after = sorted(api.get("/api/prefs")["spotlight_disabled"])
        how = "ui"
        if after != before:
            how = "api"
            api.post("/api/prefs", {"spotlight_disabled": before})
            after = sorted(api.get("/api/prefs")["spotlight_disabled"])
        restore_row("spotlight_disabled", before, (target or {}).get("id"), after,
                    after == before, how)
        check(A, "spotlight_disabled restored", after == before, after)

    # "Include collections": the switch must survive closing and reopening Settings
    at("writes/spotlight_include_collections")
    try:
        settings_goto(page, "Dashboard", "Spotlight")
        row = page.locator(".settings-content .pref-row", has_text="Include collections").first
        was = row.locator("input").is_checked()
        with Armed(("POST", "/api/prefs", ("spotlight_include_collections",))):
            with page.expect_response(lambda r: urlparse(r.url).path == "/api/prefs"
                                      and r.request.method == "POST"):
                row.locator("label.switch").click()
            esc(page)
            settings_goto(page, "Dashboard", "Spotlight")
            row = page.locator(".settings-content .pref-row", has_text="Include collections").first
            kept = row.locator("input").is_checked()
            check(A, "Include collections survives reopening Settings", kept == (not was),
                  {"before": was, "after_reopen": kept,
                   "prefs_has_key": "spotlight_include_collections" in api.get("/api/prefs")})
            if kept != was:
                with page.expect_response(lambda r: urlparse(r.url).path == "/api/prefs"
                                          and r.request.method == "POST"):
                    row.locator("label.switch").click()
        restore_row("spotlight_include_collections (UI state)", was, not was,
                    row.locator("input").is_checked(), row.locator("input").is_checked() == was,
                    "ui")
    except Exception as e:  # noqa: BLE001
        check(A, "Include collections round trip", False, e)
    esc(page)


def area_local_prefs(page, api):
    """Theme and reduce-motion live in this browser's storage, not on the server."""
    A = "writes"
    at("writes/theme")
    try:
        cur = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        bg0 = page.evaluate("() => { const r = getComputedStyle(document.documentElement); const a = document.querySelector('.app') || document.body; return [r.getPropertyValue('--bg').trim(), getComputedStyle(a).color] }")
        page.locator("button.profile").click()
        page.locator(".profile-menu .pm-theme").click()
        flipped = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        check(A, "the theme toggle flips data-theme", flipped != cur and flipped in ("light", "dark"),
              {"before": cur, "after": flipped})
        settle(page, 600)
        bg1 = page.evaluate("() => { const r = getComputedStyle(document.documentElement); const a = document.querySelector('.app') || document.body; return [r.getPropertyValue('--bg').trim(), getComputedStyle(a).color] }")
        check(A, "and the theme colours really change with it", bg0 != bg1, [bg0, bg1])
        esc(page)
        shot(page, "theme-" + flipped)
        load_app(page)
        check(A, "the theme persists across a reload",
              page.evaluate("() => document.documentElement.getAttribute('data-theme')") == flipped)
        page.locator("button.profile").click()
        page.locator(".profile-menu .pm-theme").click()
        esc(page)
        back = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        load_app(page)
        after = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
        restore_row("theme (browser storage)", cur, flipped, after, after == cur, "ui")
        check(A, "the theme is back to %s after a reload" % cur, after == cur == back, after)
    except Exception as e:  # noqa: BLE001
        check(A, "theme round trip", False, e)
        close_everything(page)
    at("writes/reduce-motion")
    try:
        settings_goto(page, "Library", "Preferences")
        row = page.locator(".settings-content .pref-row", has_text="Reduce Motion").first
        was = row.locator("input").is_checked()
        row.locator("label.switch").click()
        esc(page)
        settings_goto(page, "Library", "Preferences")
        row = page.locator(".settings-content .pref-row", has_text="Reduce Motion").first
        check(A, "Respect Reduce Motion survives reopening Settings",
              row.locator("input").is_checked() == (not was))
        row.locator("label.switch").click()
        now = row.locator("input").is_checked()
        restore_row("reduce motion (browser storage)", was, not was, now, now == was, "ui")
        esc(page)
    except Exception as e:  # noqa: BLE001
        check(A, "reduce motion round trip", False, e)
        close_everything(page)


def area_identity_toggle(page, api, owned):
    """One metadata provider off and back on, for one game, through its chip."""
    A = "writes"
    at("writes/identity")
    row = d = prov = None
    for g in owned[:200]:
        dd = api.get("/api/games/" + quote(g["entry_key"], safe=""))
        cands = [m["provider"] for m in dd.get("metadata_links", [])
                 if m["provider"] in ("steamgriddb", "thegamesdb", "mobygames")]
        if cands and not dd.get("disabled_identity"):
            row, d, prov = g, dd, cands[0]
            break
    if not row:
        check(A, "found a game to round-trip a provider toggle on", False)
        return
    path = "/api/games/%s/identity/%s" % (d["norm_key"], prov)
    before = list(d.get("disabled_identity") or [])
    how = "ui"
    try:
        open_game(page, api, row)
        chip = page.locator(".game-panel .idchip-meta", has_text=re.compile(
            {"steamgriddb": "SteamGridDB", "thegamesdb": "TheGamesDB",
             "mobygames": "MobyGames"}[prov], re.I)).first
        with Armed(("POST", path, None)):
            with page.expect_response(lambda r: unquote(urlparse(r.url).path) == path):
                chip.locator(".idchip-act", has_text="⊘").click()
            now = api.get("/api/games/" + quote(row["entry_key"], safe=""))["disabled_identity"]
            check(A, "disabling %s on %r persisted" % (prov, row["title"]), prov in now, now)
            # The chip must STAY, drawn as off, because its "off" button is the only way
            # back. Give the detail reload time to land, then look.
            try:
                page.wait_for_selector(".game-panel .idchip-meta.idchip-off", timeout=8000)
                stays = True
            except Exception:  # noqa: BLE001
                stays = False
            links = [m["provider"] for m in api.get(
                "/api/games/" + quote(row["entry_key"], safe=""))["metadata_links"]]
            check(A, "a disabled provider keeps its chip, so it can be re-enabled", stays,
                  {"chip_off_shown": stays, "metadata_links_after_disable": links})
            if stays:
                chip = page.locator(".game-panel .idchip-meta.idchip-off").first
                with page.expect_response(lambda r: unquote(urlparse(r.url).path) == path):
                    chip.locator(".idchip-act").click()
                page.wait_for_function(
                    "() => !document.querySelector('.game-panel .idchip-off')", timeout=10000)
    except Exception as e:  # noqa: BLE001
        check(A, "provider toggle round trip", False, e)
    finally:
        after = api.get("/api/games/" + quote(row["entry_key"], safe=""))["disabled_identity"]
        if sorted(after) != sorted(before):
            how = "api"
            try:
                api.post(path, {"disabled": False})
            except Exception as e:  # noqa: BLE001
                print("  !!!!  identity restore failed: %s" % e)
            after = api.get("/api/games/" + quote(row["entry_key"], safe=""))["disabled_identity"]
        ok = sorted(after) == sorted(before)
        restore_row("identity %s on %s" % (prov, row["entry_key"]), before, [prov], after, ok, how)
        check(A, "the provider toggle is restored", ok, after)
        esc(page)


# ------------------------------------------------------------------ header + other tabs
def area_jobs(page, api):
    A = "jobs"
    at("jobs")
    jobs = api.get("/api/jobs")["jobs"]
    page.locator(".jm-expand").click()
    page.wait_for_selector(".job-overlay", timeout=10000)
    rows = page.locator(".job-overlay .job-trow").count()
    check(A, "the job monitor lists every /api/jobs job", rows == len(jobs),
          {"rows": rows, "api": len(jobs)})
    if not jobs:
        check(A, "an empty monitor says No jobs", page.locator(".job-overlay", has_text="No jobs").count())
    for j in jobs:
        if j.get("kind") in ("media", "match", "matchindex", "backup"):
            r = page.locator(".job-overlay .job-trow", has_text=j["label"][:30])
            check(A, "the %s job slot renders with its status" % j["kind"],
                  r.count() and j["status"] in r.first.inner_text(), j)
    shot(page, "jobs")
    esc(page)
    check(A, "Escape closes the job monitor", page.locator(".job-overlay").count() == 0)
    idle = page.locator(".jobmon-idle")
    if idle.count() and not jobs:
        check(A, "the header says there are no active jobs",
              "No active jobs" in idle.inner_text(), idle.inner_text())


def area_header_menus(page, api):
    A = "header"
    for title, cls, name in (("Sync library", ".sync-menu", "sync-menu"),
                             ("Server operations", ".ops-menu", "server-menu")):
        at("header/" + name)
        try:
            page.locator('button[title="%s"]' % title).click()
            page.wait_for_selector(cls, timeout=10000)
            settle(page, 1500)
            check(A, "%s opens" % title, page.locator(cls).count() == 1)
            shot(page, name)
            esc(page)
            check(A, "Escape closes %s" % title, page.locator(cls).count() == 0)
        except Exception as e:  # noqa: BLE001
            check(A, "%s menu" % title, False, e)
            close_everything(page)
    for t, name in (("Publish", "publish"), ("Files", "files")):
        if not page.locator(".main-tabs .pt-tab", has_text=t).count():
            continue
        at("tab/" + name)
        tab(page, t)
        settle(page, 2500)
        err = page.locator(".errbound")
        check(A, "the %s tab renders without a crash" % t, err.count() == 0,
              err.first.inner_text()[:200] if err.count() else "")
        no_overflow(A, page, t)
        shot(page, "tab-" + name)
    tab(page, "Dashboard")


def area_logout(page):
    A = "auth"
    at("auth/logout")
    with Armed(("POST", "/api/auth/logout", None)):
        page.locator("button.profile").click()
        page.locator(".profile-menu .pm-item", has_text="Sign out").click()
        page.wait_for_selector('input[type="password"]', timeout=15000)
    check(A, "Sign out returns to the sign-in form", True)
    sign_in(page)
    page.wait_for_selector(".pt-tab", timeout=45000)
    check(A, "and signing in again works", True)


# ------------------------------------------------------------------ driver
def run_viewport(browser, vp):
    STATE["vp"] = vp
    full = vp == "desktop"
    print("\n== viewport %s %s%s" % (vp, VIEWPORTS[vp], "" if full else " (subset)"), flush=True)
    ctx = browser.new_context(viewport=VIEWPORTS[vp], locale="en-US",
                              is_mobile=(vp == "phone"), has_touch=(vp == "phone"),
                              device_scale_factor=1)
    try:
        ctx.route("**/api/**", guard)
        page = ctx.new_page()
        page.set_default_timeout(20000)
        watch(page)
        api = Api(ctx)
        steps = []

        def step(name, fn, *a):
            try:
                return fn(*a)
            except Exception as e:  # noqa: BLE001
                check(name, "the %s step completed" % name, False,
                      "%s: %s" % (type(e).__name__, str(e).splitlines()[0][:220]))
                traceback.print_exc(limit=2)
                shot(page, "crash-" + name)
                try:
                    close_everything(page)
                except Exception:  # noqa: BLE001
                    load_app(page)
                steps.append(name)
                return None

        user = step("auth", area_auth, page, api)
        if not user:
            return
        owned = all_owned(api)
        stats = step("dashboard", area_dashboard, page, api)
        if full and stats:
            step("dashboard-cards", area_dashboard_cards, page, api, stats)
        step("library", area_library, page, api, owned, full)
        step("detail", area_details, page, api, owned, full)
        step("settings", area_settings, page, api, user, full)
        step("jobs", area_jobs, page, api)
        if full:
            step("header", area_header_menus, page, api)
            if WRITES:
                if user["role"] != "admin":
                    check("writes", "writes mode needs an admin login", False, user["role"])
                else:
                    step("writes", area_writes, page, api, owned)
            step("logout", area_logout, page)
    finally:
        ctx.close()       # our context only; the browser and its other tabs stay


def report():
    areas = {}
    for vp, area, _, ok, _ in RESULTS:
        a = areas.setdefault((vp, area), [0, 0])
        a[0 if ok else 1] += 1
    print("\n== summary (viewport / area: passed failed)")
    for (vp, area), (p, f) in sorted(areas.items()):
        print("  %-8s %-16s %4d %4d" % (vp, area, p, f))
    fails = [r for r in RESULTS if not r[3]]
    print("\n== failures (%d)" % len(fails))
    for vp, area, label, _, detail in fails:
        print("  [%s/%s] %s\n      %s" % (vp, area, label, detail))
    print("\n== aborted writes the guard caught (%d)" % len(FINDINGS["aborted"]))
    for a in FINDINGS["aborted"]:
        print("  %s %s %s at %s keys=%s" % (a["vp"], a["method"], a["path"], a["where"],
                                             a.get("body_keys")))
    print("\n== writes the guard let through because a step armed them (%d)" % len(ALLOWED_WRITES))
    for a in ALLOWED_WRITES:
        print("  %s %s %s body=%s" % (a["vp"], a["method"], a["path"], json.dumps(a["body"])[:120]))
    for k in ("console", "pageerror", "http", "reqfail", "covered", "overflow"):
        print("\n== %s (%d)" % (k, len(FINDINGS[k])))
        seen = set()
        for f in FINDINGS[k]:
            sig = json.dumps({x: y for x, y in f.items() if x != "where"}, sort_keys=True)
            if sig in seen:
                continue
            seen.add(sig)
            print("  " + json.dumps(f)[:400])
    print("\n== restores (%d)" % len(RESTORES))
    for r in RESTORES:
        print("  %s %-44s before=%r changed_to=%r after=%r via %s" % (
            "ok  " if r["restored"] else "FAIL", r["setting"], r["before"], r["changed_to"],
            r["after_restore"], r["how"]))
    os.makedirs(SHOTS, exist_ok=True)
    with open(os.path.join(SHOTS, "report.json"), "w") as fh:
        json.dump({"results": RESULTS, "findings": FINDINGS, "restores": RESTORES,
                   "allowed_writes": ALLOWED_WRITES}, fh, indent=1, default=str)
    bad_restore = [r for r in RESTORES if not r["restored"]]
    total = len(RESULTS)
    print("\nRESULT: %d checks, %d failed, %d restore failure(s)" % (total, len(fails),
                                                                   len(bad_restore)))
    return 1 if (fails or bad_restore) else 0


def main():
    print("ludodex full UI check against %s (writes %s)" % (URL, "ON" if WRITES else "off"))
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        # NEVER browser.close(): over CDP that would close a browser we do not own.
        for vp in WANT_VP:
            run_viewport(browser, vp)
    sys.exit(report())


if __name__ == "__main__":
    main()
