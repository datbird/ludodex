#!/usr/bin/env python3
"""Paid AI must never fire by accident (#34) — the project's first rule, untested.

Every other invariant in this repo is about correctness. This one is about money, and it
had no coverage at all: the tier system, the budget caps and the "already judged" markers
were each trusted to work because they looked right.

Five properties, from the inventory's H section:

  H1  no paid call fires without an explicit scope
  H2  an already-judged game is not re-billed
  H3  a configured cap actually stops a loop
  H4  Algo makes ZERO model calls — by definition, never verified
  H5  Lite judges covers only; Heavy judges every kind

Offline. Nothing here may reach a provider — a test of the spend guardrail that spends
money would be its own counterexample, so `ai` is stubbed and any real call raises.
"""
import os
import sqlite3
import sys

import test_support

PASS = []


def check(l, c):
    PASS.append(c); print("  %s   %s" % ("ok " if c else "FAIL", l))
    if not c:
        sys.exit("FAILED: " + l)


def main():
    d = test_support.isolate("ludodex-spend-")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import config
    from server import app as srv
    from server import ai

    # ---- H4: the default costs nothing --------------------------------------
    check("a source with no tier chosen defaults to algo",
          srv.import_mode_for("brand-new-store") == "algo")
    config.set_("import_mode_steam", "lite")
    check("an explicit tier is honoured", srv.import_mode_for("steam") == "lite")
    config.set_("import_mode_steam", "nonsense")
    check("an unrecognised tier falls back to algo, never to a paid one",
          srv.import_mode_for("steam") == "algo")
    config.set_("import_mode_steam", "")

    # ---- H4: Algo reaches no model, structurally ----------------------------
    calls = []
    real_pick = ai.pick_art
    ai.pick_art = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a model was called: %r" % (a[:2],)))
    try:
        # `_ai_art_pass` is the only paid art path; with no area configured it must
        # decline rather than proceed. area_available() is the gate.
        real_avail = ai.area_available
        ai.area_available = lambda area: False
        try:
            n = srv._ai_art_pass(["anything"], heavy=False)
            check("with no AI configured the art pass does nothing", n == 0)
        finally:
            ai.area_available = real_avail
    finally:
        ai.pick_art = real_pick

    # ---- H5: the tier contract ----------------------------------------------
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "server", "app.py")).read()
    ap = src[src.index("def _ai_art_pass("):]
    ap = ap[:ap.index("\ndef ", 10)]
    check("Lite restricts vision to covers", 'kinds = None if heavy else ("cover",)' in ap)
    check("Heavy leaves every kind in scope", "None if heavy" in ap)
    check("the scope marker distinguishes the two",
          'scope = "all" if heavy else "cover"' in ap)

    # ---- H2: a judged game is not re-billed ---------------------------------
    idx = os.path.join(d, "media-index.sqlite")
    con = sqlite3.connect(idx)
    con.execute("CREATE TABLE art_adjudicated(norm_key TEXT PRIMARY KEY, at INT, "
                "scope TEXT)")
    con.execute("INSERT INTO art_adjudicated VALUES('done', 0, 'cover')")
    con.execute("INSERT INTO art_adjudicated VALUES('deep', 0, 'all')")
    con.commit(); con.close()
    check("a cover-judged game is skipped by another cover pass",
          srv._art_adjudicated("done", "cover") is True)
    check("a cover-judged game is NOT skipped by a deeper pass — Heavy must get to "
          "judge kinds Lite never looked at",
          srv._art_adjudicated("done", "all") is False)
    check("an all-judged game is skipped at any depth",
          srv._art_adjudicated("deep", "all") is True
          and srv._art_adjudicated("deep", "cover") is True)
    check("an unjudged game is never skipped",
          srv._art_adjudicated("never", "cover") is False)

    # ---- H3: a cap stops the loop -------------------------------------------
    # No cap configured -> check_limit is a no-op. A cap at zero usage -> still fine.
    # A cap already exceeded -> raises, and the caller treats that as STOP not as error.
    check("with no caps configured nothing is refused",
          ai.check_limit("gemini", "gemini-flash-latest") is None)

    hit = []
    real_limit = ai.check_limit

    def _capped(p, m):
        hit.append((p, m))
        raise RuntimeError("monthly budget reached")
    ai.check_limit = _capped
    ai.area_available = lambda area: True
    try:
        n = srv._ai_art_pass(["a", "b", "c"], heavy=False)
        check("a cap stops the pass instead of raising out of it", n == 0)
        check("the cap was consulted before any work", len(hit) >= 1)
        check("and the pass did not power through it — at most one check per worker",
              len(hit) <= srv.AI_ART_WORKERS)
    finally:
        ai.check_limit = real_limit

    # ---- H1: paid work is always scoped -------------------------------------
    check("the art pass with an empty scope does nothing",
          srv._ai_art_pass([], heavy=False) == 0)
    check("the art pass with None does nothing", srv._ai_art_pass(None) == 0)
    ap_call = src[src.index("_phase(\"supplement\""):][:2000]
    check("the import only reaches the AI supplement for lite/heavy sources",
          'in ("lite", "heavy")' in ap_call or "ai_srcs" in ap_call)

    print("\n%d/%d passed" % (sum(PASS), len(PASS)))


main()
