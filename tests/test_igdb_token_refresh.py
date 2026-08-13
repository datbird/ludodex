#!/usr/bin/env python3
"""An unexpired token that the server rejects must be re-minted, not trusted.

The OAuth token is cached with the TTL Twitch reported and reused until that clock
runs out — 60 days. Nothing ever asked whether it still WORKS. When IGDB started
answering 401 on 2026-08-09 the cached row had 5,187,755 seconds left, a fresh mint
succeeded immediately, and every IGDB call in between failed: a full identity
re-resolve died on its first request and would have kept dying for two months, because
the only thing that could have cleared the row was the expiry it had not reached.

A token can be invalidated server-side before it expires. So expiry is a hint, and the
401 is the authority: on one, drop the cached token, mint a fresh one, and retry the
request once. Once — a second 401 with a brand-new token is a real credentials
problem and must surface, not spin.
"""
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-igdbtok-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    import igdb

    calls = {"n": 0, "tokens": []}

    def fake_open(req, timeout=None):
        calls["n"] += 1
        calls["tokens"].append(req.headers.get("Authorization"))
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'[{"id": 1}]'
        return R()

    real_open, real_tok, real_throttle = (
        igdb.urllib.request.urlopen, igdb.get_token, igdb._throttle)
    igdb.urllib.request.urlopen = fake_open
    igdb.get_token = lambda cid, csec: ("FRESH", 5000000)
    igdb._throttle = lambda: None
    minted = {"n": 0}

    def on_reauth(cid):
        minted["n"] += 1
        return "FRESH"

    try:
        out = igdb.query("games", "fields id;", "cid", "STALE", reauth=on_reauth)
        check("the request succeeds after re-minting", out == [{"id": 1}])
        check("it retried exactly once", calls["n"] == 2)
        check("the retry carried the FRESH token",
              calls["tokens"][1] == "Bearer FRESH")
        check("the caller was told to re-mint", minted["n"] == 1)

        # a 401 with no way to re-mint must still surface rather than spin
        calls["n"] = 0
        try:
            igdb.query("games", "fields id;", "cid", "STALE")
            check("a 401 without a reauth hook raises", False)
        except urllib.error.HTTPError as e:
            check("a 401 without a reauth hook raises", e.code == 401)
    finally:
        igdb.urllib.request.urlopen = real_open
        igdb.get_token = real_tok
        igdb._throttle = real_throttle

    print("\n  %d/%d passed" % (sum(1 for _, c in PASS if c), len(PASS)))


if __name__ == "__main__":
    main()
