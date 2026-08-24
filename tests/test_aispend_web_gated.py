#!/usr/bin/env python3
"""Every paid web-search call passes the gate and lands in the ledger (#15, #15b).

The project's first rule is that paid AI never fires by accident, and a budget the code
cannot MEASURE stops the spend. Four calls in this file were outside both halves of it.

  web_scores, find_media_pages, find_media_urls
      each reached the provider directly — `res = _retry(lambda: fn(key, model, ...))` —
      with no check_limit() before and no record_usage() after. A caller in app.py even
      documents web_scores as "gated by the AI spend caps"; it was not. Anthropic web
      search is billed PER SEARCH on top of tokens, so this was the most expensive call
      shape in the file and the only one nothing counted.

  detect_model_version
      fires a real Gemini generateContent — the docstring says so, deliberately — from
      suggest_price, for any model name containing "latest" or "preview". Also outside
      both. And it put the API key in the URL QUERY STRING, where proxies, access logs
      and urllib's own error text capture it.

The same three helpers also picked their transport with
`fn = _web_gemini if provider == "gemini" else _web_anthropic`, while OpenAI is declared
web-capable in WEB_PROVIDERS. With OpenAI active they called _web_anthropic with an
OpenAI key, it raised, the bare `except` swallowed it, and web scores and media pages
silently returned nothing. `_complete_text_web` had always handled OpenAI correctly, so
this was local drift between two copies of one dispatch.

Offline. Every provider entry point is replaced by a recorder; a real call raises.
"""
import io
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = test_support.isolate("ludodex-aispend-web-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

os.environ["GEMINI_API_KEY"] = "test-gemini-key"
os.environ["ANTHROPIC_API_KEY"] = "test-anthropic-key"
os.environ["OPENAI_API_KEY"] = "test-openai-key"
os.environ["AI_PROVIDER"] = "gemini"

from server import ai                                          # noqa: E402

PASS = []


def check(label, cond):
    PASS.append(bool(cond))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


EVENTS = []          # an ordered trace of gate / provider / ledger, per call

SCORES_JSON = '{"critic": 88, "user": 91}'
URLS_JSON = ('{"cover": "https://example.invalid/a.jpg", '
             '"screenshots": ["https://example.invalid/b.png"]}')
SOURCES = [{"title": "MobyGames", "url": "https://example.invalid/moby"}]


def recorder(name, text):
    def fn(key, model, system, user, *a, **k):
        EVENTS.append(("call", name, model))
        return text, 1234, 567, list(SOURCES)
    return fn


def openai_recorder(text):
    # _web_openai takes (key, model, user, base_url=None) — no separate system.
    def fn(key, model, user, base_url=None):
        EVENTS.append(("call", "openai", model))
        return text, 1234, 567, list(SOURCES)
    return fn


def install(text):
    EVENTS[:] = []
    ai._web_gemini = recorder("gemini", text)
    ai._web_anthropic = recorder("anthropic", text)
    ai._web_openai = openai_recorder(text)


real_check, real_record = ai.check_limit, ai.record_usage


def gate_ok(provider, model):
    EVENTS.append(("gate", provider, model))


def gate_stop(provider, model):
    EVENTS.append(("gate", provider, model))
    raise RuntimeError("monthly budget reached for the global AI budget")


def ledger(provider, model, i, o):
    EVENTS.append(("ledger", provider, model, i, o))


def kinds():
    return [e[0] for e in EVENTS]


def gated(label, fn, text, expect_truthy=True):
    """One paid web helper: gate first, provider once, ledger after, in that order."""
    install(text)
    ai.check_limit, ai.record_usage = gate_ok, ledger
    try:
        got = fn()
    finally:
        ai.check_limit, ai.record_usage = real_check, real_record
    check("%s produced a result: %r" % (label, got), bool(got) == expect_truthy)
    check("%s consults the budget BEFORE the provider: %r" % (label, kinds()),
          kinds()[:2] == ["gate", "call"])
    check("%s records the billed tokens after: %r" % (label, EVENTS[-1:]),
          EVENTS[-1][0] == "ledger" and EVENTS[-1][3] == 1234 and EVENTS[-1][4] == 567)
    check("%s makes exactly one provider call" % label,
          kinds().count("call") == 1)


def stopped(label, fn):
    """A cap must stop the call, not merely be consulted after it."""
    install(SCORES_JSON)
    ai.check_limit, ai.record_usage = gate_stop, ledger
    try:
        fn()
    except RuntimeError:
        pass
    finally:
        ai.check_limit, ai.record_usage = real_check, real_record
    check("%s under a hit cap never reaches the provider: %r" % (label, kinds()),
          "call" not in kinds() and "ledger" not in kinds())


def main():
    print("no paid web call escapes the gate or the ledger")

    # ---- #15: the three helpers that reached the provider directly ------------
    gated("web_scores", lambda: ai.web_scores("Chrono Trigger"), SCORES_JSON)
    gated("find_media_pages", lambda: ai.find_media_pages("Chrono Trigger"),
          "The cover is on MobyGames.")
    gated("find_media_urls", lambda: ai.find_media_urls("Chrono Trigger"), URLS_JSON)
    # _complete_text_web already gated; it must keep doing so through the shared path.
    gated("_complete_text_web",
          lambda: ai._complete_text_web("gemini", "k", "m", "sys", "user")[0],
          "grounded prose")

    stopped("web_scores", lambda: ai.web_scores("Chrono Trigger"))
    stopped("find_media_pages", lambda: ai.find_media_pages("Chrono Trigger"))
    stopped("find_media_urls", lambda: ai.find_media_urls("Chrono Trigger"))

    # ---- the OpenAI drift ------------------------------------------------------
    # OpenAI is in WEB_PROVIDERS, so these helpers must not fall through to Anthropic's
    # transport with an OpenAI key.
    os.environ["AI_PROVIDER"] = "openai"
    try:
        install(SCORES_JSON)
        ai.check_limit, ai.record_usage = gate_ok, ledger
        try:
            got = ai.web_scores("Chrono Trigger")
        finally:
            ai.check_limit, ai.record_usage = real_check, real_record
        check("with OpenAI active web_scores uses OpenAI's transport: %r" % (kinds(),),
              ("call", "openai", ai.model_for("openai")) in EVENTS)
        check("and therefore returns a score instead of silently nothing: %r" % (got,),
              got.get("critic") == 88)
    finally:
        os.environ["AI_PROVIDER"] = "gemini"

    # An unsupported provider is refused up front, not mis-dispatched.
    check("openrouter is still declared to have no web search",
          not ai.supports_web("openrouter"))

    # ---- #15b: detect_model_version ------------------------------------------
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        EVENTS.append(("call", "gemini-http", None))
        return io.BytesIO(json.dumps({
            "modelVersion": "gemini-3.7-flash",
            "candidates": [{"content": {"parts": [{"text": "hi"}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1},
        }).encode())

    urllib.request.urlopen = fake_urlopen
    EVENTS[:] = []
    ai.check_limit, ai.record_usage = gate_ok, ledger
    try:
        got = ai.detect_model_version("gemini", "gemini-flash-latest")
    finally:
        ai.check_limit, ai.record_usage = real_check, real_record
    check("the alias probe still resolves the real model: %r" % got,
          got == "gemini-3.7-flash")
    check("the probe is a PAID call and is gated first: %r" % (kinds(),),
          kinds()[:2] == ["gate", "call"])
    check("and its tokens are recorded: %r" % (EVENTS[-1:],),
          EVENTS[-1][0] == "ledger" and EVENTS[-1][3] == 3 and EVENTS[-1][4] == 1)
    check("the API key is NOT in the URL: %r" % seen["url"],
          "test-gemini-key" not in seen["url"] and "key=" not in seen["url"])
    check("it travels in a header instead: %r" % list(seen["headers"]),
          seen["headers"].get("x-goog-api-key") == "test-gemini-key")

    # A cap that is already hit must stop the probe before it spends.
    EVENTS[:] = []
    ai.check_limit, ai.record_usage = gate_stop, ledger
    try:
        got = ai.detect_model_version("gemini", "gemini-flash-latest")
    finally:
        ai.check_limit, ai.record_usage = real_check, real_record
    check("a hit cap stops the probe and it reports nothing: %r" % got, got is None)
    check("without reaching the provider: %r" % (kinds(),), "call" not in kinds())

    print("\nRESULT: %d checks, all passed" % len(PASS))


main()
