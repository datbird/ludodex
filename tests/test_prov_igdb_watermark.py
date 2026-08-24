#!/usr/bin/env python3
"""An incremental sweep is only ever as good as its watermark, and both of IGDB's
sweeps got theirs wrong in the same shape: they answered "what have I seen?" with a
timestamp that describes a DIFFERENT window from the one they actually covered.

  * A RESUMED GAMES SWEEP advanced the watermark to the resumed RUN's start. Run 1
    starts at T1 and walks ids 0..C with `updated_at >= W0` before it is interrupted.
    Run 2 starts at T2, resumes from C with the same filter, finishes, and wrote
    `watermark = T2`. A row with id < C that was edited between T1 and T2 was invisible
    to run 1 (it walked past that id before the edit), is below run 2's cursor, and the
    next sweep asks for `>= T2`. It is lost until a --full rebuild. The watermark
    belongs to the run that set the cursor to 0.
  * THE STORE-ID SWEEP HAD NO WATERMARK AT ALL. `ext_cursor` resets to 0 on completion,
    the query carried no `updated_at` filter and the rows went in with INSERT OR IGNORE
    — so every `--external` run re-pulled all ~1,352 pages and still could not update a
    pairing that had changed. The file's own comment says re-pulling costs 1,352
    requests.

No network: igdb.query is replaced, and the clock is a variable so the windows are exact.
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
    test_support.isolate("ludodex-prov-igdbwm-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import igdb
    import igdb_mirror as M

    NOW = [1000.0]
    M.time.time = lambda: NOW[0]
    M.time.sleep = lambda s: None

    CATALOG = {i: {"id": i, "name": "Game %d" % i, "slug": "game-%d" % i,
                   "game_type": 0, "updated_at": 100, "platforms": [6],
                   "first_release_date": 0}
               for i in range(1, 901)}
    EXT = [{"id": i, "game": i, "uid": "app%d" % i, "external_game_source": 1,
            "name": "Store Row %d" % i, "updated_at": 100} for i in range(1, 901)]
    seen = {"since": [], "ext_since": []}

    import re

    def fake_query(endpoint, body, cid, tok, retries=4, reauth=None):
        if endpoint == "platforms":
            return []
        if endpoint == "external_game_sources":
            return [{"id": 1, "name": "Steam"}]
        cur = int(re.search(r"id > (\d+)", body).group(1))
        m = re.search(r"updated_at >= (\d+)", body)
        since = int(m.group(1)) if m else 0
        if endpoint == "external_games":
            seen["ext_since"].append(since)
            rows = [r for r in EXT if r["id"] > cur and r["updated_at"] >= since]
        else:
            seen["since"].append(since)
            rows = [g for g in CATALOG.values()
                    if g["id"] > cur and g["updated_at"] >= since]
        rows.sort(key=lambda g: g["id"])
        return rows[:M.PAGE]

    igdb.query = fake_query
    M._auth = lambda: ("cid", "csec", "tok")

    print("1. a full sweep sets the watermark to its own start")
    NOW[0] = 1000
    M.sweep(full=True, progress=False)
    check("watermark is the run's start: %s" % M.status()["watermark"],
          int(M.status()["watermark"]) == 1000)
    check("and every row is mirrored", M.status()["games"] == 900)

    print()
    print("2. AN INTERRUPTED RUN'S WINDOW IS NOT CLOSED BY THE RUN THAT RESUMES IT")
    # Everything was touched upstream at T=1500, so the incremental pass has real work.
    for g in CATALOG.values():
        g["updated_at"] = 1500
    # Run B covers ids 1..500 as they stood at T=2000, then stops.
    NOW[0] = 2000
    b = M.sweep(max_requests=1, progress=False)
    check("it stopped part-way, with a durable cursor: %d" % b["cursor"],
          b["cursor"] == M.PAGE)
    check("the watermark did NOT move — the pass is not finished",
          int(M.status()["watermark"]) == 1000)

    # An edit lands at T=2500: after run B walked past id 7, before run C starts.
    CATALOG[7]["updated_at"] = 2500
    CATALOG[7]["name"] = "Game 7 (edited mid-sweep)"

    NOW[0] = 3000
    c = M.sweep(progress=False)
    check("run C finished the pass it resumed: cursor back to 0",
          int(M.status()["cursor"]) == 0 and c["rows_seen"] == 400)
    wm = int(M.status()["watermark"])
    check("the watermark is the START OF THE PASS (2000), not of the run that "
          "finished it (3000): %d" % wm, wm == 2000)

    print()
    print("3. ...so the edit run C could not see is still asked for next time")
    con = M.con_db()
    nm = con.execute("SELECT name FROM games WHERE id=7").fetchone()["name"]
    con.close()
    check("run C genuinely missed it (id 7 is below its cursor)",
          nm == "Game 7")
    NOW[0] = 4000
    M.sweep(progress=False)
    con = M.con_db()
    nm = con.execute("SELECT name FROM games WHERE id=7").fetchone()["name"]
    con.close()
    check("the next sweep picked the edited row up: %r" % nm,
          nm == "Game 7 (edited mid-sweep)")

    print()
    print("4. THE STORE-ID SWEEP HAS A WATERMARK TOO")
    NOW[0] = 5000
    e1 = M.sweep_external(progress=False)
    check("it pulled the whole join table: %d rows" % e1["rows_seen"],
          e1["rows_seen"] == 900)
    check("and recorded the window it covered: %s"
          % M.status().get("ext_watermark"),
          int(M.status().get("ext_watermark") or 0) == 5000)

    print()
    print("5. a re-run asks only for what CHANGED, instead of 1,352 pages again")
    EXT[3]["name"] = "Store Row 4 (renamed)"
    EXT[3]["updated_at"] = 5500
    NOW[0] = 6000
    e2 = M.sweep_external(progress=False)
    check("only the changed row came back: %d" % e2["rows_seen"],
          e2["rows_seen"] == 1)
    check("it cost 2 requests, not the whole table: %d" % e2["requests"],
          e2["requests"] <= 3)
    check("the filter really was sent: %s" % seen["ext_since"][-1],
          seen["ext_since"][-1] == 5000)

    print()
    print("6. and a CHANGED pairing is updated, not ignored")
    # INSERT OR IGNORE meant a row that already existed could never be corrected.
    con = M.con_db()
    nm = con.execute("SELECT name FROM external_ids WHERE game_id=4").fetchone()["name"]
    con.close()
    check("the store row's new name landed: %r" % nm, nm == "Store Row 4 (renamed)")

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
