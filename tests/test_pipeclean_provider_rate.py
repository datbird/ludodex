#!/usr/bin/env python3
"""The provider drivers' shared pacing and timeout rules — one copy, same behaviour.

`arcadedb._pace` and `zxinfo._pace` were byte-identical and `igdb._throttle` was the same
arithmetic with a different floor. Worse, the "is this urllib failure a timeout?" test —
the one whose CONNECT-timeout half being missing made screenscraper's retry loop dead
code for the commonest failure of a slow server — existed twice, and the classification
policy the two retry loops share was written out in PROSE in both docstrings with a note
saying the modules may not import each other and the rules must be kept in agreement by
hand. `provider_rate` is the third module both may import.

This is a REFACTOR guard, not a bug fix: every check here states what the private copies
already did, so a rewrite of the shared helper that changes any of it fails here rather
than quietly re-pacing four providers at once.

What deliberately did NOT move is asserted too. `mobygames` paces against a persisted
rolling-hour budget with a reserve, `ra` gates three axes and RAISES on the per-day cap,
and `steam_tags` / `scores_fetch` sleep a fixed cooldown BETWEEN items without discounting
the time the work took — routing that through `min_gap` would make them run faster against
services that never agreed to it.

Offline. Pure functions and a fake clock; no network, no provider ever contacted.
"""
import os
import socket
import sys
import urllib.error

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

test_support.isolate("ludodex-pipeclean-rate-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import provider_rate                                           # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def src(name):
    return open(os.path.join(DIR, "ludodex", name), encoding="utf-8").read()


class Clock:
    """A fake wall clock, so the pacing rules are checked without spending the time."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def main():
    print("provider_rate: one limiter, one timeout rule")

    print()
    print("1. min_gap waits the REMAINDER, never the whole gap again")
    # The distinction is the whole point of a limiter: a provider that answered in 800ms
    # under a 1s cooldown owes 200ms, not another second. Sleeping the full gap after
    # every call is what halves a walk's throughput for no protection at all.
    clk = Clock()
    real_time, real_sleep = provider_rate.time.time, provider_rate.time.sleep
    provider_rate.time.time, provider_rate.time.sleep = clk.time, clk.sleep
    try:
        state = [0.0]
        provider_rate.min_gap(state, 1.0)          # cold: nothing to wait for
        check("the first call through a fresh state does not sleep", clk.slept == [])
        check("and stamps the state", state[0] == clk.now)

        clk.now += 0.8                             # the request took 800ms
        provider_rate.min_gap(state, 1.0)
        check("a 1.0s gap after 0.8s of work sleeps 0.2s",
              len(clk.slept) == 1 and abs(clk.slept[0] - 0.2) < 1e-9)

        clk.now += 5.0                             # slower than the gap: no wait at all
        provider_rate.min_gap(state, 1.0)
        check("work longer than the gap sleeps not at all", len(clk.slept) == 1)

        state_b = [0.0]
        provider_rate.min_gap(state_b, 1.0)
        check("each provider's state is its own clock", len(clk.slept) == 1)
    finally:
        provider_rate.time.time, provider_rate.time.sleep = real_time, real_sleep

    print()
    print("2. is_timeout catches BOTH wrappings")
    check("a read timeout (socket.timeout)",
          provider_rate.is_timeout(socket.timeout("timed out")))
    check("a connect timeout (URLError wrapping TimeoutError)",
          provider_rate.is_timeout(urllib.error.URLError(TimeoutError("timed out"))))
    check("a refused connection is NOT a timeout — it must be raised, not retried",
          not provider_rate.is_timeout(
              urllib.error.URLError(ConnectionRefusedError("refused"))))
    check("nor is an ordinary error", not provider_rate.is_timeout(ValueError("nope")))

    print()
    print("3. the backoff schedule is the one both retry loops used: 2, 4, 6")
    check("attempt 0 -> 2s", provider_rate.retry_delay(0) == 2)
    check("attempt 1 -> 4s", provider_rate.retry_delay(1) == 4)
    check("attempt 2 -> 6s", provider_rate.retry_delay(2) == 6)

    print()
    print("4. the private copies are gone from the drivers that adopted it")
    for name in ("arcadedb.py", "zxinfo.py", "igdb.py"):
        s = src(name)
        check("%s paces through provider_rate" % name, "provider_rate.min_gap(" in s)
        check("%s keeps no private gap arithmetic" % name,
              "wait = gap - (time.time()" not in s and "if dt < gap:" not in s)
    for name in ("screenscraper.py", "thegamesdb.py"):
        s = src(name)
        check("%s tests timeouts through provider_rate" % name,
              "provider_rate.is_timeout(" in s)
        check("%s backs off through provider_rate" % name,
              "provider_rate.retry_delay(" in s)
        check("%s defines no second copy of the timeout test" % name,
              "def _is_timeout(" not in s)

    print()
    print("5. what must NOT have been folded in is still its own")
    moby = src("mobygames.py")
    check("mobygames still paces against its persisted hourly window",
          "_spend_window()" in moby and "provider_rate.min_gap(" not in moby)
    ra = src("ra.py")
    check("ra still RAISES on the per-day cap rather than waiting",
          "per-day request cap" in ra and "provider_rate.min_gap(" not in ra)
    for name in ("steam_tags.py", "scores_fetch.py"):
        check("%s still sleeps between items rather than pacing" % name,
              "provider_rate.min_gap(" not in src(name))
    check("provider_rate says WHY each of those stayed out",
          all(w in src("provider_rate.py")
              for w in ("mobygames._pace", "ra._throttle", "steam_tags")))

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


main()
