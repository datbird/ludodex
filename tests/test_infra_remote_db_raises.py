#!/usr/bin/env python3
"""Nothing in the backing-store plumbing may call sys.exit.

`dbsync.PocketBaseBackend.__init__` calls `pb_auth`, and the whole sync runs inside a
worker thread the server starts for the backing-store job. That job wrapper catches
`Exception` — and `SystemExit` inherits from `BaseException`, not `Exception`, so a
wrong PocketBase password did not fail the job: the thread died with `last` never set,
the UI kept showing the previous result, and the sync silently stopped happening.

`pb_write` already carries the rule in a comment ("RuntimeError, not sys.exit: this
runs inside the server's backing-store job"). It just was not applied to the two
functions that run BEFORE any write — which are exactly the ones a bad credential or
an unreachable host trips first.

Offline. `http` is replaced with a stub; nothing here reaches a network.
"""
import os
import re
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-remotedb-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import remote_db                                               # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def raised_by(fn):
    """Return the exception `fn` raised, catching BaseException so a SystemExit is
    reported as the failure it is instead of ending this test with status 0."""
    try:
        fn()
    except BaseException as e:                                 # noqa: BLE001
        return e
    return None


def main():
    print("the backing-store plumbing reports failures instead of exiting")

    real_http = remote_db.http
    remote_db.http = lambda *a, **k: (400, {"message": "Failed to authenticate."})
    try:
        e = raised_by(lambda: remote_db.pb_auth("http://pb.invalid", "a@b.c", "wrong"))
        check("a rejected password raises rather than exiting",
              isinstance(e, Exception) and not isinstance(e, SystemExit))
        check("and the message names what failed, for the job's `last`",
              "auth" in str(e).lower())

        e = raised_by(lambda: remote_db.pb_ensure_collection(
            "http://pb.invalid", {}, "ludodex_tags", [{"name": "k", "type": "text"}]))
        check("a collection that cannot be created raises too",
              isinstance(e, Exception) and not isinstance(e, SystemExit))
        check("and names the collection", "ludodex_tags" in str(e))
    finally:
        remote_db.http = real_http

    # ---- and the server's job wrapper would actually catch these ---------------- #
    caught = None
    try:
        remote_db.http = lambda *a, **k: (0, "connection refused")
        try:
            remote_db.pb_auth("http://pb.invalid", "a@b.c", "pw")
        except Exception as ex:                                # noqa: BLE001
            caught = ex                     # exactly the clause app.py's job uses
    finally:
        remote_db.http = real_http
    check("an unreachable host is caught by `except Exception`", caught is not None)

    # ---- no sys.exit survives anywhere in the module ---------------------------- #
    src = open(os.path.join(DIR, "ludodex", "remote_db.py"), encoding="utf-8").read()
    check("the module contains no sys.exit at all",
          re.search(r"\bsys\.exit\s*\(", src) is None)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
