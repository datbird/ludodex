#!/usr/bin/env python3
"""A backup you have never restored is a rumour, and a schedule nobody checked is worse.

`backups.run_job` is asserted elsewhere for what it must not put in the zip. The other
half — WHEN a job runs and how you get the archive BACK — was covered by nothing.

  * `due_jobs` is what the scheduler loop asks. Getting it wrong is silent in both
    directions: too eager and every tick re-runs a job that just finished (a full
    snapshot, a full push, every minute); too shy and the nightly backup the user
    configured months ago has simply never happened. So the rules are pinned
    explicitly: a manual-only job (every_minutes = 0) is NEVER due no matter how long
    ago it ran, a disabled job is never due, a job that just ran is not due again until
    its interval has passed, and a job that has NEVER run is due immediately — which is
    the case a naive `now - last_run >= interval` gets right only by accident, because
    last_run is 0.

  * `list_archives` is scoped by the job's own filename prefix. Two jobs pointing at the
    same share is the normal case (one nightly, one weekly), and a listing that ignored
    the prefix would offer job A's archives as job B's restore points — and, through the
    same prefix rule in `_prune`, let one job's retention delete the other's backups.
    Newest-first matters too: it is what the restore UI offers by default.

  * `fetch_archive` brings one back from wherever it was pushed. It is the first step of
    every restore, so it is asserted by actually restoring: destroy a live database, and
    read the data back out of the fetched archive.

Offline: a LOCAL destination, no devices, no network.
"""
import os
import shutil
import sqlite3
import sys
import time
import zipfile

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-ported-backups-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import backups                                                 # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def make_db(name, rows):
    p = os.path.join(DATA, name)
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE t (k TEXT, v TEXT)")
    con.executemany("INSERT INTO t VALUES (?,?)", rows)
    con.commit()
    con.close()
    return p


