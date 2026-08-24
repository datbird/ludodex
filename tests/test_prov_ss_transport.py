#!/usr/bin/env python3
"""The ScreenScraper transport decides what every caller believes, so the only thing
that matters here is which failures it turns into which ANSWER.

Two of those answers used to be lies:

  * "TIMED OUT" WAS UNREACHABLE FOR THE COMMONEST TIMEOUT. `_request` has a 177-line
    docstring about retrying a service that routinely answers in 30-40s. urllib raises a
    CONNECT timeout as URLError(reason=TimeoutError), and `_read` caught every URLError
    first and re-raised it as SSError('closed') — so the retry ran for read timeouts and
    never for connect timeouts. ss_scrape slept 60s and skipped the game; ss_mirror
    counted a closed strike and moved its cursor past it.
  * "NOT FOUND" WAS RETURNED FOR A BODY WE COULD NOT READ. `_read` ended with
    `return None  # non-JSON body we couldn't classify -> treat as not found`, and
    ss_scrape writes status='notfound' for a None, whose rows are in the permanently-done
    set. One HTML maintenance page therefore deleted that (game, system) from the
    worklist for good.

Nothing here touches the network: urlopen is replaced.
"""
import io
import os
import socket
import sys
import urllib.error

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
    test_support.isolate("ludodex-prov-sstrans-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import screenscraper as ss

    CREDS = {"devid": "d", "devpassword": "p", "softname": "ludodex"}
    calls = {"n": 0}
    slept = []
    ss.time.sleep = lambda s: slept.append(s)      # the backoff, without the wait

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def serve(raise_with=None, body=None):
        def urlopen(req, timeout=None):
            calls["n"] += 1
            if raise_with is not None:
                raise raise_with()
            return Resp(body)
        ss.urllib.request.urlopen = urlopen

    print("1. A CONNECT TIMEOUT IS A TIMEOUT, and is retried like one")
    # urllib wraps it: URLError(reason=TimeoutError()). This is the case the retry
    # existed for and the one it could not see.
    serve(raise_with=lambda: urllib.error.URLError(TimeoutError("timed out")))
    calls["n"] = 0
    del slept[:]
    try:
        ss._request("jeuInfos.php", CREDS, {"gameid": 3}, timeout=1, attempts=3)
        err = None
    except ss.SSError as e:
        err = e
    check("it raised", err is not None)
    check("it tried three times, not once: %d" % calls["n"], calls["n"] == 3)
    check("it backed off between them: %s" % slept, len(slept) == 2)
    check("and it is reported as a timeout, not as 'api closed': %s" % err.kind,
          err.kind == "error" and "timed out" in str(err))

    print()
    print("2. a READ timeout behaves identically — one policy, not two")
    serve(raise_with=socket.timeout)
    calls["n"] = 0
    try:
        ss._request("jeuInfos.php", CREDS, {"gameid": 3}, timeout=1, attempts=3)
        err = None
    except ss.SSError as e:
        err = e
    check("also retried three times: %d" % calls["n"], calls["n"] == 3)
    check("also classified as a timeout", err and err.kind == "error")

    print()
    print("3. a connection that is genuinely REFUSED is not retried as a timeout")
    serve(raise_with=lambda: urllib.error.URLError(ConnectionRefusedError()))
    calls["n"] = 0
    try:
        ss._request("jeuInfos.php", CREDS, {"gameid": 3}, timeout=1, attempts=3)
        err = None
    except ss.SSError as e:
        err = e
    check("one attempt: %d" % calls["n"], calls["n"] == 1)
    check("and it is 'closed'", err and err.kind == "closed")

    print()
    print("4. AN UNREADABLE BODY IS NOT AN ABSENCE")
    serve(body=b"<html><head><title>Maintenance</title></head><body>brb</body></html>")
    try:
        jeu, _q = ss.jeu_infos(CREDS, gameid=3)
        err = None
    except ss.SSError as e:
        jeu, err = "raised", e
    check("the caller is told something went wrong: %s" % (err and err.kind),
          err is not None and err.kind == "error")
    check("rather than being handed 'ScreenScraper does not have this game'",
          jeu == "raised")

    print()
    print("5. a real 404 IS an absence, and still is")
    def missing(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {},
                                     io.BytesIO(b"not found"))
    ss.urllib.request.urlopen = missing
    calls["n"] = 0
    jeu, _q = ss.jeu_infos(CREDS, gameid=999999)
    check("no game, no error", jeu is None)
    check("and no retry was spent proving it", calls["n"] == 1)

    print()
    print("6. 429 AND 430 ARE DIFFERENT ANSWERS")
    # 'slow down' vs 'that is your day'. Collapsed into one kind, every transient
    # throttle looked like exhaustion — which is why the walk had to spend an extra
    # ssuserInfos request per throttled result to find out which it really was.
    for code, kind in ((429, "rate"), (430, "quota"), (431, "quota"),
                       (401, "badcreds"), (423, "closed"), (500, "error")):
        def boom(req, timeout=None, _c=code):
            raise urllib.error.HTTPError(req.full_url, _c, "x", {},
                                         io.BytesIO(b"refused"))
        ss.urllib.request.urlopen = boom
        try:
            ss._request("jeuInfos.php", CREDS, {"gameid": 3}, attempts=1)
            got = "no error"
        except ss.SSError as e:
            got = e.kind
        check("HTTP %d -> %s" % (code, kind), got == kind)

    print()
    print("7. every attempt is counted for the budget, retries included")
    ss.urllib.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(
        socket.timeout())
    before = ss.attempts_made()
    try:
        ss._request("jeuInfos.php", CREDS, {"gameid": 3}, timeout=1, attempts=3)
    except ss.SSError:
        pass
    check("three attempts were reported, not one: %d"
          % (ss.attempts_made() - before), ss.attempts_made() - before == 3)

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
