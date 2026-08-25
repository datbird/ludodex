#!/usr/bin/env python3
"""Job lifecycle: nothing deletes rows a live worker still owns, and a resume resumes.

  * Deleting a job set the worker's cancel flag and deleted the run's database rows in
    the NEXT STATEMENT. Cancel is cooperative — the worker only sees it between steps —
    so it went on writing progress and undo entries into a run that no longer existed.
    `_stop_and_wait` makes the delete wait for the worker to actually stop, and refuse
    (409) rather than delete out from under one that will not.
  * Restarting a paused metadata scan rebuilt its options from `scan_runs`, which has no
    column for `want_media`. `_aimeta_scan` defaults that to True, so a scan the user
    deliberately ran with media OFF resumed with media fill ON. The original run was also
    left untouched, so it stayed `done < total` and therefore restartable forever: two
    presses of ▶ started two workers over the same keys, each paying for the same games.
  * A publish writes a device's ROM tree for minutes and appeared in no job feed, so
    there was nothing to watch it with and no way to dismiss a finished one. Its
    check-then-set on _PUBLISH_JOB was also unlocked.

Offline: real threads and the real job registry, with a worker that blocks on an event
instead of doing provider work. What cannot be exercised here is a worker that ignores
its cancel flag for longer than _JOB_STOP_WAIT in the wild — the wait is shortened for
the test, and the branch it takes is the same one.
"""
import os
import sys
import threading
import time

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-apicleanup-j-")
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
#  1. a delete never races the worker whose rows it is deleting
# --------------------------------------------------------------------------- #
def part1():
    print("1. deleting a running job stops the worker FIRST")
    rid = aimeta.scan_new("test", ["a", "b", "c"], 0, 1, None)
    jid = "aimeta:%d" % rid
    release = threading.Event()
    still_writing = []

    def worker(cancel):
        while not cancel.is_set():
            time.sleep(0.01)
        release.wait(5)                     # keep "working" after seeing the cancel
        # if the delete did not wait, the run row is already gone by now
        still_writing.append(bool(aimeta.scan_get(rid)))

    cancel = threading.Event()
    t = threading.Thread(target=worker, args=(cancel,), daemon=True)
    rec = {"kind": "aimeta", "label": "t", "cancel": cancel, "thread": t,
           "error": None, "run_id": rid, "cancelable": True, "started": time.time()}
    app._JOBS[jid] = rec
    t.start()

    saved_wait = app._JOB_STOP_WAIT
    app._JOB_STOP_WAIT = 0.4                # the worker will NOT stop inside this
    try:
        err = None
        try:
            app._delete_one_job(jid)
        except HTTPException as e:
            err = e
        check("a worker that has not stopped yet refuses the delete",
              isinstance(err, HTTPException) and err.status_code == 409)
        check("and the scan row is still there", aimeta.scan_get(rid) is not None)

        release.set()                       # let the worker finish
        t.join(5)
        check("the worker still saw its run while it was running",
              still_writing == [True])
        check("once the worker is gone the delete goes through",
              app._delete_one_job(jid) and aimeta.scan_get(rid) is None)
    finally:
        app._JOB_STOP_WAIT = saved_wait
        app._JOBS.pop(jid, None)


