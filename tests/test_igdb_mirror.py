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
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

    # The store join table: 700 rows, and row 3 is deliberately malformed (no game),
    # because IGDB really does return those and a sweep that trips over one is a
    # sweep that stops 600k rows early.
    EXT = [{"id": i, "game": (i % 300) + 1, "uid": "app%d" % i,
            "external_game_source": 1 + (i % 3), "name": "Store Row %d" % i}
           for i in range(1, 701)]
    EXT[2] = {"id": 3, "uid": "orphan", "external_game_source": 1}

    def fake_query(endpoint, body, cid, tok, retries=4, reauth=None):
        state["calls"] += 1
        if endpoint == "platforms":
            return [] if "id > 0" not in body else [
                {"id": 6, "name": "PC (Microsoft Windows)", "abbreviation": "PC",
                 "platform_type": {"name": "Operating_system"}}]
        if endpoint == "external_game_sources":
            return [{"id": 1, "name": "Steam"}, {"id": 2, "name": "GOG"},
                    {"id": 3, "name": "Epic"}]
        import re
        if endpoint == "external_games":
            cur = int(re.search(r"id > (\d+)", body).group(1))
            if state["throttle_after"] and state["calls"] > state["throttle_after"]:
                igdb._throttled[0] += 1
            return [r for r in EXT if r["id"] > cur][:M.PAGE]
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
    print("9. the store-id sweep resumes on its OWN cursor, not the games cursor")
    # Two sweeps sharing one cursor key is a bug that looks like success: each run
    # appears to work while silently restarting or skipping the other's progress.
    con = M.con_db()
    M.put(con, "cursor", 12345)
    con.commit(); con.close()
    e1 = M.sweep_external(max_requests=1, progress=False)
    check("one page of store ids", e1["rows_seen"] == M.PAGE)
    check("its own cursor advanced", M.status()["ext_cursor"] == 500)
    check("the games cursor was untouched", M.status()["cursor"] == 12345)
    e2 = M.sweep_external(progress=False)
    check("the rest arrived", e2["rows_seen"] == 200)
    st = M.status()
    check("ext cursor reset once exhausted", st["ext_cursor"] == 0)
    # 700 rows minus the one with no game. A row IGDB gives us half-filled is skipped,
    # not stored with a null game_id that would later join to nothing.
    check("the malformed row was skipped, not stored: %d" % st["external_ids"],
          st["external_ids"] == 699)
    con = M.con_db()
    nm = con.execute("SELECT name FROM stores WHERE id=1").fetchone()["name"]
    # The production lookup, exactly: a store id in hand -> which IGDB game is it.
    hit = con.execute("SELECT game_id FROM external_ids WHERE source_id=1 AND uid=?",
                      ("app300",)).fetchone()
    con.close()
    check("store names came with them", nm == "Steam")
    check("a store id resolves to its game: %r" % (hit and hit[0]),
          hit and hit["game_id"] == 1)

    print()
    print("10. the store sweep obeys the SAME cooldown the games sweep sets")
    # Both sweeps hit one rate limit. A cooldown earned by one that the other ignores
    # is not a cooldown.
    con = M.con_db()
    M.put(con, "cooldown_until", time.time() + 600)
    con.commit(); con.close()
    before_calls = state["calls"]
    r = M.sweep_external(progress=False)
    check("it declined", r.get("skipped") == "cooldown")
    check("and made no requests", state["calls"] == before_calls)

    print()
    print("11. platform type/family/generation survive an OLD db missing the columns")
    # CREATE TABLE IF NOT EXISTS is a no-op, not a migration: a mirror built by an
    # earlier version has a 3-column platforms table and must not crash on open.
    con = M.con_db()
    M.put(con, "cooldown_until", 0)
    con.execute("DROP TABLE platforms")
    con.execute("CREATE TABLE platforms(id INTEGER PRIMARY KEY, name TEXT, "
                "abbreviation TEXT)")
    con.commit(); con.close()
    con = M.con_db()                       # the heal happens here
    cols = {r[1] for r in con.execute("PRAGMA table_info(platforms)")}
    con.close()
    check("the new columns were added to the existing table",
          {"platform_type", "platform_family", "generation",
           "alternative_name"} <= cols)
    # ...and an empty/untyped platform table is re-fetched rather than left blank.
    M.sweep(progress=False)
    con = M.con_db()
    pt = con.execute("SELECT platform_type FROM platforms WHERE id=6").fetchone()
    con.close()
    check("and the type was populated: %r" % (pt and pt[0]),
          pt and pt[0] == "Operating_system")

    print()
    print("12. the platform join table is queryable, not just a csv")
    con = M.con_db()
    n = con.execute("SELECT COUNT(*) FROM game_platforms WHERE platform_id=6"
                    ).fetchone()[0]
    con.close()
    check("every game joined to its platform: %d" % n, n == 1200)

    print()
    print("13. the exact release date is KEPT, not truncated to a year on the way in")
    # The timestamp arrives in the same payload the year is derived from. Storing only
    # the year threw away month and day for free, and getting them back cost a full
    # re-sweep — so the raw value is persisted and the year stays a derived convenience.
    CATALOG[11]["first_release_date"] = 858124800      # 1997-03-12 UTC
    CATALOG[11]["updated_at"] = int(time.time()) + 9
    M.sweep(progress=False)
    con = M.con_db()
    r = con.execute("SELECT year, first_release_date FROM games WHERE id=11").fetchone()
    con.close()
    check("the year is still there: %r" % r["year"], r["year"] == 1997)
    check("and so is the full timestamp: %r" % r["first_release_date"],
          r["first_release_date"] == 858124800)
    check("which recovers the day, not just the year: %s"
          % time.strftime("%Y-%m-%d", time.gmtime(r["first_release_date"])),
          time.strftime("%Y-%m-%d", time.gmtime(r["first_release_date"]))
          == "1997-03-12")
    check("a game with no date stores null, not epoch 0",
          con_null(M) is None)

    print()
    print("%d checks, all passed" % len(PASS))


def con_null(M):
    """first_release_date for a record IGDB has no date for. 0 and NULL are different
    claims: one says 1970, the other says unknown."""
    con = M.con_db()
    r = con.execute("SELECT first_release_date FROM games WHERE id=12").fetchone()
    con.close()
    return r["first_release_date"]


if __name__ == "__main__":
    main()
