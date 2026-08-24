#!/usr/bin/env python3
"""The gate must price the call it is about to make, and the shipped defaults must
be priceable at all.

Four defects, all the same shape: a budget that cannot be measured, passing.

  _enforce measured the PAST. `unpriced` came from `_month_usage()` over the `usage`
  table, so a model with no rows yet this month reported `unpriced=False, cost=0` and
  sailed through. The stop only ever fired from the SECOND call onward — and with the
  parallel match pool, every worker passed the gate before any of them had recorded a
  single token. The price to test is the price of the model ABOUT TO BE CALLED.

  The shipped defaults were unpriceable. PROVIDERS defaults Anthropic to
  `claude-haiku-4-5-20251001` while DEFAULT_PRICES only listed `claude-haiku-4-5`, and
  price_get is an exact match; the whole `openrouter` provider had no price rows at all.
  So a dollar budget on a fresh install was "unmeasurable" out of the box — which, now
  that unmeasurable correctly means STOP, would have meant a install that refuses to
  work rather than one that overspends. Both are the same bug.

  price_get re-seeded the price table on EVERY call — sixteen INSERT OR IGNOREs — on a
  connection it then closed WITHOUT commit, so the work was rolled back and repeated
  forever, about ten fresh SQLite opens per AI call.

  list_models cached `ids = []` after an exception, so one transient API failure pinned
  the curated fallback list for the whole process lifetime.

Offline. No provider is reachable; the SDK entry points raise if touched unexpectedly.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = test_support.isolate("ludodex-aispend-gate-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

os.environ["ANTHROPIC_API_KEY"] = "test-anthropic-key"

from server import ai                                          # noqa: E402

PASS = []


def check(label, cond):
    PASS.append(bool(cond))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def raises(fn):
    try:
        fn()
    except RuntimeError as e:
        return str(e)
    return None


def main():
    print("a budget is measured against the call about to happen")

    # ---- the gate prices the PENDING model, on a month with no usage at all ----
    ai.set_limit("global", "all", {"usd": 20.0})
    con = sqlite3.connect(ai.USAGE_DB)
    con.execute("DELETE FROM usage")
    con.commit()
    con.close()

    msg = raises(lambda: ai.check_limit("gemini", "brand-new-unpriced-model"))
    check("the FIRST call to an unpriced model is stopped: %r" % (msg or "")[:60], msg)
    check("and says why, naming the fix: %r" % (msg or "")[:70],
          msg and "cannot be measured" in msg and "Set a price" in msg)
    check("naming the model it could not price",
          msg and "brand-new-unpriced-model" in msg)

    ai.price_set("gemini", "brand-new-unpriced-model", 0.30, 2.50, source="manual")
    check("once priced, the same call proceeds",
          ai.check_limit("gemini", "brand-new-unpriced-model") is None)

    # A priced model under a priced budget is never blocked.
    check("a priced model under the cap proceeds",
          ai.check_limit("gemini", "gemini-2.5-flash") is None)

    # And with no budget set, an unpriced model is free to run — "unmeasurable" is only
    # a problem when there is something to measure.
    ai.set_limit("global", "all", {"usd": 0})
    check("with no budget, an unpriced model still runs",
          ai.check_limit("gemini", "another-unpriced-model") is None)

    # A token cap does not need a price, so it must still bind on an unpriced model.
    ai.set_limit("global", "all", {"total": 100})
    con = ai._usage_con()
    con.execute("INSERT INTO usage(provider,model,day,calls,input_tokens,output_tokens)"
                " VALUES('gemini','another-unpriced-model',?,1,500,500)",
                (ai._month_prefix() + "-01",))
    con.commit()
    con.close()
    msg = raises(lambda: ai.check_limit("gemini", "another-unpriced-model"))
    check("a TOKEN cap still binds without any price: %r" % (msg or "")[:50],
          msg and "token cap" in msg)
    ai.set_limit("global", "all", {"total": 0})

    print()
    # ---- the shipped defaults can be measured --------------------------------
    for prov in ai.PROVIDERS:
        mdl = ai.PROVIDERS[prov][2]
        priced = ai.price_get(prov, mdl) is not None
        # An ALIAS is the one allowed exception: `gemini-flash-latest` points at a model
        # that keeps changing, so no honest table can hold its rate. It is priceable
        # instead — looks_like_alias is what routes it to the resolver and the pricing
        # dialog before any AI runs.
        check("the shipped default for %s (%s) is priced, or is a resolvable alias"
              % (prov, mdl), priced or ai.looks_like_alias(mdl))
    check("the Anthropic default's DATED name is priced, not just its family name",
          ai.price_get("anthropic", "claude-haiku-4-5-20251001") is not None)
    check("openrouter has price rows at all",
          any(p == "openrouter" for (p, _m) in ai.DEFAULT_PRICES))

    print()
    # ---- the seed is written once, and stays written -------------------------
    ai.price_get("gemini", "gemini-2.5-flash")
    raw = sqlite3.connect(ai.USAGE_DB)
    n = raw.execute("SELECT COUNT(*) FROM prices WHERE source='default'").fetchone()[0]
    raw.close()
    check("the shipped prices are COMMITTED, not rolled back every time: %d rows" % n,
          n == len(ai.DEFAULT_PRICES))
    check("and the seed is not replayed on every lookup",
          ai.USAGE_DB in ai._SEEDED)

    print()
    # ---- an explicit provider choice, and no dead branch ---------------------
    src = open(os.path.join(DIR, "server", "ai.py"), encoding="utf-8").read()
    body = src[src.index("def active_provider("):]
    body = body[:body.index("\ndef ", 10)]
    check("the explicit-choice branch is not written twice, the second unreachable",
          "if p in PROVIDERS and key_for(p):" not in body)
    os.environ["AI_PROVIDER"] = "anthropic"
    check("an explicit choice with a key is used",
          ai.active_provider() == "anthropic")
    os.environ["AI_PROVIDER"] = "openrouter"
    check("an explicit choice with NO key is None, never a silent substitution — "
          "billing a provider the user did not pick is the failure",
          ai.active_provider() is None)
    os.environ.pop("AI_PROVIDER")

    print()
    # ---- one API failure must not pin the fallback list ----------------------
    import anthropic
    real_cls = anthropic.Anthropic
    ai._MODELS_CACHE.clear()

    class Boom:
        def __init__(self, **kw):
            raise RuntimeError("503 service unavailable")

    class Fake:
        class models:
            @staticmethod
            def list(limit=None):
                return type("R", (), {"data": [type("M", (), {"id": "claude-new-9"})]})

        def __init__(self, **kw):
            pass

    anthropic.Anthropic = Boom
    try:
        got = ai.list_models("anthropic")
        check("a failed lookup still answers with the curated list",
              got == sorted(ai.MODELS["anthropic"]))
        check("but does NOT cache that failure for the process lifetime",
              "anthropic" not in ai._MODELS_CACHE)
        anthropic.Anthropic = Fake
        got = ai.list_models("anthropic")
        check("so the next attempt sees the real list: %r" % (got,),
              "claude-new-9" in got)
        check("and a successful list IS cached", "anthropic" in ai._MODELS_CACHE)
    finally:
        anthropic.Anthropic = real_cls

    print("\nRESULT: %d checks, all passed" % len(PASS))


main()
