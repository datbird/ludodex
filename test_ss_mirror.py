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
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
    state["quota"]["requeststoday"] = 100000 - M.DAILY_RESERVE - 10
    r = M.walk(progress=False)
    check("only the 10 remaining requests were spent: %d" % r["requests"],
          r["requests"] == 10)
    state["quota"]["requeststoday"] = 100000 - M.DAILY_RESERVE
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
    print("9. a quota error mid-walk stops the run and does NOT advance the cursor")
    con = M.con_db()
    M.put(con, "cooldown_until", 0)
    con.commit()
    cur_before = int(M.get(con, "cursor", 0))
    con.close()
    state["quota"]["requeststoday"] = 0
    state["raise_kind"], state["raise_at"] = "quota", state["calls"] + 5
    r = M.walk(max_requests=600, progress=False)
    check("it stopped on quota", r["stopped"] == "quota")
    check("the cursor did not move past the failed block: %d -> %d"
          % (cur_before, r["cursor"]), r["cursor"] == cur_before)
    check("a cooldown was set", M.status()["cooldown_until"] > time.time())

    print()
    print("10. bad credentials stop immediately rather than burning the day")
    con = M.con_db()
    M.put(con, "cooldown_until", 0)
    con.commit(); con.close()
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
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
