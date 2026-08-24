#!/usr/bin/env python3
"""Work that did not happen must not be reported as done (#34, #36).

#34 — a wand apply ended with `_apply_surgical_meta(touched)` in a try/except whose
message says "rebuild will apply", then ran `aimeta.mark_applied(only_ids)` regardless.
There IS no follow-up rebuild: `/api/catalog/rebuild` says in as many words that wand
applies no longer trigger one. So a surgical failure left the catalog unchanged, moved
the findings out of pending where nobody could retry them, and told the user it worked.

#36 — three endpoints ran a long child process and threw the result away:
  (a) DELETE /api/ingest-hints ran build_library.py with a bare `subprocess.run` and
      returned {"ok": True} whatever it exited with — and it was the one rebuild path
      that never went through `_run_script`, so its failure tail was not even captured.
  (b) /api/media/language-filter DISCARDED the `(ok, err)` of its media_choose re-pick,
      so a failed re-pick returned the previous pass's chosen art as a fresh result.
  (c) /api/media/scan-local dropped the return code of BOTH children, so a crashed
      media_index.py finished as a clean job, and `should_stop` was only consulted
      between two half-hour subprocesses.

Offline. The child processes and the finding store are stubs: what is under test is
whether the caller believes them when they fail, which is exactly where the bug was.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-api-apply-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


class FakeAimeta:
    """Just the surface `_aimeta_apply` touches."""
    SUPPLEMENT_KINDS = ()

    def __init__(self):
        self.applied = []

    def accepted_ids(self):
        return [1, 2]

    def accepted_provider_matches(self):
        return []

    def accepted_supplements(self):
        return {}

    def accepted_ss_matches(self):
        return []

    def accepted_collections(self):
        return []

    def mark_applied(self, ids):
        self.applied.append(list(ids or []))


def run_apply(surgical_raises):
    """_aimeta_apply with everything but the surgical step stubbed out."""
    fake = FakeAimeta()
    saved = {n: getattr(app, n) for n in
             ("aimeta", "_record_accepted_collections", "_materialize_collection_members",
              "_igdb_token", "_apply_ss_matches", "_apply_surgical_meta",
              "_reconcile_media_now", "_run_script")}

    def surgical(touched):
        if surgical_raises:
            raise RuntimeError("no such column: game_key")

    app.aimeta = fake
    app._record_accepted_collections = lambda c: None
    app._materialize_collection_members = lambda created_out=None, ingest=False: None
    app._igdb_token = lambda: (None, None)
    app._apply_ss_matches = lambda now: None
    app._apply_surgical_meta = surgical
    app._reconcile_media_now = lambda touched, now: None
    app._run_script = lambda *a, **k: (True, "")
    try:
        err = None
        try:
            app._aimeta_apply(lambda: False, only_ids=[1, 2])
        except Exception as e:                                 # noqa: BLE001
            err = e
        return fake.applied, err
    finally:
        for n, v in saved.items():
            setattr(app, n, v)


def main():
    print("a failed step is reported as a failure")

    # ---- #34 ------------------------------------------------------------------ #
    applied, err = run_apply(surgical_raises=False)
    check("a clean apply marks its findings applied", applied == [[1, 2]])
    check("and raises nothing", err is None)

    applied, err = run_apply(surgical_raises=True)
    check("a FAILED surgical apply marks nothing applied", applied == [])
    check("and the failure reaches the job monitor", err is not None)
    check("with the reason in the message",
          "game_key" in str(err) or "surgical" in str(err).lower())

    # ---- #36a: DELETE /api/ingest-hints ---------------------------------------- #
    calls = []

    def failing_script(script, out=None, capture=False, timeout=300, args=None,
                       job=None, env=None):
        calls.append(script)
        return False, "build_library.py: MemoryError"

    saved_rs, saved_hints = app._run_script, app.ingesthints
    app._run_script = failing_script

    class FakeHints:
        def clear(self, system=None):
            return 7

        def count(self):
            return 0

        def listing(self, limit):
            return []

    app.ingesthints = FakeHints()
    try:
        res = app.ingest_hints_clear()
        check("clearing hints rebuilds through _run_script",
              calls == ["build_library.py"])
        check("a failed rebuild is NOT reported as ok", res.get("ok") is False)
        check("and says what failed", "MemoryError" in (res.get("error") or ""))
        check("while still reporting what it did clear", res.get("cleared") == 7)
    finally:
        app._run_script, app.ingesthints = saved_rs, saved_hints

    # ---- #36b: /api/media/language-filter -------------------------------------- #
    saved_rs, saved_ml = app._run_script, app.medialang
    app._run_script = failing_script

    class FakeLang:
        def apply_filter(self, mode):
            return {"hidden": 3}

    app.medialang = FakeLang()
    try:
        res = app.media_language_filter({"mode": "hide"})
        check("a failed re-pick is not silently a success",
              res.get("repick_ok") is False)
        check("and the re-pick failure is reported",
              "MemoryError" in (res.get("error") or ""))
        check("the filter's own counts survive", res.get("hidden") == 3)
    finally:
        app._run_script, app.medialang = saved_rs, saved_ml

    # ---- #36c: /api/media/scan-local ------------------------------------------- #
    saved_rs, saved_sj = app._run_script, app._start_job
    jobs = {}

    def capture_job(jid, kind, label, fn, run_id=None, cancelable=False):
        jobs["fn"] = fn
        return jid

    app._start_job = capture_job
    app._run_script = failing_script
    try:
        app.media_scan_local({"root": os.path.join(DATA, "roms")})
        raised = None
        try:
            jobs["fn"](lambda: False)
        except Exception as e:                                 # noqa: BLE001
            raised = e
        check("a crashed art index fails the job", raised is not None)
        check("naming the child that failed", "media_index" in str(raised))

        # and a stop between the two long children is honoured
        app._run_script = lambda *a, **k: (True, "")
        stops = [False, True]
        seen = []

        def counting(script, out=None, capture=False, timeout=300, args=None,
                     job=None, env=None):
            seen.append(script)
            return True, ""

        app._run_script = counting
        jobs["fn"](lambda: stops.pop(0) if stops else True)
        check("a stop request skips the second child", "media_choose.py" not in seen)
    finally:
        app._run_script, app._start_job = saved_rs, saved_sj

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