def rowcount(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        con.close()


def main():
    print("when a backup runs, and how you get it back")
    test_support.assert_isolated()
    check("backups resolved its DATA to the fixture dir",
          os.path.abspath(backups.DATA) == os.path.abspath(DATA))

    make_db("tags.sqlite", [("mario", "favourite"), ("zelda", "favourite")])
    make_db("pins.sqlite", [("zelda", "cover")])
    dest = os.path.join(DATA, "share")
    os.makedirs(dest, exist_ok=True)

    print()
    print("1. due_jobs — a manual job is never due, however long it waits")
    manual = backups.set_job({"name": "Manual", "contents": ["tags.sqlite"],
                              "dest_kind": "local", "dest_path": dest,
                              "every_minutes": 0, "retention": 3})
    far_future = time.time() + 365 * 86400
    check("every_minutes=0 is not due now", backups.due_jobs() == [])
    check("nor a year from now", backups.due_jobs(now=far_future) == [])

    print()
    print("2. a scheduled job that has NEVER run is due immediately")
    nightly = backups.set_job({"name": "Nightly", "contents": ["tags.sqlite"],
                              "dest_kind": "local", "dest_path": dest,
                               "every_minutes": 60, "retention": 2})
    check("it is due straight away: %s" % [j["id"] for j in backups.due_jobs()],
          [j["id"] for j in backups.due_jobs()] == [nightly])
    check("and the manual job is still not, alongside it",
          manual not in [j["id"] for j in backups.due_jobs()])

    print()
    print("3. once it has run it is not due again until the interval passes")
    r = backups.run_job(nightly)
    check("the run succeeded over 1 database", r["ok"] and r["databases"] == 1)
    check("it is no longer due", [j["id"] for j in backups.due_jobs()] == [])
    check("still not due 59 minutes later",
          [j["id"] for j in backups.due_jobs(now=time.time() + 59 * 60)] == [])
    check("due again once the hour has passed",
          [j["id"] for j in backups.due_jobs(now=time.time() + 3601)] == [nightly])

    print()
    print("4. a disabled job is never due, whatever its schedule says")
    backups.set_job({"id": nightly, "enabled": 0})
    check("disabled and overdue is still not due",
          backups.due_jobs(now=far_future) == [])
    backups.set_job({"id": nightly, "enabled": 1})
    check("re-enabling brings it back",
          [j["id"] for j in backups.due_jobs(now=far_future)] == [nightly])
    check("due_jobs hands over the whole job row, not just an id",
          backups.due_jobs(now=far_future)[0]["dest_path"] == dest)
    check("and it never leaks the passphrase",
          "passphrase" not in backups.due_jobs(now=far_future)[0])

    print()
    print("5. list_archives finds what the run actually pushed")
    job = backups.get_job(nightly)
    arcs = backups.list_archives(job)
    check("exactly one archive, the one run_job reported: %s" % arcs,
          arcs == [r["file"]])
    check("and it is really on disk", os.path.exists(os.path.join(dest, arcs[0])))

    print()
    print("6. it is scoped to THIS job's archives, newest first")
    # A second job sharing the destination is the normal case (a nightly and a weekly
    # pointing at one share). Its archives must never be offered as this job's restore
    # points — the same prefix rule that stops _prune deleting them.
    other = backups.set_job({"name": "Weekly", "contents": ["pins.sqlite"],
                             "dest_kind": "local", "dest_path": dest, "retention": 5})
    ro = backups.run_job(other)
    # Older/newer stamps for this job, plus a stray file, placed directly in the
    # destination — exactly what a share looks like after months of runs.
    for extra in ("ludodex-Nightly-2020-01-01_000000.zip",
                  "ludodex-Nightly-2099-01-01_000000.zip"):
        shutil.copy2(os.path.join(dest, arcs[0]), os.path.join(dest, extra))
    with open(os.path.join(dest, "ludodex-Nightly-notes.txt"), "w") as fh:
        fh.write("not an archive")
    mine = backups.list_archives(backups.get_job(nightly))
    check("the other job's archive is not listed: %s" % ro["file"],
          ro["file"] not in mine)
    check("a non-zip beside them is not listed",
          not any(n.endswith(".txt") for n in mine))
    check("all three of this job's archives are: %d" % len(mine), len(mine) == 3)
    check("newest first: %s" % mine[0],
          mine[0] == "ludodex-Nightly-2099-01-01_000000.zip"
          and mine[-1] == "ludodex-Nightly-2020-01-01_000000.zip")
    check("and the other job still sees only its own",
          backups.list_archives(backups.get_job(other)) == [ro["file"]])

    print()
    print("7. a destination that isn't there answers [] instead of raising")
    ghost = backups.set_job({"name": "Ghost", "contents": ["tags.sqlite"],
                             "dest_kind": "local",
                             "dest_path": os.path.join(DATA, "no-such-share")})
    check("a missing directory lists nothing", backups.list_archives(
        backups.get_job(ghost)) == [])
    nodest = backups.set_job({"name": "Nodest", "contents": ["tags.sqlite"],
                              "dest_kind": "local", "dest_path": ""})
    check("and so does a job with no destination at all",
          backups.list_archives(backups.get_job(nodest)) == [])

    print()
    print("8. fetch_archive brings one home, byte for byte")
    into = os.path.join(DATA, "fetched")
    got = backups.fetch_archive(backups.get_job(nightly, with_secret=True),
                                r["file"], into)
    check("it returns the local path it wrote: %s" % os.path.basename(got),
          os.path.isfile(got) and os.path.dirname(got) == into)
    check("named after the archive, not the job", os.path.basename(got) == r["file"])
    check("and it is the same bytes as the pushed archive",
          open(got, "rb").read() == open(os.path.join(dest, r["file"]), "rb").read())
    check("which is a real zip holding the chosen database",
          zipfile.ZipFile(got).namelist() == ["tags.sqlite"])

    print()
    print("9. and the fetched archive actually restores — the whole point")
    live = os.path.join(DATA, "tags.sqlite")
    con = sqlite3.connect(live)
    con.execute("DELETE FROM t")                    # the bad change you want undone
    con.commit()
    con.close()
    check("the live database is empty", rowcount(live) == 0)
    names = backups.unpack(got, os.path.join(DATA, "unpacked"))
    check("the archive unpacks to the database it promised: %s" % names,
          names == ["tags.sqlite"])
    restored = os.path.join(DATA, "unpacked", "tags.sqlite")
    check("with both rows in it", rowcount(restored) == 2)
    con = sqlite3.connect(restored)
    check("and the actual values, not just a row count",
          con.execute("SELECT v FROM t WHERE k='mario'").fetchone()[0] == "favourite")
    con.close()

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
