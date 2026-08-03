#!/usr/bin/env python3
"""Isolation guard for the offline tests. Import and call `isolate()` FIRST.

A test that seeds fixtures has to point `LUDODEX_DATA` somewhere disposable. Doing that
with `os.environ.setdefault` is silently wrong: setdefault keeps an EXISTING value, so
inside the running container — where `LUDODEX_DATA=/data` is already set — the "temp
dir" was never used and the fixture setup ran against the live databases. That is how
`test_shape_select.py`'s `DELETE FROM media` erased a 66,280-row production media index
on 2026-08-02.

The mistake is not really "setdefault vs assignment". It is that a destructive test had
no way to tell it was pointed at production. So this does both:

  * hard-assign a fresh temp dir, overriding whatever was inherited, and
  * refuse to run if the resolved data dir still looks live.

`isolate()` must be called BEFORE importing any ludodex module, because they resolve
paths at import time.
"""
import os
import sys
import tempfile

# Deployments keep their databases here. A test resolving to any of these is a bug in
# the test, never something to work around.
LIVE_DIRS = ("/data", "<appdata>/ludodex")


def isolate(prefix="ludodex-test-"):
    """Point LUDODEX_DATA at a fresh temp dir and prove it is not a live one."""
    d = tempfile.mkdtemp(prefix=prefix)
    os.environ["LUDODEX_DATA"] = d            # ASSIGN — never setdefault, see module doc
    assert_isolated()
    return d


def assert_isolated():
    """Abort loudly if the current LUDODEX_DATA points at real data.

    Cheap enough to call again after any code that might re-resolve the path, and the
    only thing standing between a destructive fixture and someone's library.
    """
    raw = os.environ.get("LUDODEX_DATA") or ""
    if not raw.strip():
        # NB os.path.abspath("") returns the CWD, which is truthy — so the emptiness
        # test has to happen on the raw value, before normalising.
        sys.exit("REFUSING TO RUN: LUDODEX_DATA is unset, so this test would use the "
                 "default data dir. Call test_support.isolate() before importing "
                 "any ludodex module.")
    d = os.path.abspath(raw)
    for live in LIVE_DIRS:
        if d == live or d.startswith(live.rstrip("/") + "/"):
            sys.exit("REFUSING TO RUN: LUDODEX_DATA=%s is a LIVE data directory. This "
                     "test seeds and deletes fixture rows; running it here would "
                     "destroy real data. Call test_support.isolate() first." % d)
    return d
