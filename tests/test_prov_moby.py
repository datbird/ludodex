"""MobyGames: 720 requests an HOUR, one shared window, and a walk that must not mistake
a bad answer for the end of a platform.

  * A 404 IS NOT AN EMPTY PLATFORM. `_get` returns None on a 404 because for a single
    game "a miss is a RESULT" — but `games()` turned that None into `[]`, and the walk
    reads an empty page as "this platform is finished", adds it to done_platforms and
    never asks again. One transient 404, or one platform id their catalogue no longer
    serves, silently truncated the walk with nothing recorded as an error.
  * THE BURST RESERVE WAS UNUSABLE BY THE PERSON IT EXISTS FOR. Its docstring says it
    exists so a long job cannot spend the hour in twelve minutes and leave an interactive
    lookup waiting forty-eight. But every caller shared one `_pace()` and one persisted
    window, so when the reserve was reached EVERYONE waited, the interactive lookup
    included. The sustained rate was 648/h instead of 720/h and the reserve bought
    nothing at all.

No network: mobygames._get is replaced, and the pacer's sleep is observed rather than
taken.
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
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_support
    test_support.isolate("ludodex-prov-moby-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import config
    import mobygames as MG
    import moby_mirror as MM

    config.set_("mobygames_api_key", "k")
    REC = {"game_id": 1, "title": "Doom", "moby_score": 8.5,
           "platforms": [{"platform_id": 3, "platform_name": "DOS"}]}

    served = {"404_for": None, "pages": {}}

    def fake_get(path, params=None, timeout=60, attempts=3):
        params = params or {}
        if path == "/platforms":
            return {"platforms": [{"platform_id": 1, "platform_name": "One"},
                                  {"platform_id": 2, "platform_name": "Two"},
                                  {"platform_id": 3, "platform_name": "Three"}]}
        if path == "/games":
            pid = (params.get("platform") or [0])[0]
            if pid == served["404_for"]:
                return None                      # what a 404 looks like from _get
            off = int(params.get("offset") or 0)
            if off:
                return {"games": []}
            return {"games": [dict(REC, game_id=pid * 100, title="Game %d" % pid)]}
        return {}

    MG._get = fake_get

    print("1. A 404 DOES NOT FINISH A PLATFORM")
    served["404_for"] = 2
    r = MM.walk(progress=False)
    st = MM.status()
    done = st.get("platforms_done") if isinstance(st, dict) else None
    con = MM.con_db()
    import json as _json
    donelist = _json.loads(MM.get(con, "done_platforms", "[]"))
    con.close()
    check("the platform that answered 404 is NOT recorded as finished: %s" % donelist,
          2 not in donelist)
    check("the ones that really answered are: %s" % donelist,
          1 in donelist and 3 in donelist)
    check("and the failure is reported rather than swallowed: %r" % r.get("failed"),
          r.get("platform_errors") or r.get("failed"))
    check("so the run does not claim completion: %r" % r.get("stopped"),
          r.get("stopped") != "complete")

    print()
    print("2. ...and the next run picks that platform up again")
    served["404_for"] = None
    r2 = MM.walk(progress=False)
    con = MM.con_db()
    donelist = _json.loads(MM.get(con, "done_platforms", "[]"))
    got = con.execute("SELECT COUNT(*) FROM moby_games WHERE id=200").fetchone()[0]
    con.close()
    check("it is finished now: %s" % donelist, 2 in donelist)
    check("and its game landed", got == 1)
    check("the run may now claim completion: %r" % r2.get("stopped"),
          r2.get("stopped") == "complete")

    print()
    print("3. THE BURST RESERVE IS FOR THE INTERACTIVE CALLER, so it must be spendable")
    slept = []
    MG.time.sleep = lambda s: slept.append(s)
    MG._last[0] = time.time()
    con = MG._state()
    con.execute("DELETE FROM req")
    now = time.time()
    # The hour is full up to the reserve line: a long job has used its share.
    con.executemany("INSERT INTO req(at) VALUES(?)",
                    [(now - 1800.0,)] * (MG.hourly_limit() - MG._reserve()))
    con.commit()
    con.close()
    check("the reserve is a real number: %d of %d"
          % (MG._reserve(), MG.hourly_limit()), MG._reserve() > 0)

    del slept[:]
    MG._pace()
    interactive = sum(slept)
    del slept[:]
    with MG.bulk_window():
        MG._pace()
    bulk = sum(slept)
    check("an interactive lookup still goes at the burst floor: %.2fs" % interactive,
          interactive <= MG._burst_floor() + 0.01)
    check("while a BULK caller waits for the window to open: %.0fs" % bulk,
          bulk > 60)

    print()
    print("4. once even the reserve is gone, everybody waits")
    con = MG._state()
    con.executemany("INSERT INTO req(at) VALUES(?)",
                    [(time.time() - 1800.0,)] * MG._reserve())
    con.commit()
    con.close()
    del slept[:]
    MG._pace()
    check("the interactive caller is paced too: %.0fs" % sum(slept), sum(slept) > 60)

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
