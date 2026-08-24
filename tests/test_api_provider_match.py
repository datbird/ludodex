#!/usr/bin/env python3
"""Provider matching: don't reject a game's other systems, don't bin paid work (#27, #19).

#27 — `_ss_match` searches ScreenScraper once per system the game is on (`for sid in
sids`), but the candidate fit check asked `ss.system_fits(systems[0], j)` — always the
FIRST system. `system_fits` refuses when both sides name a system and disagree, so for a
game on [snes, genesis] whose SNES search returns nothing, every record the GENESIS search
returned was thrown away for being a Genesis record. The fit has to be judged against the
system that was actually searched.

#19 — the call site built `pms = [p for p in (_provider_match(…), _ss_match(…)) if p]`
inside the per-game try. `_ss_match` RAISES on purpose when it never completed (budget
exhausted, or every search errored) so the miss is not cached as an answer. Raised there
the exception skipped `aimeta.store_finding`, which threw away the `ai.analyze_game` call
the user had ALREADY BEEN BILLED FOR, threw away a perfectly good IGDB match alongside it,
and counted the game `errored` — so the next scan bought the same answer again.

Offline: `screenscraper` is replaced in sys.modules with a stub that answers from a fixture
table, so no network and no credentials. The acceptance gate (`matchgate`) is the real one.
"""
import os
import sys
import types

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-api-match-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


SYS_IDS = {"snes": 4, "genesis": 1, "megadrive": 1}


def fake_ss(results, calls):
    """A screenscraper stand-in. `results` maps a searched system id -> records."""
    m = types.ModuleType("screenscraper")

    def systeme_id(label):
        return SYS_IDS.get((label or "").lower())

    def jeu_recherche(creds, q, systemeid=None, limit=30):
        calls.append(systemeid)
        return list(results.get(systemeid, []))

    def system_fits(platform, jeu):            # the REAL rule, copied verbatim
        want, got = systeme_id(platform), jeu.get("systeme", {}).get("id")
        if not want or not got:
            return True
        return want == got

    m.systeme_id = systeme_id
    m.jeu_recherche = jeu_recherche
    m.system_fits = system_fits
    m.jeu_year = lambda j: j.get("year")
    m.jeu_name = lambda j: j.get("name")
    m.jeu_system_id = lambda j: j.get("systeme", {}).get("id")
    return m


def with_ss(module, fn):
    saved_mod = sys.modules.get("screenscraper")
    saved_creds = app.config.screenscraper_creds
    sys.modules["screenscraper"] = module
    app.config.screenscraper_creds = lambda: {"devid": "x", "devpassword": "y"}
    try:
        return fn()
    finally:
        app.config.screenscraper_creds = saved_creds
        if saved_mod is None:
            del sys.modules["screenscraper"]
        else:
            sys.modules["screenscraper"] = saved_mod


class FakeAimeta:
    SUPPLEMENT_KINDS = ()

    def __init__(self):
        self.stored = []

    def _lib(self):
        class C:
            def close(self):
                pass
        return C()

    def review_targets(self, n):
        return []

    def game_context(self, nk, lib=None):
        return {"title": "Sonic the Hedgehog", "systems": ["genesis"], "missing": []}

    def store_finding(self, run_id, ctx, res, model):
        self.stored.append(res)
        return True

    def mark_reviewed(self, nk, lib=None):
        pass

    def scan_progress(self, *a):
        pass

    def scan_finish(self, *a):
        pass


def main():
    print("provider matching keeps what it found and what it paid for")

    # ---- #27: the game's SECOND system --------------------------------------- #
    genesis_rec = {"id": 900, "name": "Sonic the Hedgehog", "year": "1991",
                   "systeme": {"id": 1}}
    calls = []
    ss = fake_ss({1: [genesis_rec]}, calls)          # SNES search finds nothing
    got = with_ss(ss, lambda: app._ss_match(["Sonic the Hedgehog"],
                                            ["snes", "genesis"], 1991))
    check("the genesis system was searched", 1 in calls)
    check("a record for the system being searched is accepted",
          got is not None and got.get("ss_id") == 900)

    # and the rule it was protecting still holds: a record for a system the game is
    # NOT on stays refused.
    calls = []
    ss = fake_ss({4: [{"id": 901, "name": "Sonic the Hedgehog", "year": "2006",
                       "systeme": {"id": 1}}]}, calls)
    got = with_ss(ss, lambda: app._ss_match(["Sonic the Hedgehog"], ["snes"], 1991))
    check("a wrong-system record is still refused", got is None)

    # ---- #19: a provider search that raises ----------------------------------- #
    fake = FakeAimeta()
    saved = {n: getattr(app, n) for n in
             ("aimeta", "ai", "_provider_match", "_ss_match", "_emulation_consoles",
              "resolve_per_entry_identity", "_auto_detect_collections",
              "_score_confidence_ai", "_wand_fill_media")}

    def boom(*a, **k):
        raise RuntimeError("screenscraper search did not complete (budget exhausted)")

    class FakeAI:
        def model_for_area(self, area):
            return "test-model"

        def analyze_game(self, ctx, web=False):
            return {"match": {"suggested_title": "Sonic the Hedgehog",
                              "suggested_year": 1991}}

    app.aimeta = fake
    app.ai = FakeAI()
    app._provider_match = lambda t, y, consoles=None: {"provider": "igdb", "igdb_id": 1234}
    app._ss_match = boom
    app._emulation_consoles = lambda nk: []
    app.resolve_per_entry_identity = lambda keys, stop: {"set": [], "detached": []}
    app._auto_detect_collections = lambda keys, stop: []
    app._score_confidence_ai = lambda keys, stop: {"scored": 0}
    app._wand_fill_media = lambda keys, web, stop: None
    try:
        app._aimeta_scan(1, ["sonic-the-hedgehog"],
                         {"match_provider": True, "want_media": False}, lambda: False)
    finally:
        for n, v in saved.items():
            setattr(app, n, v)

    check("the finding was stored despite the provider failure", len(fake.stored) == 1)
    res = fake.stored[0] if fake.stored else {}
    check("the AI analysis it was billed for survived", bool(res.get("match")))
    check("and the IGDB match survived with it",
          [p["provider"] for p in (res.get("provider_matches") or [])] == ["igdb"])

    # ---- #19 (second half): the budget message prints ONCE -------------------- #
    src = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read()
    body = src.split("def _ss_match(", 1)[1].split("\ndef ", 1)[0]
    check("giving up on the budget leaves the per-system loop too",
          "if not completed:" in body and "break" in body.split("if not completed:", 1)[1][:120])

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