# --------------------------------------------------------------------------- #
#  2. a resume resumes the scan the user actually ran
# --------------------------------------------------------------------------- #
def part2():
    print("\n2. resuming a scan keeps its options and closes the original run")
    started = []
    real = app._start_aimeta_job

    def fake_start(run_id, keys, opts):
        real(run_id, keys, opts)            # keep the opts memo honest
        app._JOBS.pop("aimeta:%d" % run_id, None)
        started.append((run_id, list(keys), dict(opts)))

    app._start_aimeta_job = fake_start
    try:
        rid = aimeta.scan_new("wand", ["k1", "k2", "k3", "k4"], 0, 1, None, 1)
        # the user ran it with media OFF; scan_runs has no column for that
        app._start_aimeta_job(rid, ["k1", "k2", "k3", "k4"],
                              {"web": False, "match_provider": True,
                               "metadata_kinds": None, "want_media": False,
                               "pull_scores": True, "label": "wand"})
        aimeta.scan_progress(rid, 2, 0)
        started.clear()

        res = app.jobs_restart("aimeta:%d" % rid)
        check("the resume started a new run", len(started) == 1)
        new_rid, new_keys, new_opts = started[0]
        check("it picks up exactly where the old one stopped", new_keys == ["k3", "k4"])
        check("MEDIA STAYS OFF — the user turned it off", new_opts.get("want_media") is False)
        check("and the heavy score pass is carried over too",
              new_opts.get("pull_scores") is True)
        check("the response names the new run", res["id"] == "aimeta:%d" % new_rid)

        old = aimeta.scan_get(rid)
        check("the original run is closed", old["status"] == "resumed")
        feed = {j["id"]: j for j in app._jobs_list()}
        check("and it is no longer offered for resume",
              feed["aimeta:%d" % rid]["restartable"] is False)
        check("while the new run is listed", ("aimeta:%d" % new_rid) in feed)
    finally:
        app._start_aimeta_job = real


# --------------------------------------------------------------------------- #
#  3. a publish is a job like any other
# --------------------------------------------------------------------------- #
def part3():
    print("\n3. a publish is visible in the job feed")
    app._PUBLISH_JOB["job"] = {"running": True, "device_id": 1, "done": 3, "total": 9,
                               "current": "Doom", "report": None, "error": "",
                               "started": time.time()}
    feed = {j["id"]: j for j in app._jobs_list()}
    check("a running publish is in the feed", "publish" in feed)
    check("it reports progress", feed["publish"]["progress"]["done"] == 3)
    check("a running publish cannot be dismissed", feed["publish"]["deletable"] is False)
    app._delete_one_job("publish")
    check("and dismissing one anyway does not drop the live job",
          app._PUBLISH_JOB["job"] is not None)

    app._PUBLISH_JOB["job"]["running"] = False
    feed = {j["id"]: j for j in app._jobs_list()}
    check("a finished publish is dismissable", feed["publish"]["deletable"] is True)
    app._delete_one_job("publish")
    check("and dismissing it clears it", app._PUBLISH_JOB["job"] is None)


# --------------------------------------------------------------------------- #
#  4. the deferred member backlog is drained, not forgotten
# --------------------------------------------------------------------------- #
def part4():
    print("\n4. members past the ingest cap come back on the next run")
    import catalog_patch
    import sqlite3
    con = sqlite3.connect(app.LIBRARY_DB)
    catalog_patch._ensure_queue(con)
    con.execute("DELETE FROM member_ingest_queue")
    for i in range(5):
        nk = "member%d" % i
        con.execute("INSERT INTO games(canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,n_sources,n_kinds,sources_summary,wanted) "
                    "VALUES(?,?,'pc',?,?,?,1,0,'via:bundle',0)",
                    (nk, nk, nk + "@pc", nk, "title:" + nk))
        con.execute("INSERT INTO member_ingest_queue(norm_key,platform,queued_at) "
                    "VALUES(?,'pc',?)", (nk, i))
    con.commit()
    con.close()

    first = app._take_pending_members(cap=2)
    check("a capped take hands back exactly the cap", len(first) == 2)
    check("oldest first", [k for k, _p in first] == ["member0", "member1"])
    app._mark_members_ingested(first)

    second = app._take_pending_members(cap=2)
    check("THE NEXT RUN GETS THE DEFERRED ONES, not the same slice",
          [k for k, _p in second] == ["member2", "member3"])
    app._mark_members_ingested(second)
    third = app._take_pending_members(cap=2)
    check("and the last of them", [k for k, _p in third] == ["member4"])
    app._mark_members_ingested(third)
    check("then the backlog is empty", app._take_pending_members(cap=2) == [])


def main():
    print("audit cleanup: job lifetimes")
    part1()
    part2()
    part3()
    part4()
    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
