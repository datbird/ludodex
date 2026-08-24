#!/usr/bin/env python3
"""A backup must not be the thing that leaks the credentials it was made to protect.

The default job shipped every secret in the clear. `contents` defaults to 'ALL', which
meant every *.sqlite except three rebuildable mirrors, so `config.sqlite` (every provider
API key), `connections.sqlite` (device passwords, stored as plaintext), `auth.sqlite` and
`backups.sqlite` all went into the zip. `passphrase TEXT DEFAULT ''` means UNENCRYPTED by
default, and the local push is `shutil.copy2` under the process umask — 0644 in the
container. So the out-of-the-box configuration wrote a world-readable archive of every
credential in the install onto a NAS share.

`backups.sqlite` is the sharpest edge of it: it stores each job's passphrase in
plaintext, and it was itself inside 'ALL'. One unencrypted job therefore handed over the
passphrase of every ENCRYPTED job, to the same destination, defeating their encryption
entirely.

The rules this pins:

  * 'ALL' means every database whose loss would hurt — NOT the ones that hold secrets.
    A default job stays useful (library, tags, ownership, media index) and stays safe.
  * A secret database may still be backed up, deliberately, by naming it — but only in
    an ENCRYPTED job, refused at configure time rather than at 3am.
  * `backups.sqlite` never travels, in any job, encrypted or not. It is the file whose
    contents are other jobs' keys.
  * The archive is 0600 wherever it lands, because copy2 and `rsync -a` both carry the
    source mode to the destination.

And 12b, the "successful backup" that was not one: `_snapshot` fell back to a plain
`shutil.copy2` when the online backup raised, which copies a WAL database WITHOUT its
-wal — the exact tear this module's docstring exists to prevent — while `except OSError:
continue` dropped a file entirely, and the summary reported `len(files)` rather than the
number actually captured. A torn or missing database read as a clean backup.

Offline. Local destination, no devices, no network.
"""
import os
import sqlite3
import stat
import sys
import zipfile

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-backup-secrets-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import backups                                                 # noqa: E402

try:
    import pyzipper                                            # noqa: F401
    HAVE_PYZIPPER = True
except ImportError:
    HAVE_PYZIPPER = False

PASS = []
DEST = os.path.join(DATA, "dest")


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def seed(name, rows=(("k", "v"),)):
    con = sqlite3.connect(os.path.join(DATA, name))
    con.execute("CREATE TABLE IF NOT EXISTS t (k TEXT, v TEXT)")
    con.executemany("INSERT INTO t VALUES (?,?)", rows)
    con.commit()
    con.close()


def raised_by(fn):
    try:
        fn()
    except BaseException as e:                                 # noqa: BLE001
        return e
    return None


def archives(prefix):
    return sorted(f for f in os.listdir(DEST) if f.startswith(prefix))


