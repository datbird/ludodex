#!/usr/bin/env python3
"""EA's auth path must not hand the remid cookie to whoever the redirect names.

`_get` used a plain default opener, and urllib's HTTPRedirectHandler copies EVERY header
except Content-Length/Content-Type onto the redirect target — Cookie included. The remid
cookie is the DURABLE EA credential: it is what mints access tokens non-interactively,
and losing it means a browser re-login. accounts.ea.com sits behind Akamai Bot Manager,
which is precisely the thing that redirects a request it does not like, so the one place
this bites is the one place it always happens.

The same Akamai shield returns an HTML challenge page with a 200, and `json.load(r)` then
raised a bare JSONDecodeError from inside `_get` — before the friendly "your EA login
likely expired, re-run --login" message a few lines below could ever be reached. The user
saw a stack trace about column 1 char 0 and had no idea what to do.

Two smaller ones from the same audit: the pasted token's own `expires_in` was thrown away
and `save_token` hardcoded a four-hour TTL, so a token EA said was good for one hour was
trusted for four (every call in between failing); and `token(cookies, force=...)` had no
caller at all, while `gql` open-coded the same refresh.

Offline. Every request is stubbed; no cookie or token value is ever printed.
"""
import json
import os
import sys
import time
import urllib.request

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-ea-auth-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import ea_owned                                                # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def headers_of(req):
    return {k.lower() for k in (req.headers or {})} | \
           {k.lower() for k in (req.unredirected_hdrs or {})}


class Body:
    """A urlopen result carrying a fixed payload."""

    def __init__(self, raw):
        self.raw = raw if isinstance(raw, bytes) else raw.encode()

    def read(self, *a):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main():
    print("EA's auth path keeps the remid cookie to itself")

    # ---- a cross-host redirect must not carry the credential -------------------- #
    handler = None
    for h in ea_owned.OPENER.handlers:
        if isinstance(h, urllib.request.HTTPRedirectHandler):
            handler = h
    check("the EA opener installs its own redirect handler",
          handler is not None and type(handler) is not urllib.request.HTTPRedirectHandler)

    req = urllib.request.Request("https://accounts.ea.com/connect/auth",
                                 headers={"Cookie": "remid=durable-credential",
                                          "User-Agent": "x"})
    moved = handler.redirect_request(req, None, 302, "Found", {},
                                     "https://challenge.akamai.invalid/blocked")
    check("a redirect to another host drops the cookie",
          "cookie" not in headers_of(moved))
    check("and keeps the harmless headers", "user-agent" in headers_of(moved))

    same = handler.redirect_request(req, None, 302, "Found", {},
                                    "https://accounts.ea.com/connect/auth2")
    check("a redirect within accounts.ea.com still carries the session",
          "cookie" in headers_of(same))

    # ---- an HTML challenge page is an actionable message, not a traceback -------- #
    real_open = ea_owned.OPENER.open
    ea_owned.OPENER.open = lambda *a, **k: Body(
        "<html><head><title>Access Denied</title></head><body>…</body></html>")
    try:
        raised = None
        try:
            ea_owned.fetch_token({"remid": "x"})
        except BaseException as e:                             # noqa: BLE001
            raised = e
    finally:
        ea_owned.OPENER.open = real_open
    check("a non-JSON reply raises something with a message, not a JSONDecodeError",
          raised is not None and not isinstance(raised, json.JSONDecodeError))
    msg = str(raised)
    check("the message says EA did not answer with JSON", "json" in msg.lower())
    check("and tells the user what to do about it",
          "--login" in msg or "--token" in msg)
    check("without echoing the cookie back at them", "x" != msg and "remid=" not in msg)

    # ---- the pasted token's own lifetime is honoured ---------------------------- #
    ea_owned.save_token("tok", ttl=3600)
    exp = json.load(open(ea_owned.TOKEN))["expires_at"]
    check("an explicit ttl is respected (not the hardcoded 4 hours)",
          3000 < exp - int(time.time()) < 3600)

    # parse-only: calling main() here would go on to pull the library over the network.
    tok, ttl = ea_owned.parse_pasted_token(
        json.dumps({"access_token": "tok2", "expires_in": 900}))
    check("a pasted token's own expires_in is used", (tok, ttl) == ("tok2", 900))
    check("a bare pasted value still falls back to EA's usual lifetime",
          ea_owned.parse_pasted_token("  raw-token ") == ("raw-token", 14400))
    ea_owned.save_token(tok, ttl=ttl)
    saved = json.load(open(ea_owned.TOKEN))
    check("so a 15-minute token is not trusted for four hours",
          saved["access_token"] == "tok2"
          and saved["expires_at"] - int(time.time()) < 900)

    # ---- `force` has a caller ---------------------------------------------------- #
    src = open(os.path.join(DIR, "ludodex", "ea_owned.py"), encoding="utf-8").read()
    check("gql refreshes through token(force=True) instead of open-coding it",
          "token(cookies, force=True)" in src)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
