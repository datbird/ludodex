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

    print("\n%d/%d passed" % (sum(PASS), len(PASS)))


main()
