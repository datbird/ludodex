#!/usr/bin/env python3
"""Putting a backup back must not be able to leave a worse database than it found.

Both restore paths (a local snapshot, and an archive fetched from a device) did the
same thing: `shutil.copy2(src, os.path.join(DATA, f))`. That rewrites the live file
IN PLACE, which has three consequences and no upside.

  * THE OLD -wal AND -shm STAY. SQLite can replay a stale write-ahead log onto the
    bytes that replaced it, so the file you were recovering ends up corrupt. reset.py
    already removes them for exactly this reason: "a stale WAL would replay onto the
    file we just removed."
  * A HALF-WRITTEN FILE IS VISIBLE. copy2 truncates and streams; the restore is
    explicitly followed by a client-driven restart, so requests are served against the
    file while it changes. A failure partway leaves a truncated database and no backup
    of what used to be there beyond the safety snapshot.
  * NOTHING CHECKS THE SOURCE. A truncated archive, a zip that unpacked badly, a file
    that is not a database at all — all of it was copied over good data.

Staging beside the target and renaming is atomic: the live path is either the old file
or the new one, never half of either.

auth.sqlite is the way back in. reset.py refuses to delete it for that reason, and a
restore silently replacing it can log the operator out with an old credential set, so it
is opt-in rather than automatic.

Offline. No network.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-restore-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def make_db(path, note):
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t(note TEXT)")
    con.execute("INSERT INTO t VALUES(?)", (note,))
    con.commit()
    con.close()
    return path


def note_in(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT note FROM t").fetchone()[0]
    finally:
        con.close()


def sidecars(path):
    return [s for s in ("-wal", "-shm") if os.path.exists(path + s)]


def strays(path):
    d, base = os.path.dirname(path), os.path.basename(path)
    return [f for f in os.listdir(d)
            if f.startswith(base) and f not in (base, base + "-wal", base + "-shm")]


def main():
    print("a restore is atomic, checked, and leaves no stale WAL")

    live = os.path.join(DATA, "live.sqlite")
    backup = os.path.join(DATA, "backup.sqlite")

    # ---- the good case -------------------------------------------------------- #
    make_db(live, "today")
    make_db(backup, "last week")
    open(live + "-wal", "wb").write(b"stale log bytes")     # left by the live process
    app._restore_db(backup, live)
    check("the backed-up database is in place", note_in(live) == "last week")
    check("the stale write-ahead log is gone", sidecars(live) == [])
    check("and no staging file is left behind", strays(live) == [])

    # ---- a source that is not a database -------------------------------------- #
    make_db(live, "today")
    junk = os.path.join(DATA, "junk.sqlite")
    with open(junk, "wb") as f:
        f.write(b"this is not a database")
    raised = None
    try:
        app._restore_db(junk, live)
    except Exception as e:                                  # noqa: BLE001
        raised = e
    check("an unreadable archive is refused", raised is not None)
    check("and the live database is untouched", note_in(live) == "today")
    check("with nothing staged beside it", strays(live) == [])

    # ---- a truncated source --------------------------------------------------- #
    make_db(live, "today")
    cut = os.path.join(DATA, "cut.sqlite")
    make_db(cut, "partial")
    with open(cut, "r+b") as f:                             # a half-transferred archive
        f.truncate(60)
    raised = None
    try:
        app._restore_db(cut, live)
    except Exception as e:                                  # noqa: BLE001
        raised = e
    check("a truncated archive is refused too", raised is not None)
    check("and again the live database survives", note_in(live) == "today")

    # ---- both restore endpoints go through it --------------------------------- #
    src = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read()
    for fn in ("ops_restore", "backup_restore"):
        body = src.split("def %s(" % fn, 1)[1].split("\n@app.")[0]
        check("%s stages and renames rather than copying over the live file" % fn,
              "_restore_db(" in body and "shutil.copy2(" not in body)
        check("%s does not replace the way back in without being asked" % fn,
              "auth.sqlite" in body)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
