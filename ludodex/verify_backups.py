#!/usr/bin/env python3
"""Verify the snapshot-backup engine (backups.py) end to end, offline.

Exercises the real thing against a throwaway LUDODEX_DATA: real sqlite databases, real
online-backup snapshots, real zips (plain and AES), a real local push, real retention
pruning, and a round-trip that reads the data back OUT of the archive — because a backup
you have never restored is a rumour, not a backup.

Usage: python3 ludodex/verify_backups.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile

FAIL = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAIL.append(label)


scratch = tempfile.mkdtemp(prefix="ludodex-bak-")
os.environ["LUDODEX_DATA"] = scratch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Two databases with recognisable contents, plus a WAL-mode one that is mid-write when the
# snapshot runs — the case a plain file copy gets wrong.
for name, rows in (("tags.sqlite", [("mario", "favourite")]),
                   ("pins.sqlite", [("zelda", "cover")])):
    c = sqlite3.connect(os.path.join(scratch, name))
    c.execute("CREATE TABLE t (k TEXT, v TEXT)")
    c.executemany("INSERT INTO t VALUES (?,?)", rows)
    c.commit()
    c.close()

live = sqlite3.connect(os.path.join(scratch, "game-library.sqlite"))
live.execute("PRAGMA journal_mode=WAL")
live.execute("CREATE TABLE games (k TEXT)")
live.executemany("INSERT INTO games VALUES (?)", [("g%d" % i,) for i in range(500)])
live.commit()          # left OPEN, WAL active, to mimic a running server

import backups                                  # noqa: E402

dest = os.path.join(scratch, "dest")
os.makedirs(dest, exist_ok=True)

print("1. job CRUD")
jid = backups.set_job({"name": "Nightly", "contents": ["tags.sqlite"],
                       "dest_kind": "local", "dest_path": dest, "retention": 2})
check(isinstance(jid, int) and jid > 0, "job created (id=%s)" % jid)
j = backups.get_job(jid)
check(j["name"] == "Nightly" and j["contents"] == ["tags.sqlite"], "fields round-trip")
check("passphrase" not in j, "passphrase never leaves the server")
backups.set_job({"id": jid, "name": "Nightly renamed"})
check(backups.get_job(jid)["contents"] == ["tags.sqlite"],
      "partial update keeps unspecified fields")
check(backups.get_job(jid)["name"] == "Nightly renamed", "partial update applies the change")

print("2. content selection")
check(backups.db_files("ALL") == sorted(f for f in os.listdir(scratch)
                                        if f.endswith(".sqlite")),
      "ALL picks up every database, including ones added later")
check(backups.db_files("tags.sqlite") == ["tags.sqlite"], "explicit selection is honoured")
check(backups.db_files("nope.sqlite") == [], "a selection that no longer exists is skipped")

print("3. run: snapshot -> zip -> push")
backups.set_job({"id": jid, "contents": ["tags.sqlite", "game-library.sqlite"]})
r = backups.run_job(jid)
check(r["ok"] and r["databases"] == 2, "ran over 2 databases")
made = [f for f in os.listdir(dest) if f.endswith(".zip")]
check(len(made) == 1, "one archive landed in the destination (got %d)" % len(made))
z = os.path.join(dest, made[0])
check(os.path.getsize(z) > 0, "archive is non-empty")

print("4. the archive actually restores")
with zipfile.ZipFile(z) as zf:
    names = sorted(zf.namelist())
    check(names == ["game-library.sqlite", "tags.sqlite"], "contains exactly what was chosen")
    out = os.path.join(scratch, "restore")
    zf.extractall(out)
c = sqlite3.connect(os.path.join(out, "tags.sqlite"))
check(c.execute("SELECT v FROM t WHERE k='mario'").fetchone()[0] == "favourite",
      "data reads back out of the archive")
c.close()
c = sqlite3.connect(os.path.join(out, "game-library.sqlite"))
check(c.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 500,
      "a WAL database snapshotted while open is complete and consistent")
c.close()

print("5. status recorded on the job")
j = backups.get_job(jid)
check(j["last_ok"] == 1 and j["last_file"].endswith(".zip"), "last run recorded as ok")
check(j["last_size"] > 0, "size recorded")

print("6. retention keeps the newest N of THIS job only")
# A second job writing to the same folder must not be pruned by the first.
other = backups.set_job({"name": "Other", "contents": ["pins.sqlite"],
                         "dest_kind": "local", "dest_path": dest, "retention": 5})
backups.run_job(other)
for _ in range(3):
    import time as _t
    _t.sleep(1.05)                      # filenames are second-resolution
    backups.run_job(jid)
mine = sorted(f for f in os.listdir(dest) if f.startswith("ludodex-Nightly-renamed-"))
theirs = [f for f in os.listdir(dest) if f.startswith("ludodex-Other-")]
check(len(mine) == 2, "retention=2 kept exactly 2 of its own (got %d)" % len(mine))
check(len(theirs) == 1, "the other job's archive was left alone (got %d)" % len(theirs))

print("7. encryption")
enc = backups.set_job({"name": "Secret", "contents": ["tags.sqlite"], "dest_kind": "local",
                       "dest_path": dest, "passphrase": "hunter2", "retention": 1})
check(backups.get_job(enc)["encrypted"] is True, "job reports itself encrypted")
backups.run_job(enc)
ez = os.path.join(dest, [f for f in os.listdir(dest) if f.startswith("ludodex-Secret-")][0])
try:
    with zipfile.ZipFile(ez) as zf:
        zf.read("tags.sqlite")
    check(False, "encrypted archive rejects reading without the passphrase")
except RuntimeError:
    check(True, "encrypted archive rejects reading without the passphrase")
import pyzipper
with pyzipper.AESZipFile(ez) as zf:
    zf.setpassword(b"hunter2")
    blob = zf.read("tags.sqlite")
check(blob[:15] == b"SQLite format 3", "decrypts with the passphrase to a real database")
with pyzipper.AESZipFile(ez) as zf:
    zf.setpassword(b"wrong")
    try:
        zf.read("tags.sqlite")
        check(False, "wrong passphrase fails")
    except Exception:
        check(True, "wrong passphrase fails")

print("8. failure is recorded, not swallowed")
bad = backups.set_job({"name": "Bad", "contents": ["tags.sqlite"], "dest_kind": "local",
                       "dest_path": ""})
try:
    backups.run_job(bad)
    check(False, "a job with no destination raises")
except Exception:
    check(True, "a job with no destination raises")
check(backups.get_job(bad)["last_ok"] == 0, "failure recorded on the job")
check("destination" in (backups.get_job(bad)["last_error"] or ""), "error message kept")

print("9. scheduling")
check(backups.due_jobs() == [], "manual-only jobs (every_minutes=0) are never due")
backups.set_job({"id": jid, "every_minutes": 60})
check([x["id"] for x in backups.due_jobs()] == [], "a job that just ran isn't due yet")
import time as _t
backups.set_job({"id": jid, "every_minutes": 1})
check([x["id"] for x in backups.due_jobs(now=_t.time() + 3600)] == [jid],
      "becomes due once the interval passes")
backups.set_job({"id": jid, "enabled": 0})
check(backups.due_jobs(now=_t.time() + 3600) == [], "a disabled job is never due")

print("10. delete")
backups.delete_job(bad)
check(backups.get_job(bad) is None, "job deleted")

print("11. restore from an archive round-trips real data")
# Wipe a live database, then restore it from the archive the job wrote.
rest_dest = os.path.join(scratch, "rdest")
rj = backups.set_job({"name": "Roundtrip", "contents": ["tags.sqlite"],
                      "dest_kind": "local", "dest_path": rest_dest, "retention": 3})
backups.run_job(rj)
arcs = backups.list_archives(backups.get_job(rj))
check(len(arcs) == 1, "archive listed back from the destination (got %d)" % len(arcs))

c = sqlite3.connect(os.path.join(scratch, "tags.sqlite"))
c.execute("DELETE FROM t")                       # simulate the bad change
c.commit(); c.close()
c = sqlite3.connect(os.path.join(scratch, "tags.sqlite"))
check(c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0, "data destroyed")
c.close()

j = backups.get_job(rj, with_secret=True)
got = backups.fetch_archive(j, arcs[0], os.path.join(scratch, "fetch"))
names = backups.unpack(got, os.path.join(scratch, "unpacked"))
check(names == ["tags.sqlite"], "archive unpacked (%s)" % names)
shutil.copy2(os.path.join(scratch, "unpacked", "tags.sqlite"),
             os.path.join(scratch, "tags.sqlite"))
c = sqlite3.connect(os.path.join(scratch, "tags.sqlite"))
check(c.execute("SELECT v FROM t WHERE k='mario'").fetchone()[0] == "favourite",
      "data restored from the archive")
c.close()

print("12. encrypted archive restore needs the passphrase")
ej = backups.set_job({"name": "Enc", "contents": ["tags.sqlite"], "dest_kind": "local",
                      "dest_path": rest_dest, "passphrase": "s3cret", "retention": 1})
backups.run_job(ej)
ea = backups.list_archives(backups.get_job(ej))[0]
ejob = backups.get_job(ej, with_secret=True)
ez2 = backups.fetch_archive(ejob, ea, os.path.join(scratch, "fetch2"))
try:
    backups.unpack(ez2, os.path.join(scratch, "u2"))
    check(False, "unpacking an encrypted archive without a passphrase fails")
except Exception:
    check(True, "unpacking an encrypted archive without a passphrase fails")
ok = backups.unpack(ez2, os.path.join(scratch, "u3"), "s3cret")
check(ok == ["tags.sqlite"], "unpacks with the right passphrase")

live.close()
shutil.rmtree(scratch, ignore_errors=True)
print()
if FAIL:
    print("FAILED (%d): %s" % (len(FAIL), "; ".join(FAIL)))
    sys.exit(1)
print("ALL CHECKS PASSED")
