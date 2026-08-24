#!/usr/bin/env python3
"""Server-side fetches of a URL somebody else chose (#13).

Five places fetched a URL supplied by the user or proposed by an LLM, with no check on
where it pointed: `add_media_from_url`, `_fetch_ref_text`, `_fetch_img` (open-web media
discovery), and the two match-index calls. The word `ipaddress` did not appear in app.py.
A self-hosted ludodex sits inside a home LAN, so "fetch this URL" was a request the server
would happily make against 127.0.0.1:8090, the router, or a metadata endpoint at
169.254.169.254 — and hand the result back through the UI.

The match-index pair was worse than an SSRF: `_release_headers()` attached the stored
GitHub release token as `Authorization: Bearer …` to WHATEVER HOST was typed into the
Check/Download box, which posts the credential to a stranger's server.

The rule now: resolve the hostname, refuse loopback / private / link-local / reserved /
multicast / unspecified addresses, refuse a redirect that lands on one, and never send the
GitHub token anywhere but GitHub.

Offline: every address in this test is a literal, so `getaddrinfo` answers without asking
a nameserver, and nothing is fetched — the helper's verdict is the assertion. The redirect
refusal is checked on the handler directly for the same reason.
"""
import os
import sys
import urllib.request

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-api-url-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


REFUSE = [
    ("http://127.0.0.1:8090/api/collections", "the loopback address"),
    ("http://localhost:8000/x.png", "localhost by name"),
    ("http://[::1]:8000/x.png", "IPv6 loopback"),
    ("http://10.1.2.3/roms", "a private 10/8 host"),
    ("http://172.16.0.1/x", "a private 172.16/12 host"),
    ("http://192.168.1.1/", "the usual router address"),
    ("http://169.254.169.254/latest/meta-data/", "the link-local metadata address"),
    ("http://0.0.0.0/", "the unspecified address"),
    ("http://224.0.0.1/", "a multicast address"),
    ("file:///etc/passwd", "a file:// URL"),
    ("gopher://10.0.0.1:70/", "a non-http scheme"),
    ("http:///nohost", "a URL with no host"),
    ("", "an empty URL"),
]

ALLOW = [
    "http://8.8.8.8/x.png",
    "https://93.184.216.34/manifest.json",
]


def refused(url):
    try:
        app._public_url_or_refuse(url)
        return False
    except Exception:                                          # noqa: BLE001
        return True


def main():
    print("the server only fetches public addresses, and only tells GitHub its token")

    for url, why in REFUSE:
        check("refuses %s" % why, refused(url))
    for url in ALLOW:
        check("still fetches the public %s" % url, not refused(url))

    # a redirect INTO the LAN is the same attack with one extra hop
    h = app._SafeRedirectHandler()
    req = urllib.request.Request("https://8.8.8.8/a")

    class FakeResp:
        headers = {}

        def read(self):
            return b""

    raised = None
    try:
        h.redirect_request(req, FakeResp(), 302, "Found", {},
                           "http://192.168.1.10/admin")
    except Exception as e:                                     # noqa: BLE001
        raised = e
    check("a redirect into the LAN is refused", raised is not None)

    ok = h.redirect_request(req, FakeResp(), 302, "Found", {}, "https://8.8.4.4/b")
    check("a redirect to a public host is followed", ok is not None)

    # ---- the GitHub release token ---------------------------------------------- #
    saved = app.config.get
    app.config.get = lambda k, d="": ("ghp_secrettoken"
                                      if k == app.RELEASE_TOKEN_KEY else saved(k, d))
    try:
        for host in ("https://api.github.com/repos/x/releases/latest",
                     "https://github.com/x/y", "https://objects.githubusercontent.com/z"):
            check("the token goes to %s" % host,
                  "Authorization" in app._release_headers(host))
        for host in ("http://192.168.1.9/manifest.json", "https://evil.example/r",
                     "https://github.com.attacker.test/x"):
            check("the token is NOT sent to %s" % host,
                  "Authorization" not in app._release_headers(host))
    finally:
        app.config.get = saved

    # ---- every fetch site goes through the check ------------------------------- #
    src = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read()
    for fn in ("_fetch_ref_text", "add_media_from_url", "matchindex_release",
               "matchindex_download", "_fetch_media_web"):
        body = src.split("def %s(" % fn, 1)[1].split("\n@app.", 1)[0].split("\ndef ", 1)[0]
        check("%s fetches through the guarded opener" % fn,
              "_safe_urlopen(" in body)
        check("%s no longer calls urlopen directly" % fn,
              "urllib.request.urlopen(" not in body)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