def main():
    print("a backup does not leak the credentials it exists to protect")
    os.makedirs(DEST, exist_ok=True)
    for n in ("tags.sqlite", "game-library.sqlite", "config.sqlite",
              "connections.sqlite", "auth.sqlite"):
        seed(n)
    backups.all_jobs()                      # creates backups.sqlite in DATA

    # ---- 'ALL' no longer means 'and every secret too' --------------------------- #
    allf = backups.db_files("ALL")
    check("'ALL' still picks up the databases you would miss",
          "tags.sqlite" in allf and "game-library.sqlite" in allf)
    for secret in ("config.sqlite", "connections.sqlite", "auth.sqlite",
                   "backups.sqlite"):
        check("'ALL' leaves %s out" % secret, secret not in allf)

    # ---- naming a secret is allowed, but only with encryption ------------------- #
    e = raised_by(lambda: backups.set_job(
        {"name": "Keys", "contents": ["config.sqlite"], "dest_kind": "local",
         "dest_path": DEST}))
    check("configuring an unencrypted job that names a secret is refused",
          isinstance(e, Exception))
    check("and the refusal names the file and the reason",
          "config.sqlite" in str(e) and "passphrase" in str(e).lower())

    enc = backups.set_job({"name": "Keys", "contents": ["config.sqlite", "tags.sqlite"],
                           "dest_kind": "local", "dest_path": DEST,
                           "passphrase": "hunter2", "retention": 2})
    check("the same job WITH a passphrase is accepted", isinstance(enc, int))
    if HAVE_PYZIPPER:
        # pyzipper is a declared requirement, but a checkout without it must still be
        # able to run the rest of this file — the guards are the subject here.
        r = backups.run_job(enc)
        check("and runs", r["ok"])
        check("the encrypted archive landed", len(archives("ludodex-Keys-")) == 1)
    got = archives("ludodex-Keys-")

    # ---- a job stored before this fix must still not run in the clear ----------- #
    # set_job is the gate, but rows written by the old code are already on disk, and
    # the scheduler runs them unattended.
    con = sqlite3.connect(backups.DB)
    con.execute("UPDATE jobs SET passphrase='' WHERE id=?", (enc,))
    con.commit()
    con.close()
    e = raised_by(lambda: backups.run_job(enc))
    check("running a pre-existing unencrypted job over a secret is refused too",
          isinstance(e, Exception))
    check("and nothing new was written to the destination",
          archives("ludodex-Keys-") == got)

    # ---- backups.sqlite never travels, in any job ------------------------------- #
    # It stores every job's passphrase in plaintext, so shipping it inside job B's
    # archive hands over job A's key.
    check("even named explicitly, backups.sqlite is not selected",
          backups.db_files("backups.sqlite,tags.sqlite") == ["tags.sqlite"])
    e = raised_by(lambda: backups.set_job(
        {"name": "Jobs", "contents": ["backups.sqlite"], "dest_kind": "local",
         "dest_path": DEST, "passphrase": "hunter2"}))
    check("a job that backs up ONLY that is refused, not left silently empty",
          isinstance(e, Exception) and "backups.sqlite" in str(e))

    # ---- the archive is not world-readable -------------------------------------- #
    plain = backups.set_job({"name": "Nightly", "contents": [], "dest_kind": "local",
                             "dest_path": DEST, "retention": 2})
    r = backups.run_job(plain)
    z = os.path.join(DEST, r["file"])
    mode = stat.S_IMODE(os.stat(z).st_mode)
    check("the pushed archive is owner-only (got 0%o)" % mode, mode == 0o600)
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
    check("and the default archive carries no secret database",
          not (names & {"config.sqlite", "connections.sqlite", "auth.sqlite",
                        "backups.sqlite"}))
    check("while carrying the data you actually wanted", "tags.sqlite" in names)
    check("the summary counts what was captured, not what was asked for",
          r["databases"] == len(names))

    # ---- a database that cannot be snapshotted fails the job -------------------- #
    # The old fallback copy2'd a WAL database without its -wal and carried on, so a torn
    # file reported as a clean backup. Refusing is recoverable; a silent tear is not.
    real_connect = backups.sqlite3.connect

    def flaky(path, *a, **k):
        if str(path).endswith("tags.sqlite"):
            raise sqlite3.OperationalError("database is locked")
        return real_connect(path, *a, **k)

    before = archives("ludodex-Nightly-")
    backups.sqlite3.connect = flaky
    try:
        e = raised_by(lambda: backups.run_job(plain))
    finally:
        backups.sqlite3.connect = real_connect
    check("a database that will not snapshot fails the job", isinstance(e, Exception))
    check("and the failure names the database", "tags.sqlite" in str(e))
    check("no half-archive was pushed", archives("ludodex-Nightly-") == before)
    check("and the job records the failure", backups.get_job(plain)["last_ok"] == 0)

    # ---- a device destination with no device is not a local write --------------- #
    # push_file(None, ...) writes LOCALLY and returns happily, so the user was told
    # their archive was offsite when it never left the server.
    e = raised_by(lambda: backups.set_job(
        {"name": "Offsite", "dest_kind": "device", "dest_path": "/backups",
         "device_id": None}))
    check("configuring a device job with no device is refused", isinstance(e, Exception))
    check("and says which field is missing", "device" in str(e).lower())

    con = sqlite3.connect(backups.DB)       # again: rows the old code already wrote
    con.execute("INSERT INTO jobs (name, enabled, contents, dest_kind, dest_path, "
                "device_id, every_minutes, retention, passphrase) VALUES "
                "('Offsite', 1, 'tags.sqlite', 'device', ?, NULL, 0, 2, '')", (DEST,))
    con.commit()
    orphan = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.close()
    e = raised_by(lambda: backups.run_job(orphan))
    check("running one is refused rather than silently written locally",
          isinstance(e, Exception))
    check("and it did not quietly land in the local destination",
          archives("ludodex-Offsite-") == [])

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
