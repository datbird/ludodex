#!/usr/bin/env python3
"""A truncated answer is a failure, not a partial success (#18).

`max_tokens=400` was hardcoded in the Anthropic and OpenAI paths while Gemini used 2048,
so the same request succeeded on one provider and came back two thirds missing on
another. ingest_ai sends BATCH = 60 paths per call and its own estimator budgets about
1,320 output tokens for the reply; identify_roms, detect_collections (which lists a
compilation's members), adjudicate_entry and analyze_game (a full attribute object plus
its sources) all overran 400 as a matter of course.

Nothing noticed, because `_json` REPAIRS a truncated reply: it closes the open braces and
returns whatever survived. So roughly two thirds of every batch was silently dropped, at
full price — the call is billed for the tokens it generated whether or not the answer is
usable. That is the worst possible outcome for a file whose first rule is that spend must
be measurable and deliberate.

Three things have to be true, and this pins all three:

  * the cap is a PARAMETER the caller sizes, with a default that is not 400,
  * the providers agree on it, and
  * hitting it RAISES, so a partial batch can never be mistaken for a complete one.

Plus two provider-specific traps in the same code:

  * a `gpt-5*` model on chat completions REJECTS `max_tokens` and wants
    `max_completion_tokens` — and PROVIDERS defaults OpenAI to gpt-5-mini, so the
    hardcoded parameter name was a 400 on the shipped configuration.
  * `_call_openai` always forced `response_format={"type":"json_object"}`, but
    title_aliases asks for "ONLY a JSON array" and then requires `isinstance(out, list)`.
    On OpenAI it therefore returned [] every time, and the alias rescue — the thing that
    saves a match when an exact title lookup fails — silently never fired.

Offline. Every provider SDK entry point is replaced; a real network call cannot happen.
"""
import os
import sys
import types as _pytypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = test_support.isolate("ludodex-aispend-cap-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

os.environ["ANTHROPIC_API_KEY"] = "test-anthropic-key"
os.environ["OPENAI_API_KEY"] = "test-openai-key"
os.environ["GEMINI_API_KEY"] = "test-gemini-key"

from server import ai                                          # noqa: E402

PASS = []
SEEN = {}


def check(label, cond):
    PASS.append(bool(cond))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def ns(**kw):
    return _pytypes.SimpleNamespace(**kw)


# ----------------------------------------------------------------- fake providers
class FakeAnthropic:
    def __init__(self, api_key=None, **kw):
        self.messages = self

    def create(self, **kw):
        SEEN.clear()
        SEEN.update(kw)
        return ns(content=[ns(type="text", text=SEEN.get("_text", '{"ok":1}'))],
                  usage=ns(input_tokens=900, output_tokens=400),
                  stop_reason=STOP["anthropic"])


class FakeOpenAI:
    def __init__(self, api_key=None, base_url=None, **kw):
        self.chat = ns(completions=self)

    def create(self, **kw):
        SEEN.clear()
        SEEN.update(kw)
        return ns(choices=[ns(message=ns(content=TEXT["openai"]),
                              finish_reason=STOP["openai"])],
                  usage=ns(prompt_tokens=900, completion_tokens=400))


class FakeGenaiClient:
    def __init__(self, api_key=None, http_options=None, **kw):
        self.models = self

    def generate_content(self, model=None, contents=None, config=None):
        SEEN.clear()
        SEEN["model"] = model
        SEEN["max_output_tokens"] = getattr(config, "max_output_tokens", None)
        return ns(text='{"ok":1}',
                  usage_metadata=ns(prompt_token_count=900, candidates_token_count=400),
                  candidates=[ns(finish_reason=ns(name=STOP["gemini"]))])


STOP = {"anthropic": "end_turn", "openai": "stop", "gemini": "STOP"}
TEXT = {"openai": '{"ok":1}'}


def install():
    import anthropic
    import openai
    from google import genai
    anthropic.Anthropic = FakeAnthropic
    openai.OpenAI = FakeOpenAI
    genai.Client = FakeGenaiClient


def truncation_raises(label, call):
    try:
        call()
    except ai.TruncatedResponse as e:
        check("%s: hitting the cap RAISES rather than returning a partial answer: %r"
              % (label, str(e)[:60]), True)
        check("%s: and the error names the cap that was hit" % label,
              str(ai.DEFAULT_MAX_OUT) in str(e))
        return
    check("%s: hitting the cap RAISES rather than returning a partial answer" % label,
          False)


def main():
    print("the output cap is sized by the caller, and overrunning it is an error")
    install()

    check("the default cap is no longer 400", ai.DEFAULT_MAX_OUT != 400)
    check("and it covers ingest_ai's ~1,320-token batch reply with headroom: %d"
          % ai.DEFAULT_MAX_OUT, ai.DEFAULT_MAX_OUT >= 2048)

    # ---- one cap, the same on every provider ---------------------------------
    ai._call_anthropic("k", "claude-haiku-4-5", "sys", "user")
    anth_cap = SEEN.get("max_tokens")
    check("anthropic sends the shared default: %r" % anth_cap,
          anth_cap == ai.DEFAULT_MAX_OUT)

    ai._call_openai("k", "gpt-4o-mini", "sys", "user")
    oai_cap = SEEN.get("max_tokens")
    check("openai sends the same: %r" % oai_cap, oai_cap == ai.DEFAULT_MAX_OUT)

    ai._call_gemini("k", "gemini-2.5-flash", "sys", "user")
    gem_cap = SEEN.get("max_output_tokens")
    check("gemini sends the same: %r" % gem_cap, gem_cap == ai.DEFAULT_MAX_OUT)
    check("so a batch that fits one provider fits them all",
          anth_cap == oai_cap == gem_cap)

    # ---- and the caller can size it ------------------------------------------
    ai._call_anthropic("k", "claude-haiku-4-5", "sys", "user", max_out=6000)
    check("anthropic honours an explicit cap: %r" % SEEN.get("max_tokens"),
          SEEN.get("max_tokens") == 6000)
    ai._call_openai("k", "gpt-4o-mini", "sys", "user", max_out=6000)
    check("openai honours an explicit cap: %r" % SEEN.get("max_tokens"),
          SEEN.get("max_tokens") == 6000)
    ai._call_gemini("k", "gemini-2.5-flash", "sys", "user", max_out=6000)
    check("gemini honours an explicit cap: %r" % SEEN.get("max_output_tokens"),
          SEEN.get("max_output_tokens") == 6000)
    ai._complete_text("gemini", "k", "gemini-2.5-flash", "sys", "user", max_out=4096)
    check("_complete_text passes the caller's size through: %r"
          % SEEN.get("max_output_tokens"), SEEN.get("max_output_tokens") == 4096)

    print()
    # ---- overrunning it is an error, on every provider ------------------------
    STOP["anthropic"] = "max_tokens"
    truncation_raises("anthropic",
                      lambda: ai._call_anthropic("k", "claude-haiku-4-5", "s", "u"))
    STOP["anthropic"] = "end_turn"

    STOP["openai"] = "length"
    truncation_raises("openai", lambda: ai._call_openai("k", "gpt-4o-mini", "s", "u"))
    STOP["openai"] = "stop"

    STOP["gemini"] = "MAX_TOKENS"
    truncation_raises("gemini",
                      lambda: ai._call_gemini("k", "gemini-2.5-flash", "s", "u"))
    STOP["gemini"] = "STOP"

    # A cut-off call was still billed in full, so it must still reach the ledger — and
    # it must NOT be retried: the same request at the same cap is cut off again.
    STOP["anthropic"] = "max_tokens"
    billed, real_record = [], ai.record_usage
    ai.record_usage = lambda p, m, i, o: billed.append((p, m, i, o))
    calls = {"n": 0}
    real_call = ai._call_anthropic

    def counting(*a, **k):
        calls["n"] += 1
        return real_call(*a, **k)
    ai._call_anthropic = counting
    try:
        try:
            ai._complete_text("anthropic", "k", "claude-haiku-4-5", "s", "u")
        except ai.TruncatedResponse:
            pass
        check("a truncated call is still counted — it was billed in full: %r" % (billed,),
              billed == [("anthropic", "claude-haiku-4-5", 900, 400)])
        check("and it is not retried: %d attempt" % calls["n"], calls["n"] == 1)
    finally:
        ai.record_usage, ai._call_anthropic = real_record, real_call
        STOP["anthropic"] = "end_turn"

    print()
    # ---- the gpt-5 parameter name --------------------------------------------
    ai._call_openai("k", "gpt-5-mini", "sys", "user")
    check("a gpt-5 model gets max_completion_tokens: %r" % sorted(SEEN),
          SEEN.get("max_completion_tokens") == ai.DEFAULT_MAX_OUT
          and "max_tokens" not in SEEN)
    check("which matters because it is the SHIPPED OpenAI default",
          ai.PROVIDERS["openai"][2].startswith("gpt-5"))
    ai._call_openai("k", "gpt-4o-mini", "sys", "user")
    check("an older model keeps max_tokens: %r" % sorted(SEEN),
          SEEN.get("max_tokens") == ai.DEFAULT_MAX_OUT
          and "max_completion_tokens" not in SEEN)
    ai._call_openai("k", "openai/gpt-5-mini", "sys", "user")
    check("and the openrouter vendor prefix does not hide it: %r" % sorted(SEEN),
          "max_completion_tokens" in SEEN)

    print()
    # ---- an array-shaped request must not be forced into an object ------------
    TEXT["openai"] = '["Nemesis", "Salamander"]'
    os.environ["AI_PROVIDER"] = "openai"
    try:
        got = ai.title_aliases("Gradius")
        check("title_aliases gets its aliases on OpenAI: %r" % (got,),
              got == ["Nemesis", "Salamander"])
        check("because the reply was not forced into a JSON OBJECT: %r" % sorted(SEEN),
              SEEN.get("response_format") is None)
        # Belt and braces: a provider that wraps the array anyway is still understood.
        TEXT["openai"] = '{"aliases": ["Nemesis"]}'
        check("and a wrapped array is still read: %r" % (ai.title_aliases("Gradius"),),
              ai.title_aliases("Gradius") == ["Nemesis"])
        # JSON mode remains the default for everything that asks for an object.
        ai._call_openai("k", "gpt-4o-mini", "sys", "user")
        check("object-shaped requests still use JSON mode",
              SEEN.get("response_format") == {"type": "json_object"})
    finally:
        os.environ.pop("AI_PROVIDER", None)
        TEXT["openai"] = '{"ok":1}'

    print("\nRESULT: %d checks, all passed" % len(PASS))


main()
