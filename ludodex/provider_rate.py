#!/usr/bin/env python3
"""The pacing and timeout rules the provider drivers share.

WHY THIS MODULE EXISTS AT ALL. Several drivers had grown a private copy of the same few
lines — `arcadedb._pace` and `zxinfo._pace` were byte-identical, `igdb._throttle` was the
same arithmetic with a different floor, and the "is this urllib failure a timeout?"
unwrapper lived twice, once as a named helper in `screenscraper` and once inline in
`thegamesdb`. The two retry loops' shared classification policy was even written out in
prose in BOTH docstrings, with a note saying the modules may not import each other and
the rules must be kept in agreement by hand. A third module neither of them owns is the
fix that note was asking for: a driver imports this, drivers still never import each
other, and there is one copy of the arithmetic to be wrong in.

WHAT DELIBERATELY DOES NOT LIVE HERE, because "seven throttles" counts things that are
not the same thing:

  * `mobygames._pace` paces against a PERSISTED rolling-hour window with a reserve held
    back for interactive callers. It is a budget, not a gap, and it survives a restart on
    purpose — an in-memory counter is how a paced client becomes an unpaced one after a
    container restart.
  * `ra._throttle` gates three axes at once (a per-day cap that RAISES, a cooldown, and a
    per-minute window) off `config.rate_limits`. Folding a rule that refuses into one
    that only waits would lose the refusal.
  * `steam_tags` and `scores_fetch` sleep a fixed cooldown BETWEEN items. That is not a
    limiter: it does not discount the time the work itself took, so routing them through
    `min_gap` would make them run measurably faster against services that never agreed
    to it. Speeding up a scraper is a behaviour change, not a cleanup.

The retry loops in `screenscraper`, `thegamesdb` and `mobygames` also stay where they
are. They look alike and are not: each raises its OWN exception type, and each provider
means something different by the same status code — ScreenScraper separates 429 ("too
fast") from 430/431 ("that is your allowance"), TheGamesDB folds 403 and 429 together
into one monthly cap, and MobyGames treats 404 as a RESULT rather than a failure. A
shared classifier would have to be told all of that per provider, which is the table
those functions already are. What they genuinely share — the timeout test and the
backoff schedule — is here, and their bodies now read it from one place.
"""
import socket
import time


def min_gap(state, gap):
    """Wait until `gap` seconds have passed since the last call through `state`.

    `state` is a one-element mutable list owned by the CALLING module, so each provider
    keeps its own clock — one shared clock would make every provider wait on every other
    provider's last request. Measured against the wall clock at the moment of the
    previous call, so time spent inside the request counts towards the gap: a provider
    that answers in 800ms with a 1s cooldown waits 200ms, not a further second.
    """
    wait = gap - (time.time() - state[0])
    if wait > 0:
        time.sleep(wait)
    state[0] = time.time()


def is_timeout(e):
    """A timeout however urllib chose to wrap it.

    Read timeouts surface as `socket.timeout`; CONNECT timeouts arrive as
    `URLError(reason=TimeoutError)`. Missing the second form is what made
    `screenscraper`'s retry loop dead code for the commonest failure of a slow server —
    the URLError was swallowed into a permanent error before the retry could see it.
    """
    return isinstance(e, (socket.timeout, TimeoutError)) or isinstance(
        getattr(e, "reason", None), (socket.timeout, TimeoutError))


def retry_delay(attempt):
    """Seconds to wait before attempt N+1: 2, 4, 6 …

    Linear, not exponential, and deliberately so. These retries only ever follow a
    TIMEOUT — a provider that is slow, not one that has refused — and the call that
    timed out has already spent its own timeout (90s for ScreenScraper) waiting. Doubling
    on top of that turns a retry budget into a stall.
    """
    return 2 * (attempt + 1)
