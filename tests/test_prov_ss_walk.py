#!/usr/bin/env python3
"""The ScreenScraper walk must never turn a TRANSIENT FAILURE into a permanent hole.

The walk's cursor is the only record of what has been covered. Everything here is about
the difference between the three answers an id can give, which the first version
collapsed into two:

  * A GAME. Stored.
  * NO GAME. A hole, normal, ~32% of the id space. The cursor may pass it forever.
  * NO ANSWER. Three timeouts, a 5xx, an HTML maintenance page, a 429. The id was never
    actually asked. Advancing the cursor past it loses the game FOREVER, because a walk
    only ever looks forward — which is exactly how 368 games ended up sitting BEHIND the
    cursor and had to be repaired by hand from the web UI.

So an errored id goes into a durable ledger and is re-asked, a block that could not
answer cannot count toward the dead-run exhaustion proof, and the request budget counts
the ATTEMPTS the transport actually made rather than the ids it was handed.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_support
    test_support.isolate("ludodex-prov-sswalk-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import config
    import screenscraper as ss
    import ss_mirror as M

    # ---- a fake ScreenScraper, sparse like the real one --------------------- #
    CATALOG = {}
    for i in range(1, 2001):
        if i % 3:
            continue
        CATALOG[i] = {
            "id": str(i), "systeme": {"id": "1"},
            "noms": [{"region": "us", "text": "Game %d" % i}],
            "dates": [{"region": "us", "text": "1995-03-04"}],
            "roms": [{"romfilename": "g%d.md" % i, "romcrc": "AABB%04d" % i}],
        }
    state = {"quota": {"maxthreads": 3, "requeststoday": 0,
                       "maxrequestsperday": 100000, "favregion": "us",
                       "maxrequestspermin": 7168, "level": "1"},
             "fail_ids": set(), "fail_kind": "error", "calls": 0}

    config.screenscraper_creds = lambda: {"devid": "d", "devpassword": "p",
                                          "softname": "ludodex", "ssid": "u",
                                          "sspassword": "q"}
    ss.user_info = lambda creds: dict(state["quota"])
    ss._request = lambda ep, creds, extra=None, **kw: (
        {"systemes": [{"id": "1", "noms": {"nom_us": "Sega Genesis"}}]}
        if ep == "systemesListe.php" else {})

    def fake_jeu_infos(creds, gameid=None, **kw):
        state["calls"] += 1
        gid = int(gameid)
        if gid in state["fail_ids"]:
            raise ss.SSError(state["fail_kind"], "injected")
        return CATALOG.get(gid), {}

    ss.jeu_infos = fake_jeu_infos

    def reset(cursor=0, dead=0):
        con = M.con_db()
        M.put(con, "cursor", cursor)
        M.put(con, "dead_run", dead)
        M.put(con, "cooldown_until", 0)
        M.put(con, "walk_complete", "")
        con.execute("DELETE FROM ss_gaps")
        con.commit()
        con.close()

    print("1. AN ID THAT COULD NOT ANSWER IS NOT AN ID WITHOUT A GAME")
    state["fail_ids"] = set(range(61, 121))          # exactly the second block
    reset()
    r = M.walk(max_requests=180, progress=False)
    check("the walk still made forward progress: cursor %d" % r["cursor"],
          r["cursor"] == 180)
    con = M.con_db()
    gaps = [row["id"] for row in con.execute("SELECT id FROM ss_gaps ORDER BY id")]
    con.close()
    check("every id that errored was written down: %d" % len(gaps), len(gaps) == 60)
    check("and they are the right ones", gaps[0] == 61 and gaps[-1] == 120)
    check("status reports the debt so a run is not mistaken for complete",
          M.status().get("gaps") == 60)
    con = M.con_db()
    missed = con.execute("SELECT COUNT(*) FROM ss_games WHERE id BETWEEN 61 AND 120"
                         ).fetchone()[0]
    con.close()
    check("nothing from that block was stored yet", missed == 0)

    print()
    print("2. and the NEXT run re-asks them, rather than walking past forever")
    state["fail_ids"] = set()
    r = M.walk(max_requests=180, progress=False)
    con = M.con_db()
    got = con.execute("SELECT COUNT(*) FROM ss_games WHERE id BETWEEN 61 AND 120"
                      ).fetchone()[0]
    left = con.execute("SELECT COUNT(*) FROM ss_gaps").fetchone()[0]
    con.close()
    check("the 20 real games in the failed block are now mirrored: %d" % got,
          got == 20)
    check("the ledger is empty again", left == 0)
    check("and the forward walk carried on too: cursor %d" % r["cursor"],
          r["cursor"] > 180)

    print()
    print("3. an id that is genuinely EMPTY is settled, not re-asked forever")
    # The ledger must record failures only. A hole answered "no game" is a fact.
    con = M.con_db()
    n = con.execute("SELECT COUNT(*) FROM ss_gaps").fetchone()[0]
    con.close()
    check("no ledger rows for the ~2/3 of ids with no game", n == 0)

    print()
    print("4. A FLAKY API CANNOT PROVE THE CATALOGUE HAS ENDED")
    # dead_run is the exhaustion proof. Counting an id that never answered as a dead id
    # is how a bad afternoon writes walk_complete over a walk that is 40% done.
    reset(cursor=M.TOP_ID_SEEN, dead=M.DEAD_RUN_STOP - 60)
    state["fail_ids"] = set(range(M.TOP_ID_SEEN, M.TOP_ID_SEEN + 200))
    r = M.walk(max_requests=120, progress=False)
    check("the run did not declare exhaustion: %s" % r.get("stopped"),
          r.get("stopped") != "exhausted")
    check("and the catalogue is NOT marked complete",
          not M.status().get("walk_complete"))
    check("the dead run did not grow on ids that never answered: %d"
          % M.status()["dead_run"], M.status()["dead_run"] == M.DEAD_RUN_STOP - 60)

    print()
    print("5. a real dead run still ends the walk")
    reset(cursor=M.TOP_ID_SEEN, dead=M.DEAD_RUN_STOP - 60)
    state["fail_ids"] = set()
    r = M.walk(max_requests=120, progress=False)
    check("exhaustion is still reachable", r.get("stopped") == "exhausted")
    check("and recorded", M.status().get("walk_complete"))

    print()
    print("6. RETRIES ARE VISIBLE TO THE BUDGET")
    # _request makes up to 3 attempts per id. Counting one request per id let a flaky
    # day send three times the computed budget against a hard daily quota.
    reset()
    state["fail_ids"] = set()

    def retrying_jeu_infos(creds, gameid=None, **kw):
        ss._count_attempt()                  # the two retries the transport really made
        ss._count_attempt()
        return fake_jeu_infos(creds, gameid=gameid, **kw)

    ss.jeu_infos = retrying_jeu_infos
    r = M.walk(max_requests=180, progress=False)
    check("the run covered FEWER ids than the budget, because the retries were paid "
          "for: %d ids, %d requests" % (r["cursor"], r["requests"]),
          r["cursor"] < 180 and r["requests"] > r["cursor"])
    # It cannot be exact — you only learn a retry happened by making it — but the
    # overshoot is now one block's worth, not the 3x a per-id count allowed.
    check("and it overran the budget by at most one block: %d" % r["requests"],
          r["requests"] <= 180 + M.BLOCK * 3)
    ss.jeu_infos = fake_jeu_infos

    print()
    print("7. the transport counts every attempt, retries included")
    before = ss.attempts_made()
    ss._count_attempt()
    check("attempts_made() moves", ss.attempts_made() == before + 1)

    print()
    print("8. AN OUTAGE IS BOUNDED, so the ledger cannot swallow a whole day")
    # Every id failing is not a reason to keep going: a run that spent its 95,000-request
    # budget on ids that never answered would owe 95,000 retries, and the ledger would
    # take a fortnight to drain at any sane rate. A block that answers NOTHING is a
    # strike whatever error it carried, so the run stops after CLOSED_STRIKES of them.
    real_sleep = M.time.sleep
    M.time.sleep = lambda s: None                  # the backoff, without the wait
    try:
        reset()
        state["fail_ids"] = set(range(1, 100000))
        r = M.walk(max_requests=6000, progress=False)
        check("it stopped instead of burning the budget: %r after %d requests"
              % (r.get("stopped"), r["requests"]),
              r.get("stopped") == "closed" and r["requests"] < 6000)
        check("and the debt is one drain's worth, not a day's: %d"
              % M.status()["gaps"], M.status()["gaps"] <= M.GAP_DRAIN)
    finally:
        M.time.sleep = real_sleep
        state["fail_ids"] = set()

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
