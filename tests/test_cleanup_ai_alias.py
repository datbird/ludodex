#!/usr/bin/env python3
"""An alias marker is a whole SEGMENT of a model id, never a substring of one.

`looks_like_alias` decides whether a model name is a moving POINTER (`gemini-flash-latest`)
rather than a fixed model. Answering yes is not free: it is what sends `suggest_price`
into `detect_model_version`, which makes a REAL, PAID call to the provider to ask what
the alias currently resolves to. A false positive there spends money on a name that was
never an alias.

The rule was written as four substrings — `("-latest", "latest", "-preview", ":latest")`
— tested with `in`. Three of those four are dead: bare `"latest"` already contains
`-latest` and `:latest` as substrings, so the tuple's shape hid the fact that the real
test was "does this id contain the six letters l-a-t-e-s-t anywhere". Any model whose
name merely embeds them — a `translatest-9b`, a `latestone` build tag — was billed a
probe call as though it were a pointer.

Model ids are delimited (`-`, `:`, `.`, `/`, `_`). A pointer says so in a segment of its
own. That is the rule this pins.

Offline. No network: `detect_model_version` is never called from here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-alias-")
DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import ai                                          # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    print("1. the pointers the resolver exists for are still recognised")
    for name in ("gemini-flash-latest", "claude-sonnet-4-5-latest", "llama3:latest",
                 "gpt-4.5-preview", "gemini-2.5-pro-preview-03-25",
                 "GEMINI-FLASH-LATEST"):
        check("%r is a pointer" % name, ai.looks_like_alias(name))

    print("2. a concrete, priceable model is not one")
    for name in ("gemini-2.5-flash", "claude-haiku-4-5-20251001", "gpt-4o",
                 "openai/gpt-5", ""):
        check("%r is a real model" % name, not ai.looks_like_alias(name))
    check("None is not a pointer", not ai.looks_like_alias(None))

    print("3. a name that merely CONTAINS the letters is not a pointer")
    # This is the whole point: each of these used to cost a paid probe call.
    for name in ("translatest-9b", "mistral-latestone", "prelatest", "notlatest-mini",
                 "unpreviewed-13b"):
        check("%r does not spend a probe call" % name, not ai.looks_like_alias(name))

    print("4. the marker list states each rule once")
    # The old tuple carried the same rule three times over ("-latest" and ":latest" are
    # both inside "latest"), which is how a substring test passed for a suffix rule.
    words = ai.ALIAS_WORDS
    check("no marker subsumes another (%r)" % (words,),
          not any(a != b and a in b for a in words for b in words))

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
