#!/usr/bin/env python3
"""The mirror's job is to survive being interrupted, throttled and re-run.

None of that is exercised by a happy-path fetch, so this drives the sweep against a
fake IGDB: pages come from a dict, and the fake can be told to throttle, to run out
mid-sweep, or to change a record between runs.

The three properties that matter, none of which are obvious:

  * KEYSET, NOT OFFSET. A run stopped after N requests must resume at exactly the
    id it reached — that is what makes a 371k-row build safe to do in chunks.
  * THE WATERMARK IS THE RUN'S START, not the newest `updated_at` it saw. A record
    edited mid-sweep sits in the gap between "already passed that id" and "newer
    than anything I've seen", so a finish-time watermark would lose it forever.
    Overlapping re-fetches a few rows; gapping loses them.
  * ALT NAMES ARE REPLACED, NOT MERGED. IGDB dropping an alias has to drop it here,
    or the mirror keeps matching on a name the source has disowned.
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
    test_support.isolate("ludodex-mirror-")
    import igdb
    import igdb_mirror as M

    # ---- a fake IGDB -------------------------------------------------------- #
    CATALOG = {}
    for i in range(1, 1201):
        CATALOG[i] = {"id": i, "name": "Game %d" % i, "slug": "game-%d" % i,
                      "game_type": 0, "updated_at": 1000,
                      "platforms": [6], "first_release_date": 0,
                      "alternative_names": [{"name": "Alias %d" % i}]}
    state = {"throttle_after": None, "calls": 0}

    def fake_query(endpoint, body, cid, tok, retries=4, reauth=None):
        state["calls"] += 1
        if endpoint == "platforms":
            return [] if "id > 0" not in body else [
                {"id": 6, "name": "PC (Microsoft Windows)", "abbreviation": "PC"}]
        import re
        cur = int(re.search(r"id > (\d+)", body).group(1))
        since = re.search(r"updated_at >= (\d+)", body)
        since = int(since.group(1)) if since else 0
        rows = [g for g in CATALOG.values()
                if g["id"] > cur and g["updated_at"] >= since]
        rows.sort(key=lambda g: g["id"])
        if state["throttle_after"] and state["calls"] > state["throttle_after"]:
            igdb._throttled[0] += 1          # what a real 429 would have done
        return rows[:M.PAGE]

    igdb.query = fake_query
    M._auth = lambda: ("cid", "csec", "tok")

    print("1. a bounded run stops cleanly and REMEMBERS where it stopped")
    r1 = M.sweep(full=True, max_requests=1, progress=False)
    check("one request, one page", r1["requests"] == 1 and r1["rows_seen"] == M.PAGE)
    check("cursor advanced to the last id of that page", r1["cursor"] == 500)
    st = M.status()
    check("cursor is persisted, not just returned", st["cursor"] == 500)

    print()
    print("2. re-running resumes at the cursor rather than starting over")
    r2 = M.sweep(max_requests=1, progress=False)
    check("the next page starts after 500", r2["cursor"] == 1000)
    check("no row was fetched twice", M.status()["games"] == 1000)

    print()
    print("3. running to exhaustion completes and sets the watermark")
    before = int(time.time())
    r3 = M.sweep(progress=False)
    st = M.status()
    check("every row is mirrored", st["games"] == 1200)
    check("alt names came with them", st["alt_names"] == 1200)
    check("the platform table was fetched", st["platforms"] == 1)
    check("cursor reset for the next sweep", st["cursor"] == 0)
    check("watermark set at or after the run's start", st["watermark"] >= before)

    print()
    print("4. the watermark is the run's START, so a mid-sweep edit is not lost")
    # This row was edited DURING the sweep above: its updated_at is older than the
    # run finished, but newer than the run began. A finish-time watermark would skip
    # it forever; a start-time watermark re-reads it.
    CATALOG[7]["updated_at"] = st["watermark"] + 1
    CATALOG[7]["name"] = "Game 7 (edited)"
    M.sweep(progress=False)
    con = M.con_db()
    got = con.execute("SELECT name FROM games WHERE id=7").fetchone()["name"]
    con.close()
    check("the edited record was picked up: %r" % got, got == "Game 7 (edited)")

    print()
    print("5. an alias IGDB drops is dropped here too")
    CATALOG[9]["alternative_names"] = []
    CATALOG[9]["updated_at"] = int(time.time()) + 5
    M.sweep(progress=False)
    con = M.con_db()
    n = con.execute("SELECT COUNT(*) FROM alt_names WHERE game_id=9").fetchone()[0]
    con.close()
    check("the removed alias is gone, not merged", n == 0)

    print()
    print("6. sustained throttling stops the run and sets a cooldown")
    con = M.con_db()
    M.put(con, "cursor", 0)
    M.put(con, "watermark", 0)
    con.commit()
    con.close()
    state["calls"], state["throttle_after"] = 0, 1
    igdb._throttled[0] = 0
    # The fake catalog is 1,200 rows = 3 pages, so a 6-page streak cannot happen
    # here. Lower the bar rather than invent 3,000 fake rows: what is under test is
    # that CONSECUTIVE throttled pages trip a cooldown, not the specific number.
    M.COOLDOWN_AFTER = 2
    M.sweep(progress=False)
    st = M.status()
    check("a cooldown was recorded", st["cooldown_until"] > time.time())
    check("the pace was backed off, not left at the ceiling", st["pace"] > M.TARGET_PACE)

    print()
    print("7. a run inside the cooldown refuses instead of hammering")
    before_calls = state["calls"]
    r = M.sweep(progress=False)
    check("it declined", r.get("skipped") == "cooldown")
    check("and made no requests at all", state["calls"] == before_calls)

    print()
    print("8. no caller can pace below IGDB's documented ceiling")
    con = M.con_db()                          # clear the cooldown test 6 just set
    M.put(con, "cooldown_until", 0)
    M.put(con, "pace", M.TARGET_PACE)
    con.commit(); con.close()
    state["throttle_after"] = None
    M.sweep(pace=0.0001, progress=False)      # ask for absurdly fast
    check("the sweep clamped the pace to the ceiling (%.3fs)" % igdb._pace[0],
          igdb._pace[0] >= M.TARGET_PACE)

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
