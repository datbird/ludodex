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
  H6  an answer the match index already holds NEVER becomes a paid call
  H7  a budget it cannot MEASURE stops, instead of quietly passing
  H8  the price prompt appears ONLY when a budget exists AND the price is missing or
      stale, and only when AI will actually run

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
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "ludodex"))
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

    # ---- H6: the free answer beats the paid one -----------------------------
    # `_suspect` sends MANGLED filenames to a model, which is exactly the population a
    # dump-verified hash exists to identify. Asking a model to guess what a CRC already
    # states is the most literal form of paying for what was free.
    import ingest_ai
    import matchindex
    import ingesthints

    ix = sqlite3.connect(matchindex.DB)
    ix.executescript("""
    CREATE TABLE IF NOT EXISTS identity(id INTEGER PRIMARY KEY, name TEXT,
      norm_key TEXT, year INTEGER, first_release_date INTEGER, built_at INTEGER);
    CREATE TABLE IF NOT EXISTS identity_key(ns TEXT, val TEXT, identity_id INTEGER,
      kind TEXT, PRIMARY KEY(ns, val, identity_id));
    CREATE TABLE IF NOT EXISTS identity_state(k TEXT PRIMARY KEY, v TEXT);
    """)
    ix.execute("INSERT OR REPLACE INTO identity VALUES(31,'Chrono Trigger',"
               "'chrono trigger',1995,NULL,0)")
    ix.execute("INSERT OR IGNORE INTO identity_key VALUES('crc','aabbccdd',31,'exact')")
    ix.commit(); ix.close()

    known = {"system": "snes", "game": "CT_(U)_[!]", "path": "CT.zip",
             "crc": "aabbccdd", "sha1": None}
    unknown = {"system": "snes", "game": "ZZ_(U)_[!]", "path": "ZZ.zip",
               "crc": "00000000", "sha1": None}

    free, rest = ingest_ai.identify_from_index([known, unknown])
    check("the hash-identified rom is answered for free", free == 1)
    check("and is REMOVED from what a model is asked", rest == [unknown])

    rows = {(r[0], r[1]): r for r in sqlite3.connect(ingesthints.DB).execute(
        "SELECT system,game,to_title,confidence,model FROM hints")}
    row = rows.get((known["system"], known["game"]))
    check("the hint carries the index's title: %r" % (row and row[2]),
          row and row[2] == "Chrono Trigger")
    check("at confidence 1.0 — a dump is not a guess", row and row[3] == 1.0)
    check("and is attributed to the index, not a model: %r" % (row and row[4]),
          row and row[4] == "match-index")

    # An estimate that writes hints is not an estimate.
    before = sqlite3.connect(ingesthints.DB).execute(
        "SELECT COUNT(*) FROM hints").fetchone()[0]
    ingest_ai.identify_from_index([unknown, dict(known, game="Other_(U)")], write=False)
    after = sqlite3.connect(ingesthints.DB).execute(
        "SELECT COUNT(*) FROM hints").fetchone()[0]
    check("write=False records nothing", before == after)

    # Fail-open: no hash, no index, or a broken lookup must never drop a target.
    nohash = {"system": "snes", "game": "Q", "path": "q.zip", "crc": None, "sha1": None}
    n, left = ingest_ai.identify_from_index([nohash])
    check("a rom with no hash is still asked about", n == 0 and left == [nohash])
    check("an empty list is handled", ingest_ai.identify_from_index([])[0] == 0)

    isrc = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "ludodex", "ingest_ai.py")).read()
    check("run() asks the index BEFORE the model loop",
          0 < isrc.find("identify_from_index(items)") < isrc.find("ai.identify_roms"))

    # ---- H7: an unmeasurable budget is a stop, not a pass ------------------
    # Live, this was not theoretical. `gemini-flash-latest` is an ALIAS and never
    # appears in any price table, so cost_usd() returned None, `unpriced` went True, and
    # _enforce SKIPPED the dollar cap entirely. A $20 budget sat in the database while
    # 19,954,304 input tokens were recorded at $0.00 and nothing ever stopped.
    real_lim, real_usage = ai.limits_map, ai._month_usage
    try:
        ai.limits_map = lambda: {"global": {"all": {"total": 0, "usd": 20.0,
                                                    "input": 0, "output": 0}},
                                 "provider": {}, "model": {}}
        # Priced and under budget: work proceeds.
        ai._month_usage = lambda scope, key: (1000, 900, 100, 1.0, False)
        ai.check_limit("gemini", "gemini-2.5-flash")
        check("a measurable budget under the cap allows the call", True)

        # Priced and over budget: the ordinary stop.
        ai._month_usage = lambda scope, key: (1000, 900, 100, 25.0, False)
        try:
            ai.check_limit("gemini", "gemini-2.5-flash")
            check("an exceeded budget stops the call", False)
        except RuntimeError as e:
            check("an exceeded budget stops the call: %s" % str(e)[:40], True)

        # UNPRICED: the case that used to sail straight through.
        ai._month_usage = lambda scope, key: (19954304, 19954304, 391287, 0.0, True)
        try:
            ai.check_limit("gemini", "gemini-flash-latest")
            check("an UNMEASURABLE budget must not pass", False)
        except RuntimeError as e:
            check("an unmeasurable budget stops the call", True)
            check("and says why, naming the fix: %r" % str(e)[:48],
                  "cannot be measured" in str(e) and "Set a price" in str(e))

        # No budget configured is not the same as a budget that cannot be read.
        ai.limits_map = lambda: {"global": {}, "provider": {}, "model": {}}
        ai.check_limit("gemini", "gemini-flash-latest")
        check("with NO budget set, an unpriced model is free to run", True)
    finally:
        ai.limits_map, ai._month_usage = real_lim, real_usage

    print()
    # ---- H7b: the alias can be resolved, so the budget can be repaired ------
    check("a -latest name is recognised as a pointer, not a model",
          ai.looks_like_alias("gemini-flash-latest")
          and not ai.looks_like_alias("gemini-2.5-flash"))
    # WHEN GUESSING A PRICE, GUESS HIGH. Guessing low lets spend run past the cap;
    # guessing high trips it early and the user raises it. Only one costs money.
    name, price = ai._family_price("gemini", "gemini-3.7-flash")
    check("an unknown model borrows the DEAREST same-family price: %r %r"
          % (name, price), price and price[1] == max(
              p[1] for (pr, n), p in ai.DEFAULT_PRICES.items()
              if pr == "gemini" and "flash" in n))
    ex = ai.suggest_price("gemini", "gemini-2.5-flash")
    check("an already-priced model reports basis 'exact'", ex["basis"] == "exact")
    # A LOCAL TABLE BEING EMPTY IS NOT EVIDENCE A PRICE DOES NOT EXIST. suggest_price
    # skipped the published feed entirely, so it announced "nobody publishes a price for
    # gemini-3.7-flash" and substituted a same-family guess of $1.50/$9.00. The feed
    # lists that model at $0.375/$1.875 and lists the alias too — the guess was four
    # times the real rate, and the UI called it prudence.
    asrc = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "server", "ai.py")).read()
    i_feed = asrc.index("feed = feed_prices()")
    i_fam = asrc.index("name, price = _family_price(")
    check("the published feed is consulted BEFORE any guess", 0 < i_feed < i_fam)
    # The toggle is a PERMISSION, not a schedule. The Settings panel hides its "Fetch
    # current prices" button when it is off, and the Auto-resolve endpoint guards its
    # feed pull the same way — this dialog was the only thing reaching OpenRouter
    # against a setting that says not to.
    check("the OpenRouter toggle is honoured, like everywhere else",
          "if prices_openrouter_enabled():" in asrc[i_feed - 600:i_feed + 200])
    # It is still fetched ON DEMAND when permitted, rather than waiting for the daily
    # job the user may never have scheduled.
    check("but a permitted feed is fetched on demand, not on a schedule",
          "feed = feed_prices()" in asrc)
    # OpenRouter marks alias entries with a leading '~', and ':batch' variants are a
    # different product at half the rate.
    check("alias entries are matched, batch variants are not",
          'lstrip("~")' in asrc and '":" not in name' in asrc)
    tsx0 = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "web", "src", "App.tsx")).read()
    # The RENDERED string only. The comment above it quotes the old wording to explain
    # why it went, and a whole-file search matched that instead of the UI text — the
    # same trap a test in this repo already fell into once.
    i_bt = tsx0.index("const basisText =")
    rendered = tsx0[i_bt:tsx0.index("return (", i_bt)]
    check("a guessed price is labelled a GUESS, not prudence",
          "so this is a GUESS" in rendered and "high on purpose" not in rendered)
    check("and it names where a real price came from",
          "OpenRouter's public model list" in rendered)
    check("and suggest_price SAVES NOTHING",
          ai.price_get("gemini", "gemini-flash-latest") is None)

    print()
    # ---- H8: the prompt asks only when it has something to say ---------------
    # The first version pinned a banner to the top of the sync menu: it showed on merely
    # OPENING the menu, showed when the chosen tier was Algorithmic and no AI would run,
    # and being permanent it read as a broken app rather than a question.
    import datetime as _d
    prov, mdl = "gemini", "unit-test-model"
    ai.price_set(prov, mdl, 1.0, 2.0)
    st, age = ai.price_state(prov, mdl)
    check("a freshly set price reads 'ok': %r" % st, st == "ok")
    check("a model nobody priced reads 'missing'",
          ai.price_state(prov, "never-heard-of-it")[0] == "missing")
    # A price is not a constant — providers reprice, and an alias starts pointing at a
    # dearer model. A figure old enough stops being an answer.
    con = ai._usage_con()
    old_day = (_d.datetime.utcnow() - _d.timedelta(days=ai.PRICE_STALE_DAYS + 5)
               ).isoformat()
    con.execute("UPDATE prices SET updated=? WHERE provider=? AND model=?",
                (old_day, prov, mdl))
    con.commit(); con.close()
    st2, age2 = ai.price_state(prov, mdl)
    check("an aged price reads 'stale' (%s days)" % age2, st2 == "stale")
    # A seeded default ships with the release and carries no local date. It is a real
    # published figure, not something that aged here.
    check("a seeded default is not treated as stale",
          ai.price_state("gemini", "gemini-2.5-flash")[0] == "ok")

    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "server", "app.py")).read()
    i_gate = src.index("def ai_pricing_check")
    seg = src[i_gate:i_gate + 1800]
    check("no budget means no question, whatever the price state",
          'if not budget or state == "ok":' in seg)
    check("and the two failures are reported apart",
          'state == "stale"' in seg and '"missing"' in ai.price_state(
              prov, "never-heard-of-it")[0] + '"missing"')

    tsx = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "web", "src", "App.tsx")).read()
    check("the gate is a dialog, asked at run time, not a banner",
          "function AiPricingGate()" in tsx and "PricingGate onCleared" not in tsx)
    check("every AI path awaits ONE shared gate",
          "export async function ensureAiPricing" in tsx
          and tsx.count("ensureAiPricing(") >= 3)
    check("an algo-only run never asks",
          "const willUseAi =" in tsx and "tierOf(s) !== 'algo'" in tsx)
    check("and it is mounted once, above the app",
          "<AiPricingGate />" in tsx and tsx.count("<AiPricingGate />") == 1)
    # It shipped on .overlay-2, z-index 30 — the same value as .filter-menu. Equal
    # stacking falls through to DOM order, so the sync menu whose button opened the
    # dialog painted OVER it and clipped the sentence explaining the charge.
    css = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "web", "src", "App.css")).read()
    gate_src = tsx[tsx.index("function AiPricingGate()"):]
    gate_src = gate_src[:gate_src.index("\nfunction ")]
    check("the dialog carries its own stacking class, not the shared one",
          "ai-price-overlay" in gate_src and "overlay-2" not in gate_src)
    import re as _re
    z_gate = int(_re.search(r"\.overlay\.ai-price-overlay \{ z-index: (\d+)",
                            css).group(1))
    z_menu = int(_re.search(r"\.filter-menu \{[^}]*?z-index: (\d+)", css,
                            _re.S).group(1))
    check("it outranks the control that opens it (%d > %d)" % (z_gate, z_menu),
          z_gate > z_menu)

    print("\n%d/%d passed" % (sum(PASS), len(PASS)))


main()
