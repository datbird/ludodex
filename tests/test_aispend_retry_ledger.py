#!/usr/bin/env python3
"""A retried call is still a billed call, and a lost ledger write is not silence.

Three defects of one shape — money moving where nothing counted it.

  _retry re-spent and recorded nothing. A request that times out CLIENT-side after the
  provider already processed it IS billed. `record_usage` only ran after a successful
  return, so a request retried twice under-counted the ledger by two whole calls. The
  cap then measured a month that had never happened.

  _TRANSIENT matched by SUBSTRING, on bare numbers: "503", "500", "429". Any provider
  message containing those three digits anywhere — "500 tokens", a request id, a model
  named gpt-500 — bought two extra paid attempts at a permanent failure. Status belongs
  to the exception, not to a search of its prose.

  record_usage swallowed every exception (`except Exception: pass`) and `_usage_con()`
  set no busy_timeout. Under the parallel match pool "database is locked" is the normal
  contention outcome, so the ledger quietly lost calls that had already been paid for,
  which is exactly the state a dollar budget cannot survive.

Offline. Nothing here calls a provider; the failures are constructed.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = test_support.isolate("ludodex-aispend-retry-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import ai                                          # noqa: E402

PASS = []


def check(label, cond):
    PASS.append(bool(cond))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


class ApiError(Exception):
    """An SDK exception, which carries its HTTP status rather than spelling it."""

    def __init__(self, msg, status_code=None):
        Exception.__init__(self, msg)
        self.status_code = status_code


def flaky(errs, result="done"):
    """A callable that raises each of `errs` in turn, then returns `result`."""
    box = {"n": 0}

    def fn():
        box["n"] += 1
        if box["n"] <= len(errs):
            raise errs[box["n"] - 1]
        return result
    fn.calls = box
    return fn


def main():
    print("a retried call is counted, and a permanent one is not retried")

    ai.record_usage("prov", "model", 0, 0)     # make sure the table exists

    # ---- what counts as transient -------------------------------------------
    f = flaky([ApiError("service unavailable", 503),
               ApiError("service unavailable", 503)])
    check("a 503 is retried to success", ai._retry(f, base=0) == "done")
    check("and took three attempts: %d" % f.calls["n"], f.calls["n"] == 3)

    f = flaky([ApiError("invalid request: unsupported parameter", 400)])
    try:
        ai._retry(f, base=0)
        check("a 400 raises immediately", False)
    except ApiError:
        check("a 400 raises immediately", True)
    check("without a second paid attempt: %d" % f.calls["n"], f.calls["n"] == 1)

    # A 400 whose PROSE happens to contain a transient-looking word must still not be
    # retried: the status is the fact, the message is just text.
    f = flaky([ApiError("400: model temporarily named gpt-503 is unavailable", 400)])
    try:
        ai._retry(f, base=0)
    except ApiError:
        pass
    check("the status beats the prose: a 400 is never retried (%d attempt)"
          % f.calls["n"], f.calls["n"] == 1)

    # The substring bug, straight: a plain error mentioning a number.
    f = flaky([ValueError("the model returned 500 tokens and then stopped")])
    try:
        ai._retry(f, base=0)
    except ValueError:
        pass
    check("'500' inside a message is not an HTTP 500: %d attempt" % f.calls["n"],
          f.calls["n"] == 1)

    # …while a status-less transient still retries on its wording.
    f = flaky([RuntimeError("The service is overloaded, please try again")])
    check("an unmistakably transient message still retries",
          ai._retry(f, base=0) == "done" and f.calls["n"] == 2)

    # ---- a billed attempt reaches the ledger --------------------------------
    billed = []
    real_record = ai.record_usage
    ai.record_usage = lambda p, m, i, o: billed.append((p, m, i, o))
    try:
        # A CLIENT-SIDE TIMEOUT: the provider may well have finished and billed it.
        f = flaky([TimeoutError("Request timed out."),
                   TimeoutError("Request timed out.")])
        ai._retry(f, base=0, provider="anthropic", model="claude-haiku-4-5",
                  est=(1000, 400))
        check("both timed-out attempts are counted, not just the one that returned: %r"
              % (billed,), len(billed) == 2)
        check("each at the caller's estimate", billed[0] == ("anthropic",
                                                             "claude-haiku-4-5",
                                                             1000, 400))
        # A 429 is a REFUSAL — the provider never ran the request, so counting it would
        # over-charge the budget in the other direction.
        billed[:] = []
        f = flaky([ApiError("rate limit exceeded", 429)])
        ai._retry(f, base=0, provider="anthropic", model="claude-haiku-4-5",
                  est=(1000, 400))
        check("a refused request is NOT counted: %r" % (billed,), billed == [])

        # Without an estimate there is nothing honest to record.
        billed[:] = []
        f = flaky([TimeoutError("Request timed out.")])
        ai._retry(f, base=0, provider="anthropic", model="claude-haiku-4-5")
        check("no estimate, no invented number", billed == [])
    finally:
        ai.record_usage = real_record

    # ---- the ledger write itself --------------------------------------------
    con = ai._usage_con()
    bt = con.execute("PRAGMA busy_timeout").fetchone()[0]
    con.close()
    check("the usage connection waits out a locked database (%d ms)" % bt, bt >= 2000)

    # A ledger write that cannot land must not be silent: unmeasured spend is the one
    # thing this file exists to prevent.
    before = len(ai.unrecorded_usage())
    real_con = ai._usage_con

    def locked():
        raise sqlite3.OperationalError("database is locked")
    ai._usage_con = locked
    try:
        ai.record_usage("anthropic", "claude-haiku-4-5", 111, 22)
        check("a failed ledger write still does not raise at the caller", True)
    except Exception as e:                                     # noqa: BLE001
        check("a failed ledger write still does not raise at the caller: %r" % e, False)
    finally:
        ai._usage_con = real_con
    lost = ai.unrecorded_usage()
    check("but the lost call is reported, not swallowed: %r" % (lost[-1:],),
          len(lost) == before + 1)
    check("naming what went uncounted",
          lost[-1]["provider"] == "anthropic" and lost[-1]["input"] == 111
          and lost[-1]["output"] == 22)

    # And a good write really lands.
    ai.record_usage("anthropic", "claude-haiku-4-5", 5, 7)
    con = sqlite3.connect(ai.USAGE_DB)
    n = con.execute("SELECT SUM(input_tokens) FROM usage WHERE provider='anthropic' "
                    "AND model='claude-haiku-4-5'").fetchone()[0]
    con.close()
    check("a healthy ledger write is stored: %r" % n, n == 5)

    print("\nRESULT: %d checks, all passed" % len(PASS))


main()
