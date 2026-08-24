#!/usr/bin/env python3
"""TheGamesDB's walk and its monthly budget, both of which are claims about things we
cannot see and therefore have to be careful about.

  * "COMPLETE" NEEDS PROOF, AND REACHING A CEILING IS NOT PROOF. The walk declared the
    catalogue complete when `cursor >= top`, and on the first pass `top` IS the guessed
    CEILING of 138,000 — so hitting it with live games still arriving in the last block
    wrote walk_complete="1". It self-heals on the next --walk, because a re-run extends
    `top` past the cursor, but until then the status lies. That is the same shape as the
    bug the file's own comment describes: a threshold the author invented, a real gap
    inside it, COMPLETE declared at 50,268 of 121,454 games.
  * A RETRY IS A REQUEST. The allowance is 12,000 a MONTH on the Developer tier, and
    `_spend()` was called once by the caller while `_request` made up to three attempts.
    A flaky day therefore spent up to three times the budget it had computed.

No network: by_ids/urlopen are replaced.
"""
import io
import os
import socket
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
    test_support.isolate("ludodex-prov-tgdb-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import config
    import thegamesdb as T
    import tgdb_mirror as MM

    config.set_("thegamesdb_api_key", "0123456789abcdef")
    T.vocabulary = lambda: {"genres": {}, "developers": {}, "publishers": {}}
    T.budget = lambda key=None: 100000

    alive = {"on": True}

    def fake_by_ids(ids, key=None):
        if not alive["on"]:
            return [], {}
        # One live game per block: enough that the dead run never arms, cheap enough to
        # walk the whole 138,000-id space the real first pass has to cover.
        return ([{"id": ids[0], "game_title": "Game %d" % ids[0], "platform": 1,
                  "release_date": "1995-01-01"}], {})

    T.by_ids = fake_by_ids

    print("1. REACHING THE GUESSED CEILING IS NOT A COMPLETED CATALOGUE")
    # No until_id, so `top` is CEILING — the number sampling suggested. Every block is
    # still returning live games when the walk runs into it. That is the end of my
    # guess, not the end of the data.
    r = MM.walk(progress=False)
    check("it walked to the ceiling: cursor %d" % r["cursor"],
          r["cursor"] >= MM.CEILING)
    check("and says so honestly rather than claiming completion: %r" % r["stopped"],
          r["stopped"] == "ceiling")
    st = MM.status()
    check("nothing was written to walk_complete", not st.get("complete"))

    print()
    print("1b. an until_id the CALLER gave is finished when it is reached...")
    con = MM.con_db()
    MM.put(con, "cursor", 0)
    MM.put(con, "dead_run", 0)
    con.commit(); con.close()
    r = MM.walk(until_id=200, progress=False)
    check("the bounded run reports itself complete: %r" % r["stopped"],
          r["stopped"] == "complete")
    check("...but still proves nothing about the catalogue, so no mark is written",
          not MM.status().get("complete"))

    print()
    print("2. a genuine DEAD RUN is still proof, and still ends the walk")
    alive["on"] = False
    con = MM.con_db()
    MM.put(con, "cursor", 0)
    MM.put(con, "dead_run", 0)
    con.commit(); con.close()
    r = MM.walk(until_id=MM.DEAD_RUN_STOP * MM.BLOCK * 2, progress=False)
    check("it declared completion: %r" % r["stopped"], r["stopped"] == "complete")
    check("only after DEAD_RUN_STOP empty blocks: %d requests" % r["requests"],
          r["requests"] >= MM.DEAD_RUN_STOP)
    check("and recorded it", MM.status().get("complete"))

    print()
    print("3. A RETRY IS A REQUEST, and the monthly allowance has to see it")
    con = T._state()
    T._put(con, "remaining", 1000)
    con.commit(); con.close()
    T.time.sleep = lambda s: None
    tries = {"n": 0}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def flaky(req, timeout=None):
        tries["n"] += 1
        if tries["n"] < 3:
            raise socket.timeout()
        return Resp(b'{"code": 200, "data": {"games": []}}')

    T.urllib.request.urlopen = flaky
    T.search("sonic", key="k")
    rem, _extra, _at = T.cached_allowance()
    check("three attempts were made", tries["n"] == 3)
    check("and all three were charged: 1000 -> %s" % rem, rem == 997)

    print()
    print("4. the endpoint that does NOT cost allowance is still free")
    # /v1/API/Limit is documented as not counting, and a retry of it must not either.
    con = T._state()
    T._put(con, "remaining", 500)
    con.commit(); con.close()
    tries["n"] = 0

    def limit_resp(req, timeout=None):
        tries["n"] += 1
        if tries["n"] < 2:
            raise socket.timeout()
        return Resp(b'{"code": 200, "remaining_monthly_allowance": 500,'
                    b' "extra_allowance": 0}')

    T.urllib.request.urlopen = limit_resp
    T.limit_status(force=True, key="k")
    rem, _e, _a = T.cached_allowance()
    check("it retried", tries["n"] == 2)
    check("and nothing was deducted for it: %s" % rem, rem == 500)

    print()
    print("5. the help text names the tier this deployment actually has")
    src = open(os.path.join(root, "ludodex", "thegamesdb.py"), encoding="utf-8").read()
    check("the $29 Developer tier and its 12,000/month are stated",
          "12,000" in src and "Developer" in src)
    check("and the guess that the tiers publish no limits is gone",
          "do not publish what limit each grants" not in src)

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
