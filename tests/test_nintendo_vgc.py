#!/usr/bin/env python3
"""The Nintendo Virtual Game Card chain, exercised without a Nintendo account.

The integration itself is UNTESTED against a real account (2026-08-22). That is exactly
why this exists: the parsing, paging and mapping are the parts that can be wrong in ways
a live run would not obviously show, and they can all be proven offline against a fake
portal and a fake GraphQL endpoint.

What this CANNOT prove, and what the first live run is for:
  * that the portal really carries `data`/`meta`/`state` blobs in that shape
  * that `shopId = 3` is accepted from a browser session
  * how completely Virtual Game Cards cover an account's digital purchases

Fixtures are shaped from the field list the Playnite client requests, so if Nintendo
changes the response the fake and the real thing diverge and the live run fails loudly.
"""
import html
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-nintendo-")

import nintendo_owned as nin                     # noqa: E402

PASS = []


def check(label, cond):
    PASS.append(bool(cond))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def view(app, name, plat="NX", has_app=True, has_dlc=False, lending=False):
    return {
        "id": "vgc-" + app, "applicationId": app, "applicationName": name,
        "apparentPlatform": plat, "publisher": "Nintendo",
        "icon": {"url": "https://x/i.png", "upgradedIconUrl": None, "sizes": [128]},
        "ownerNaId": "owner", "userNaId": "user", "isHidden": False,
        "isLending": lending, "isPartialLending": False,
        "lendingExpireDatetime": None, "insertedNsDeviceId": None,
        "hasApplication": has_app, "hasAddOnContents": has_dlc, "hasUpgrade": False,
        "hasNxApplication": has_app and plat == "NX",
        "hasNxAddOnContents": has_dlc and plat == "NX",
        "hasOunceApplication": has_app and plat == "OUNCE",
        "hasOunceAddOnContents": has_dlc and plat == "OUNCE",
        "containsReleased": True,
    }


# 301 titles, so paging (limit 300) is genuinely exercised rather than assumed.
ALL = ([view("app%03d" % i, "Filler Game %d" % i) for i in range(297)]
       + [view("appNX1", "Super Mario Odyssey™"),
          view("appO1", "Mario Kart World", plat="OUNCE"),
          view("appDLC", "Some Expansion Pass", has_app=False, has_dlc=True),
          view("appLEND", "Lent Out Game", lending=True)])

STATE = {"lang": "en-US", "user": {"countryId": 77}}
META = {"countries": [{"id": 12, "code": "JP"}, {"id": 77, "code": "US"}]}
CALLS = {"graphql": 0, "offsets": [], "headers": []}


def portal_html(signed_in=True):
    data = ({"idToken": "ID-TOKEN", "savannaClientId": "SAVANNA-1",
             "shopGraphQLApiUrl": "http://127.0.0.1:%d/graphql" % PORT}
            if signed_in else {})
    def blob(eid, obj):
        return '<div id="%s" data-json="%s"></div>' % (eid, html.escape(json.dumps(obj)))
    return ("<html><body>" + blob("data", data) + blob("meta", META)
            + blob("state", STATE) + "</body></html>")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/portal/vgcs"):
            signed = "session=" in (self.headers.get("Cookie") or "")
            self._send(200, portal_html(signed), "text/html")
        else:
            self._send(404, "no", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        doc = json.loads(self.rfile.read(n) or b"{}")
        v = doc.get("variables") or {}
        CALLS["graphql"] += 1
        CALLS["offsets"].append(v.get("offset"))
        CALLS["headers"].append(self.headers.get("x-nintendo-savanna-client-id"))
        off, lim = int(v.get("offset") or 0), int(v.get("limit") or 300)
        page = ALL[off:off + lim]
        self._send(200, json.dumps({"data": {"account": {"vgc": {"vgcViews": {
            "offsetInfo": {"offset": off, "total": len(ALL)},
            "views": page}}}}}), "application/json")


PORT = 0


def main():
    global PORT
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    PORT = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    nin.PORTAL_URL = "http://127.0.0.1:%d/portal/vgcs/?sort=activated_date" % PORT

    print("1. the pasted credential is normalised, whatever shape it arrives in")
    check("raw header value", nin.extract_cookies("a=1; b=2") == "a=1; b=2")
    check("a Cookie: prefix is stripped",
          nin.extract_cookies("Cookie: a=1; b=2") == "a=1; b=2")
    check("devtools JSON becomes a header",
          nin.extract_cookies('[{"name":"a","value":"1"},{"name":"b","value":"2"}]')
          == "a=1; b=2")
    check("newlines from a copy/paste collapse",
          nin.extract_cookies("a=1;\nb=2\n") == "a=1; b=2")
    check("empty stays empty", nin.extract_cookies("  ") == "")

    print("2. a signed-out session is an ERROR, not an empty library")
    # The distinction the whole negative-cache lesson turns on: a miss must not read as
    # "you own nothing", which is what an empty list would say to build_library.
    try:
        nin.bootstrap("nope=1")
        check("signed-out raises", False)
    except RuntimeError as e:
        check("signed-out raises, naming the cause", "idToken" in str(e))

    print("3. the GraphQL endpoint is READ FROM THE PAGE, never hardcoded")
    params, _ = nin.bootstrap("session=abc")
    check("idToken scraped", params["idToken"] == "ID-TOKEN")
    check("savanna client id scraped", params["savannaClientId"] == "SAVANNA-1")
    check("endpoint scraped", params["shopGraphQLApiUrl"].endswith("/graphql"))
    check("country resolved via meta.countries[state.user.countryId]",
          params["countryCode"] == "US")
    check("language split from state.lang", params["languageCode"] == "en")
    check("nasLanguage keeps the full tag", params["nasLanguage"] == "en-US")
    check("shopId is the off-device shop", params["shopId"] == 3)

    print("4. the library reads back, paged")
    nin.save_cookies("session=abc")
    rows = nin.fetch_owned()
    by_app = {a: (t, p) for a, t, p in rows}
    check("paged in two requests over 301 views", CALLS["graphql"] == 2)
    check("offsets advanced by the page limit", CALLS["offsets"] == [0, 300])
    check("client-id header sent on every call",
          CALLS["headers"] == ["SAVANNA-1", "SAVANNA-1"])
    check("NX maps to switch", by_app["appNX1"][1] == "switch")
    check("OUNCE maps to switch2", by_app["appO1"][1] == "switch2")
    check("trademark symbols stripped from the title",
          by_app["appNX1"][0] == "Super Mario Odyssey")
    check("DLC-only entries are skipped by default", "appDLC" not in by_app)
    check("a lent-out card is STILL OWNED", "appLEND" in by_app)
    check("every other title survived", len(rows) == 300)

    print("5. add-ons can be asked for explicitly")
    CALLS["graphql"] = 0
    rows2 = nin.fetch_owned(include_addons=True)
    check("include_addons keeps the DLC row",
          any(a == "appDLC" for a, _, _ in rows2))
    check("and nothing else changed", len(rows2) == 301)

    print("6. mapping helpers stand alone")
    check("has* flags carry a platform the summary field omits",
          nin.platform_of({"apparentPlatform": "", "hasOunceApplication": True})
          == "switch2")
    check("an unknown platform yields None, not a guess",
          nin.platform_of({"apparentPlatform": "WIIU"}) is None)
    check("'full game' qualifier removed",
          nin.clean_title("Celeste full game") == "Celeste")

    srv.shutdown()
    print("test_nintendo_vgc: %d checks passed" % len(PASS))


if __name__ == "__main__":
    main()
