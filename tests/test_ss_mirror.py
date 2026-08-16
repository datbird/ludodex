#!/usr/bin/env python3
"""ScreenScraper's id space is sparse and its quota is a hard daily wall, so the walk
is defined entirely by how it handles absence and how it stops.

The properties that are easy to get wrong, and none of which a happy-path fetch shows:

  * A HOLE IS NOT THE END. Most ids are empty. A walk that treats "no game here" as a
    failure stops on id 4 — which is exactly what the first hand-probe of this mirror
    did, concluding the catalog held 3 games.
  * THE CURSOR ADVANCES A WHOLE BLOCK. Requests run on 6 threads and finish out of
    order; advancing the cursor per result leaves holes a resume never revisits.
  * QUOTA IS THE SERVER'S NUMBER. A local counter drifts as soon as anything else
    scrapes on the same account, and the reserve exists so a multi-day walk does not
    starve the user's own scraping.
  * HASHES ARE THE POINT. A rom with no hash indexes nothing and is not stored.
"""
import os
import sys
import time

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "ludodex"))
    import test_support
    test_support.isolate("ludodex-ssmirror-")
    import config
    import screenscraper as ss
    import ss_mirror as M

    # ---- a fake ScreenScraper ---------------------------------------------- #
    # Deliberately sparse: only every third id is real, which is close to the live
    # density and guarantees the walk meets holes constantly.
    CATALOG = {}
    for i in range(1, 901):
        if i % 3:
            continue
        CATALOG[i] = {
            "id": str(i), "systeme": {"id": "1"},
            "noms": [{"region": "us", "text": "Game %d" % i},
                     {"region": "jp", "text": "Geemu %d" % i}],
            "dates": [{"region": "us", "text": "1995-03-04"}],
            "editeur": "Pub", "developpeur": "Dev",
            "roms": [{"romfilename": "game%d.md" % i, "romcrc": "AABB%04d" % i,
                      "rommd5": "d%030d" % i, "romsha1": "s%038d" % i,
                      "romsize": 524288, "romregions": "us"},
                     {"romfilename": "nohash%d.md" % i}],   # must NOT be stored
        }
    state = {"calls": 0, "quota": {"maxthreads": 3, "requeststoday": 0,
                                   "maxrequestsperday": 100000, "favregion": "us",
                                   "maxrequestspermin": 7168, "level": "1"},
             "raise_kind": None, "raise_at": None}

    config.screenscraper_creds = lambda: {"devid": "d", "devpassword": "p",
                                          "softname": "ludodex", "ssid": "u",
                                          "sspassword": "q"}
    ss.user_info = lambda creds: dict(state["quota"])

    def fake_jeu_infos(creds, gameid=None, **kw):
        state["calls"] += 1
        if state["raise_at"] and state["calls"] >= state["raise_at"]:
            if state.get("spend_on_raise"):
                state["quota"]["requeststoday"] = state["spend_on_raise"]
            raise ss.SSError(state["raise_kind"], "injected")
        return CATALOG.get(int(gameid)), {}

    ss.jeu_infos = fake_jeu_infos
    ss._request = lambda ep, creds, extra=None, **kw: (
        {"systemes": [{"id": "1", "noms": {"nom_us": "Sega Genesis"},
                       "compagnie": "Sega", "type": "console"},
                      {"id": "999", "noms": {"nom_us": "Nonexistent Machine"}}]}
        if ep == "systemesListe.php" else {})

    print("1. holes are normal — the walk does not stop on an empty id")
    r = M.walk(max_requests=120, progress=False)
    check("it kept going past the empty ids", r["cursor"] == 120)
    check("and found only the real ones: %d" % r["games_found"],
          r["games_found"] == 40)
    check("stopped on budget, not on a hole", r["stopped"] == "budget")

    print()
    print("2. it resumes exactly where it stopped")
    r2 = M.walk(max_requests=120, progress=False)
    check("the next run starts after 120", r2["cursor"] == 240)
    check("nothing was re-walked", M.status()["games"] == 80)

    print()
    print("3. every regional name is kept, so an alias can match offline")
    con = M.con_db()
    names = {r["name"] for r in con.execute(
        "SELECT name FROM ss_names WHERE game_id=3")}
    con.close()
    check("both regions stored: %s" % sorted(names),
          names == {"Game 3", "Geemu 3"})

    print()
    print("4. hashes are stored; a rom with NO hash indexes nothing and is dropped")
    con = M.con_db()
    roms = con.execute("SELECT filename,crc,md5,sha1 FROM ss_roms WHERE game_id=3"
                       ).fetchall()
    con.close()
    check("one hashed rom kept, the unhashed one dropped: %d" % len(roms),
          len(roms) == 1)
    check("the crc is lowercased for joining", roms[0]["crc"] == "aabb0003")
    check("md5 and sha1 came too",
          roms[0]["md5"].startswith("d") and roms[0]["sha1"].startswith("s"))

    print()
    print("5. identity fields survive the trip")
    con = M.con_db()
    g = con.execute("SELECT * FROM ss_games WHERE id=3").fetchone()
    con.close()
    check("name", g["name"] == "Game 3")
    check("year parsed from the date: %r" % g["year"], g["year"] == 1995)
    check("system id", g["systeme"] == 1)
    check("rom count recorded", g["n_roms"] == 2)

    print()
    print("6. the SS system map binds to IGDB where a platform exists")
    st = M.status()
    check("both systems recorded", st["systems"] == 2)
    # No IGDB mirror exists in the isolated dir, so nothing can map — the point under
    # test is that a missing counterpart is left NULL rather than guessed at.
    check("an unmappable system is left null, not invented",
          st["systems_mapped"] == 0)

    print()
    print("7. the daily quota is the SERVER's number, minus the reserve")
    # The reserve is COMPUTED from the tier now, so the test asks for it rather than
    # assuming a constant — which is the same reason the code stopped assuming one.
    reserve = M.tier_limits(state["quota"])["reserve"]
    state["quota"]["requeststoday"] = 100000 - reserve - 10
    r = M.walk(progress=False)
    check("only the 10 remaining requests were spent: %d" % r.get("requests"),
          r.get("requests") == 10)
    state["quota"]["requeststoday"] = 100000 - reserve
    r = M.walk(progress=False)
    check("at the reserve line it declines outright", r.get("skipped") == "quota")
    check("and sets a cooldown so the next run does not hammer",
          M.status()["cooldown_until"] > time.time())

    print()
    print("8. a run inside the cooldown refuses without spending a request")
    before = state["calls"]
    r = M.walk(progress=False)
    check("it declined", r.get("skipped") == "cooldown")
    check("no requests made", state["calls"] == before)

    print()
    print("9. REAL quota exhaustion mid-walk stops the run and does NOT advance the cursor")
    con = M.con_db()
    M.put(con, "cooldown_until", 0)
    con.commit()
    cur_before = int(M.get(con, "cursor", 0))
    con.close()
    # The counter has to say the day IS spent: since a 429 and daily exhaustion arrive
    # as the same error kind, the ssuser numbers are what tells them apart. With the
    # counter at zero this same injection is a throttle, which test 17 asserts.
    # Run out DURING the run, which is the only way both checks are exercised: the
    # budget check at the start must pass, and the mid-run check must then find the
    # counter genuinely spent. A static value cannot be both.
    state["quota"]["requeststoday"] = 0
    state["raise_kind"], state["raise_at"] = "quota", state["calls"] + 5
    state["spend_on_raise"] = 99999
    r = M.walk(max_requests=600, progress=False)
    check("it stopped on quota", r["stopped"] == "quota")
    check("the cursor did not move past the failed block: %d -> %d"
          % (cur_before, r["cursor"]), r["cursor"] == cur_before)
    check("a cooldown was set", M.status()["cooldown_until"] > time.time())
    state["spend_on_raise"] = None

    print()
    print("10. bad credentials stop immediately rather than burning the day")
    con = M.con_db()
    M.put(con, "cooldown_until", 0)
    con.commit(); con.close()
    state["quota"]["requeststoday"] = 0        # test 9 left the day spent
    state["raise_kind"], state["raise_at"] = "badcreds", state["calls"] + 1
    r = M.walk(max_requests=600, progress=False)
    check("stopped on badcreds", r["stopped"] == "badcreds")
    check("it did not spend the whole budget: %d" % r["requests"],
          r["requests"] < 600)

    print()
    print("11. the walk ends on a long RUN of dead ids, never on one")
    state["raise_kind"], state["raise_at"] = None, None
    con = M.con_db()
    M.put(con, "cursor", M.TOP_ID_SEEN)      # past everything the fake catalog holds
    M.put(con, "dead_run", 0)
    M.put(con, "cooldown_until", 0)
    con.commit(); con.close()
    r = M.walk(max_requests=M.DEAD_RUN_STOP + M.BLOCK * 2, progress=False)
    check("it declared the catalog exhausted", r["stopped"] == "exhausted")
    check("only after a long dead run, not the first hole",
          r["requests"] >= M.DEAD_RUN_STOP)
    check("and recorded that the walk completed", M.status()["walk_complete"])

    print()
    print("12. the account's OWN limits decide, and config may only narrow them")
    # The portability property. A scraper tuned to its author's contributor tier is a
    # scraper that throttles or bans the next person who runs it.
    free = {"maxthreads": 1, "maxrequestsperday": 20000, "maxrequestspermin": 60}
    paid = {"maxthreads": 6, "maxrequestsperday": 100000, "maxrequestspermin": 7168}

    f, p = M.tier_limits(free), M.tier_limits(paid)
    check("a free account gets its 1 thread", f["threads"] == 1)
    check("a contributor account gets its 6", p["threads"] == 6)
    check("the reserve SCALES with the quota: %d vs %d"
          % (f["reserve"], p["reserve"]),
          f["reserve"] == 1000 and p["reserve"] == 5000)
    # 5,000 held back from 20,000 would cost a free user a quarter of their day.
    check("and is a fraction, not a fixed number carried over from a big tier",
          f["reserve"] < 5000)

    print()
    print("13. the per-minute cap is honoured, not merely read")
    # It was previously read into the quota view and never used. Invisible on a
    # 7,168/min tier; the whole ballgame on 60/min.
    check("a free tier paces itself: %.2fs per block of 1"
          % f["min_block_seconds"], f["min_block_seconds"] >= 1.0)
    check("a contributor tier is effectively unpaced",
          p["min_block_seconds"] < 0.1)
    check("a server that reports no per-minute cap gets a timid default",
          M.tier_limits({"maxthreads": 2, "maxrequestsperday": 5000})["per_min"]
          == M.FALLBACK_PER_MIN)

    print()
    print("14. config narrows the tier; it can never widen it")
    import config as _cfg
    _cfg.set_("screenscraper_walk_threads", "8")
    try:
        check("asking for 8 on a 1-thread account still gives 1",
              M.tier_limits(free)["threads"] == 1)
        check("and on a 6-thread account still gives 6",
              M.tier_limits(paid)["threads"] == 6)
        _cfg.set_("screenscraper_walk_threads", "2")
        check("asking for FEWER is honoured", M.tier_limits(paid)["threads"] == 2)
    finally:
        _cfg.set_("screenscraper_walk_threads", "")
    check("cleared, it returns to what the account grants",
          M.tier_limits(paid)["threads"] == 6)

    print()
    print("15. a reserve bigger than the quota stops rather than going negative")
    tiny = M.tier_limits({"maxthreads": 1, "maxrequestsperday": 100,
                          "maxrequestspermin": 30})
    check("the reserve never exceeds the day: %d of 100" % tiny["reserve"],
          tiny["reserve"] <= 100)

    print()
    print("16. a quota wait is a RE-CHECK, never a guess at the upstream reset time")
    # The bug: parking until "next UTC midnight" gated the quota read behind the
    # cooldown, so a walk could not notice the reset it was waiting for. Observed live
    # holding 77,000 available requests with 20 hours still on the clock.
    con = M.con_db()
    M.put(con, "cooldown_until", 0)
    con.commit(); con.close()
    state["quota"]["requeststoday"] = 100000        # spent
    M.walk(progress=False)
    left = M.status()["cooldown_until"] - time.time()
    check("it waits minutes, not most of a day: %.0fm" % (left / 60),
          0 < left <= M.QUOTA_RECHECK_SECS + 60)
    check("and far less than a day", left < 86400 / 4)

    # ...and when the counter HAS reset, the next attempt proceeds rather than
    # sitting out a timer set before the reset happened.
    con = M.con_db()
    M.put(con, "cooldown_until", 0)                 # the re-check has come round
    con.commit(); con.close()
    state["quota"]["requeststoday"] = 0             # upstream reset
    r = M.walk(max_requests=60, progress=False)
    check("it resumes immediately once quota is back",
          r.get("skipped") != "quota" and r.get("requests"))

    print()
    print("17. a transient 429 is NOT the daily quota running out")
    # ScreenScraper reports both as kind='quota'. Six threads produce the occasional
    # throttle; treating one as exhaustion stopped a run with 77,000 requests left.
    con = M.con_db()
    M.put(con, "cooldown_until", 0)
    M.put(con, "cursor", 0)
    con.commit(); con.close()
    state["quota"]["requeststoday"] = 500        # nowhere near the limit
    state["raise_kind"], state["raise_at"] = "quota", state["calls"] + 3
    r = M.walk(max_requests=180, progress=False)
    check("it did NOT stop on quota: %s" % r.get("stopped"),
          r.get("stopped") != "quota")
    state["raise_kind"], state["raise_at"] = None, None

    print()
    print("18. ...but real exhaustion still stops it")
    con = M.con_db()
    M.put(con, "cooldown_until", 0)
    con.commit(); con.close()
    state["quota"]["requeststoday"] = 99999      # genuinely spent
    state["raise_kind"], state["raise_at"] = "quota", state["calls"] + 1
    r = M.walk(max_requests=180, progress=False)
    check("it stops", r.get("stopped") == "quota" or r.get("skipped") == "quota")
    state["raise_kind"], state["raise_at"] = None, None

    print()
    print("19. a COMPLETED walk probes forward again on the next run")
    # Otherwise "check every few months" is a lie: dead_run persists past the stop line,
    # so a re-run walks one block, finds it dead, and re-declares itself finished —
    # creeping 60 ids per invocation and never finding what was added since.
    con = M.con_db()
    M.put(con, "cursor", M.TOP_ID_SEEN + 100)
    M.put(con, "dead_run", M.DEAD_RUN_STOP + 500)     # as a finished walk leaves it
    M.put(con, "walk_complete", 1234567)
    M.put(con, "cooldown_until", 0)
    con.commit(); con.close()
    check("it starts out marked complete", M.status()["walk_complete"])
    state["quota"]["requeststoday"] = 0
    r = M.walk(max_requests=120, progress=False)
    check("the new run actually walked: %s requests" % r.get("requests"),
          r.get("requests") == 120)
    check("rather than instantly re-declaring itself done",
          r.get("stopped") != "exhausted")

    print()
    print("20. refreshing STALE games is how existing entries stay current")
    # The id walk finds NEW games and can never see a new ROM dump added to one we
    # already hold — which is ScreenScraper's most common change, and the one that
    # matters most for matching a file you just acquired.
    con = M.con_db()
    con.execute("UPDATE ss_games SET seen_at=0")      # everything is ancient
    con.commit(); con.close()
    state["quota"]["requeststoday"] = 0
    res = M.refresh_stale(days=1, max_requests=30, progress=False)
    check("it selected stale rows: %s" % res.get("stale_examined"),
          res.get("stale_examined") == 30)
    check("and re-fetched them", res.get("refreshed", 0) > 0)
    con = M.con_db()
    fresh = con.execute("SELECT COUNT(*) FROM ss_games WHERE seen_at > 0").fetchone()[0]
    con.close()
    check("their seen_at is no longer ancient: %d" % fresh, fresh > 0)

    print()
    print("21. refresh respects the same quota and cooldown as the walk")
    con = M.con_db()
    M.put(con, "cooldown_until", time.time() + 600)
    con.commit(); con.close()
    check("a cooldown blocks it",
          M.refresh_stale(days=1, progress=False).get("skipped") == "cooldown")
    con = M.con_db()
    M.put(con, "cooldown_until", 0)
    con.commit(); con.close()
    state["quota"]["requeststoday"] = 100000
    check("a spent quota blocks it",
          M.refresh_stale(days=1, progress=False).get("skipped") == "quota")
    state["quota"]["requeststoday"] = 0

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
