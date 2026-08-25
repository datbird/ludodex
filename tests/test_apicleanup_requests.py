#!/usr/bin/env python3
"""Request handling that turned a bad request into a 500, or a bad row into half a write.

  * /api/aimeta/accept-all compared `f["confidence"] >= minc` with no None guard. The
    rows come from `SELECT *` and `confidence` is nullable, so one NULL raised TypeError
    — partway through a loop whose `set_status` COMMITS EACH ROW ON ITS OWN. The bulk
    accept therefore aborted half applied, with nothing recording how far it got.
  * The media-diff endpoints ran `int()` straight on a client-supplied `igdb_id`, so a
    non-numeric one raised ValueError out of the handler and answered 500 — "the server
    is broken" — for what is a bad request.
  * app.py reached into `ai._resolve`, another module's private surface, purely to ask
    "is an AI provider usable". `_ai_ready` asks the same question through ai's public
    accessors.

Offline: sqlite fixtures in an isolated data dir. No provider is contacted — the IGDB
preview is stubbed out, because what is under test is the validation that runs BEFORE
it, and the 400 must be raised before any provider call is paid for.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-apicleanup-r-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from fastapi import HTTPException                              # noqa: E402
from server import app                                         # noqa: E402
import aimeta                                                  # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


# --------------------------------------------------------------------------- #
#  1. accept-all: a NULL confidence neither crashes nor splits the batch
# --------------------------------------------------------------------------- #
def seed_findings():
    con = aimeta._con()
    con.execute("DELETE FROM findings")
    rows = [(1, "alpha", 90.0), (2, "bravo", None), (3, "charlie", 80.0),
            (4, "delta", None), (5, "echo", 10.0)]
    for fid, nk, conf in rows:
        con.execute("INSERT INTO findings(id,run_id,norm_key,title,kind,status,"
                    "payload_json,confidence,model,created) "
                    "VALUES(?,1,?,?,'match','proposed','{}',?,'m',1)",
                    (fid, nk, nk.title(), conf))
    con.commit()
    con.close()


def statuses():
    con = aimeta._con()
    try:
        return {r[0]: r[1] for r in con.execute("SELECT id, status FROM findings")}
    finally:
        con.close()


def part1():
    print("1. accept-all survives a NULL confidence, all or nothing")
    seed_findings()
    res = app.aimeta_accept_all({})
    st = statuses()
    check("every proposed finding was accepted at min 0", res["accepted"] == 5)
    check("including the NULL-confidence ones", all(v == "accepted" for v in st.values()))
    check("nothing was left proposed — no half-applied batch",
          "proposed" not in st.values())

    seed_findings()
    res = app.aimeta_accept_all({"min_confidence": 50})
    st = statuses()
    check("a threshold accepts only the rows above it", res["accepted"] == 2)
    check("an unscored (NULL) finding reads as 0, so a threshold skips it",
          st[2] == "proposed" and st[4] == "proposed")
    check("and the low-confidence one is skipped too", st[5] == "proposed")
    check("the ones above the threshold are accepted",
          st[1] == "accepted" and st[3] == "accepted")


# --------------------------------------------------------------------------- #
#  2. a client-supplied igdb_id is validated, not int()'d and hoped for
# --------------------------------------------------------------------------- #
def part2():
    print("\n2. a non-numeric igdb_id is a 400, not a 500")
    called = []
    real = app._igdb_media_preview
    app._igdb_media_preview = lambda ids: called.append(list(ids)) or {}
    try:
        for bad in ("not-a-number", "12x", {"a": 1}, [7]):
            err = None
            try:
                app.aimeta_media_diff({"items": [{"norm_key": "x", "igdb_id": bad}]})
            except HTTPException as e:
                err = e
            except Exception as e:                             # noqa: BLE001
                err = e
            check("%r is refused as an HTTPException" % (bad,),
                  isinstance(err, HTTPException))
            check("%r answers 400, not 500" % (bad,),
                  isinstance(err, HTTPException) and err.status_code == 400)
        check("and the provider was never called for a bad request", called == [])

        # a numeric STRING is accepted, and normalised to an int for the comparisons
        # downstream (_store_locked_igdb compares against an int column)
        app.aimeta_media_diff({"items": [{"norm_key": "x", "igdb_id": "4242"}]})
        check("a numeric string is accepted", called and called[-1] == [4242])
        check("and reaches the provider as an int",
              called[-1] and isinstance(called[-1][0], int))
    finally:
        app._igdb_media_preview = real

    check("_int_or_none coerces what it can", app._int_or_none("12") == 12)
    check("_int_or_none returns None instead of raising",
          app._int_or_none("x") is None and app._int_or_none(None) is None
          and app._int_or_none({}) is None)


# --------------------------------------------------------------------------- #
#  3. the AI precondition is asked through ai's public surface
# --------------------------------------------------------------------------- #
def part3():
    print("\n3. the AI-usable precondition uses ai's public accessors")
    _ai = app.ai
    src = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read()
    check("app.py no longer calls ai._resolve", "ai._resolve(" not in src)

    real_ap, real_kf = _ai.active_provider, _ai.key_for
    try:
        _ai.active_provider = lambda: ""
        err = None
        try:
            app._ai_ready()
        except RuntimeError as e:
            err = e
        check("no provider configured raises", err is not None)

        _ai.active_provider = lambda: "gemini"
        _ai.key_for = lambda p: ""
        err = None
        try:
            app._ai_ready()
        except RuntimeError as e:
            err = e
        check("a provider with no key raises", err is not None)

        _ai.key_for = lambda p: "k"
        prov, model = app._ai_ready("gemini", "some-model")
        check("a usable provider returns (provider, model)",
              (prov, model) == ("gemini", "some-model"))
    finally:
        _ai.active_provider, _ai.key_for = real_ap, real_kf


def main():
    print("audit cleanup: bad input is a bad request, and a bulk write is all or nothing")
    part1()
    part2()
    part3()
    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
