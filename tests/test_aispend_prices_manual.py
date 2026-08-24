#!/usr/bin/env python3
"""A price the user set by hand must survive the daily refresh (#16).

`prices_refresh` promises, in its own docstring, that it "NEVER overwrites source='manual'
rows". It did, every time. The guard was built like this:

    seen = set(con.execute("SELECT provider, model FROM prices WHERE source='manual'"))
    ...
    if (prov, model) in seen:            # never clobber a manual override
        continue

`_usage_con()` sets `row_factory = sqlite3.Row`, so `seen` is a set of Row OBJECTS, and a
Row never compares equal to a tuple. The membership test was therefore False for every
row that had ever been set by hand, and the INSERT below it carries
`ON CONFLICT ... DO UPDATE ... source='openrouter'`.

That matters because of what runs it: `run_daily_price_update` fires unattended, and its
only reason to fire is that limits exist. So the rate a user typed to make their budget
measurable was replaced overnight by a rate from a router they may not even use, and the
dollar cap then measured spend against a different number than the one they set.

Offline. The OpenRouter fetch is fed a canned catalog; nothing reaches the network.
"""
import io
import json
import os
import sqlite3
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = test_support.isolate("ludodex-aispend-prices-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import ai                                          # noqa: E402

PASS = []


def check(label, cond):
    PASS.append(bool(cond))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


CATALOG = {"data": [
    {"id": "google/gemini-2.5-flash", "pricing": {"prompt": "0.0000003",
                                                  "completion": "0.0000025"}},
    {"id": "anthropic/hand-set-model", "pricing": {"prompt": "0.000009",
                                                   "completion": "0.000045"}},
    {"id": "anthropic/fetched-model", "pricing": {"prompt": "0.000001",
                                                  "completion": "0.000005"}},
]}


def fake_urlopen(req, timeout=None):
    """The OpenRouter models feed, canned. A test of the spend guardrail that reached a
    network would be its own counterexample."""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if "openrouter.ai" not in url:
        raise AssertionError("unexpected network call: %s" % url)
    return io.BytesIO(json.dumps(CATALOG).encode())


def row(provider, model):
    con = sqlite3.connect(ai.USAGE_DB)
    con.row_factory = sqlite3.Row
    try:
        r = con.execute("SELECT in_usd, out_usd, source FROM prices WHERE provider=? "
                        "AND model=?", (provider, model)).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def main():
    print("a manual price survives the unattended refresh")

    urllib.request.urlopen = fake_urlopen

    # A rate the user typed, for a model that ALSO appears in the feed and in the usage
    # table — the exact overlap the guard exists for.
    ai.price_set("anthropic", "hand-set-model", 3.00, 15.00, source="manual")
    ai.price_set("anthropic", "fetched-model", 9.99, 99.99, source="openrouter")
    con = ai._usage_con()
    con.execute("INSERT INTO usage(provider,model,day,calls,input_tokens,output_tokens) "
                "VALUES('anthropic','hand-set-model','2026-08-01',1,10,10)")
    con.execute("INSERT INTO usage(provider,model,day,calls,input_tokens,output_tokens) "
                "VALUES('anthropic','fetched-model','2026-08-01',1,10,10)")
    con.commit()
    con.close()

    out = ai.prices_refresh()
    check("the refresh ran against the canned catalog: %r" % (out,),
          isinstance(out, dict) and out.get("checked") == 3)

    hand = row("anthropic", "hand-set-model")
    check("the hand-set rate is untouched: %r" % (hand,),
          hand and hand["in_usd"] == 3.00 and hand["out_usd"] == 15.00)
    check("and is still labelled manual, so the next refresh skips it too",
          hand and hand["source"] == "manual")

    got = row("anthropic", "fetched-model")
    check("a fetched row IS refreshed — the guard must not block everything: %r" % (got,),
          got and got["in_usd"] == 1.0 and got["out_usd"] == 5.0
          and got["source"] == "openrouter")

    # The guard, in isolation: whatever the query returns must answer a tuple test.
    con = ai._usage_con()
    seen = ai._manual_prices(con)
    con.close()
    check("the manual set answers a plain (provider, model) tuple",
          ("anthropic", "hand-set-model") in seen)
    check("and does not claim rows it never held",
          ("anthropic", "fetched-model") not in seen)

    print("\nRESULT: %d checks, all passed" % len(PASS))


main()
