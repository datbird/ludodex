#!/usr/bin/env python3
"""The background media queue must never be wedged by one exception (#33).

`_enqueue_media_reconcile` raises the single-flight flag `_MEDIA_RUNNING[0]` and starts a
worker. The worker caught the drain's exception into `rec["error"]` and stopped there —
only `_scoped_media_drain`'s own clean return ever put the flag back down. `_media_finish`
can raise, so one bad game left the flag UP for the life of the process: every later pin /
art-apply / wand enqueue added its keys to `_MEDIA_Q` and nothing ever started them again,
with no error to look at because the failing job record had been replaced.

Also #35: `_post_scan_media_scores` starts three sibling jobs from INSIDE a background
scan thread, and `_start_job` raises HTTPException(409) when a job of that name is still
alive (the Steam media job's timeout is 7200s). Raised there it became the scan's
`rec["error"]`, and the two jobs after it — the Heavy run's score refresh and AI consensus,
the paid work the user chose the tier for — were never started at all.

Offline. The drain and the scripts the jobs run are replaced with stubs; what is under
test is the bookkeeping around them, not the media chain itself.
"""
import os
import sys
import threading
import time

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-api-queue-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def join_job(jid, timeout=5):
    rec = app._JOBS.get(jid)
    if rec and rec.get("thread"):
        rec["thread"].join(timeout)
    return rec


def main():
    print("a wedged queue and a busy sibling job")

    # ---- #33: the drain raises ------------------------------------------------ #
    runs = []

    def exploding_drain(should_stop):
        runs.append("run")
        raise RuntimeError("_media_finish blew up")

    orig = app._scoped_media_drain
    app._scoped_media_drain = exploding_drain
    try:
        app._enqueue_media_reconcile({"game-one"}, True)
        rec = join_job("aimeta-media")
        check("the drain ran", len(runs) == 1)
        check("the failure is on the job record", bool(rec and rec.get("error")))
        check("the single-flight flag is back down", app._MEDIA_RUNNING[0] is False)

        # the real symptom: everything queued afterwards
        app._enqueue_media_reconcile({"game-two"}, True)
        join_job("aimeta-media")
        check("a later enqueue still starts a drain", len(runs) == 2)
    finally:
        app._scoped_media_drain = orig
        with app._MEDIA_LOCK:
            app._MEDIA_RUNNING[0] = False
            app._MEDIA_Q.clear()

    # ---- and releasing the flag must not steal it from a LATER drain ---------- #
    # The clean-return path clears the flag itself, so a worker that always cleared it in
    # its `finally` could lower a flag a NEWER worker had just raised — and then a third
    # enqueue would start a second drain alongside the running one, which is what the
    # single-flight gate exists to prevent.
    go, go2, seen = threading.Event(), threading.Event(), []

    def slow_finish(should_stop):
        with app._MEDIA_LOCK:                    # what the real drain does on a clean exit
            app._MEDIA_RUNNING[0] = False
        seen.append("one")
        go.wait(5)                               # …then linger before the worker returns

    def second(should_stop):
        seen.append("two")
        go2.wait(5)                              # still running while we check the flag

    app._scoped_media_drain = slow_finish
    try:
        app._enqueue_media_reconcile({"game-three"}, True)
        first = app._JOBS["aimeta-media"]["thread"]
        while "one" not in seen:
            time.sleep(0.01)
        app._scoped_media_drain = second
        app._enqueue_media_reconcile({"game-four"}, True)
        later = app._JOBS["aimeta-media"]["thread"]
        check("a second drain did start once the flag was down", later is not first)
        while "two" not in seen:
            time.sleep(0.01)
        go.set()
        first.join(5)
        check("the finishing worker leaves the newer drain's flag alone",
              app._MEDIA_RUNNING[0] is True)
        go2.set()
        later.join(5)
    finally:
        app._scoped_media_drain = orig
        go.set()
        go2.set()
        with app._MEDIA_LOCK:
            app._MEDIA_RUNNING[0] = False
            app._MEDIA_Q.clear()

    # ---- #35: a sibling job is still running ---------------------------------- #
    started, hold = [], threading.Event()

    def fake_run_script(script, out=None, capture=False, timeout=300, args=None,
                        job=None, env=None):
        started.append((script, list(args or [])))
        return True, ""

    orig_rs, orig_heavy = app._run_script, app._heavy_ai_consensus
    app._run_script = fake_run_script
    app._heavy_ai_consensus = lambda keys, stop: started.append(("consensus", list(keys)))
    try:
        # a Steam-media job from an earlier scan is STILL ALIVE (7200s timeout)
        app._start_job("steammedia:wand", "steammedia", "busy", lambda stop: hold.wait(30))
        time.sleep(0.05)
        busy = app._JOBS["steammedia:wand"]
        check("the sibling job is genuinely alive", busy["thread"].is_alive())

        raised = None
        try:
            app._post_scan_media_scores(["nk-1"], {"pull_scores": True})
        except Exception as e:                                 # noqa: BLE001
            raised = e
        check("a busy sibling does not abort the run", raised is None)

        join_job("scores:wand")
        join_job("consensus:wand")
        names = [s[0] for s in started]
        check("the Heavy score refresh still ran",
              any(n == "scores_fetch.py" for n in names))
        check("the Heavy AI consensus still ran", "consensus" in names)
    finally:
        hold.set()
        join_job("steammedia:wand")
        app._run_script, app._heavy_ai_consensus = orig_rs, orig_heavy

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
