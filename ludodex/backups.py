"""Scheduled snapshot backups — roll chosen databases into a zip and push it somewhere.

Deliberately NOT the same thing as dbsync.py:

  dbsync.py   live two-way sync of your durable stores to an external database. Keeps a
              second copy continuously reconciled. Recovers you from a lost machine.
  backups.py  point-in-time archive. A zip of exactly what you picked, dropped where you
              said, on the schedule you set, keeping the last N. Recovers you from a bad
              change — the thing a live mirror can't, because a mirror faithfully copies
              your mistake.

Multiple independent JOBS: each has its own contents, destination, timing and retention,
so "config nightly to the NAS" and "everything weekly to the Deck" can coexist.

Snapshots use SQLite's ONLINE BACKUP api, so they're consistent even while the server is
mid-write — a plain file copy of a live WAL database can land torn.

Encryption is optional. When a passphrase is set the zip is AES-256 (WinZip AES via
pyzipper), which 7-Zip / Keka / WinRAR can open — a backup you can only read with the app
that made it isn't much of a backup.

SECRETS ARE OPT-IN AND ENCRYPTION-ONLY. 'ALL' means "every database whose loss would cost
you", and the databases holding credentials are not in it: an archive of every provider
API key and every device password, written unencrypted and world-readable onto a NAS
share, is a worse outcome than the data loss the backup was guarding against. Name one in
a job's contents to include it anyway — but only in a job that has a passphrase, refused
when you configure it rather than at 3am. `backups.sqlite` never travels at all, because
it stores every job's passphrase in plaintext and would hand over the key to every
ENCRYPTED job's archive.
"""
import os
import shutil
import sqlite3
import time
import zipfile

import config

DATA = os.environ.get("LUDODEX_DATA",
                      os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(DATA, "backups.sqlite")
STAGE = os.path.join(DATA, "tmp", "backup-stage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    contents TEXT DEFAULT 'ALL',      -- comma list of *.sqlite names, or 'ALL'
    dest_kind TEXT DEFAULT 'local',   -- 'local' (a path this server can see) | 'device'
    dest_path TEXT DEFAULT '',
    device_id INTEGER,
    every_minutes INTEGER DEFAULT 0,  -- 0 = manual only
    retention INTEGER DEFAULT 7,      -- keep the N newest for this job (0 = keep all)
    passphrase TEXT DEFAULT '',       -- '' = unencrypted
    last_run INTEGER DEFAULT 0,
    last_ok INTEGER,
    last_error TEXT DEFAULT '',
    last_file TEXT DEFAULT '',
    last_size INTEGER DEFAULT 0
);
"""


def _con():
    os.makedirs(DATA, exist_ok=True)
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


FIELDS = ("name", "enabled", "contents", "dest_kind", "dest_path", "device_id",
          "every_minutes", "retention", "passphrase")


def _public(row):
    """A job as the API exposes it — the passphrase itself never leaves the server."""
    d = dict(row)
    d["encrypted"] = bool((d.pop("passphrase", "") or "").strip())
    d["contents"] = [] if d["contents"] == "ALL" else [
        c for c in (d["contents"] or "").split(",") if c]
    d["all_contents"] = row["contents"] == "ALL"
    return d


def all_jobs():
    con = _con()
    try:
        return [_public(r) for r in con.execute("SELECT * FROM jobs ORDER BY id")]
    finally:
        con.close()


def get_job(job_id, with_secret=False):
    con = _con()
    try:
        r = con.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        if not r:
            return None
        return dict(r) if with_secret else _public(r)
    finally:
        con.close()


def set_job(body):
    """Create (no id) or update (id) a job. Absent keys keep their stored value; a
    passphrase of None is 'leave as-is' while '' explicitly clears it."""
    con = _con()
    try:
        jid = body.get("id")
        cur = None
        if jid:
            cur = con.execute("SELECT * FROM jobs WHERE id=?", (int(jid),)).fetchone()
        vals = {}
        for f in FIELDS:
            if f in body and body[f] is not None:
                v = body[f]
                if f == "contents":
                    v = "ALL" if (v in ("ALL", None) or v == []) else ",".join(v)
                if f in ("enabled", "every_minutes", "retention"):
                    v = int(v or 0)
                if f == "device_id":
                    v = int(v) if v else None
                vals[f] = v
            elif cur is not None:
                vals[f] = cur[f]
            else:
                vals[f] = {"name": "Backup", "enabled": 1, "contents": "ALL",
                           "dest_kind": "local", "dest_path": "", "device_id": None,
                           "every_minutes": 0, "retention": 7, "passphrase": ""}[f]
        # Validate the RESOLVED job, not the body: a partial update that only clears the
        # passphrase must be judged against the contents already stored.
        _guard_contents(vals["contents"], vals["passphrase"])
        _guard_destination(vals["dest_kind"], vals["device_id"])
        if cur is not None:
            con.execute("UPDATE jobs SET %s WHERE id=?"
                        % ",".join("%s=?" % f for f in FIELDS),
                        [vals[f] for f in FIELDS] + [int(jid)])
        else:
            con.execute("INSERT INTO jobs (%s) VALUES (%s)"
                        % (",".join(FIELDS), ",".join("?" * len(FIELDS))),
                        [vals[f] for f in FIELDS])
            jid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.commit()
        return int(jid)
    finally:
        con.close()


def delete_job(job_id):
    con = _con()
    try:
        con.execute("DELETE FROM jobs WHERE id=?", (int(job_id),))
        con.commit()
    finally:
        con.close()


def _mark(job_id, ok, error="", fname="", size=0):
    con = _con()
    try:
        con.execute("UPDATE jobs SET last_run=?, last_ok=?, last_error=?, last_file=?, "
                    "last_size=? WHERE id=?",
                    (int(time.time()), 1 if ok else 0, (error or "")[:400], fname, size,
                     int(job_id)))
        con.commit()
    finally:
        con.close()


# Rebuildable scraper artifacts. These are MIRRORS of external catalogs — every byte
# can be re-fetched, they carry no user decision, and together they run to gigabytes
# (166 MB of IGDB, ~1.5 GB of ScreenScraper when its walk finishes). 'ALL' means "every
# database that holds something losing would cost you", which is not the same as every
# file on disk. Name one explicitly in a job's contents to back it up anyway.
DERIVED = {"igdb-catalog.sqlite", "ss-catalog.sqlite", "match-index.sqlite"}

# Databases whose rows ARE credentials: provider API keys, the device passwords
# connections.sqlite stores in the clear, session tokens. Excluded from 'ALL' so the
# default job cannot write every secret in the install to a share in plaintext. Naming
# one explicitly still works — see _guard_contents, which requires a passphrase for it.
SECRET = {"config.sqlite", "connections.sqlite", "auth.sqlite"}

# Never in any archive, named or not, encrypted or not. backups.sqlite holds each job's
# passphrase as plaintext, so shipping it inside job B's zip hands whoever can open that
# zip the key to every ENCRYPTED job's archive at the same destination.
NEVER = {"backups.sqlite"}


def db_files(contents):
    """Resolve a job's content selection to real files present in DATA. 'ALL' (stored as
    the literal string) means every *.sqlite that is not a rebuildable mirror and not a
    credential store — including ones added by future features, which is what you want
    from a backup."""
    try:
        present = sorted(f for f in os.listdir(DATA)
                         if f.endswith(".sqlite") and os.path.isfile(os.path.join(DATA, f)))
    except OSError:
        return []
    present = [f for f in present if f not in NEVER]
    if contents == "ALL" or not contents:
        return [f for f in present if f not in DERIVED and f not in SECRET]
    want = {c.strip() for c in contents.split(",") if c.strip()}
    return [f for f in present if f in want]


def _guard_contents(contents, passphrase):
    """Refuse a selection that would write credentials in the clear.

    Checked when the job is SAVED and again when it RUNS: the save is where a person can
    fix it, but rows written before this rule existed are already on disk and the
    scheduler runs them unattended."""
    if contents == "ALL" or not contents:
        return
    named = {c.strip() for c in contents.split(",") if c.strip()}
    if named & NEVER and not (named - NEVER):
        # Otherwise this fails later as the unhelpful "nothing selected to back up".
        raise ValueError(
            "%s cannot be backed up: it stores every job's passphrase in plaintext, so "
            "putting it in an archive hands over the key to every encrypted job's "
            "backup. Re-create your jobs by hand after a restore."
            % ", ".join(sorted(named & NEVER)))
    leaking = sorted(named & SECRET)
    if leaking and not (passphrase or "").strip():
        raise ValueError(
            "%s hold credentials in the clear; a job that backs them up needs a "
            "passphrase, because the archive is written to a destination this server "
            "does not control" % ", ".join(leaking))


def _guard_destination(dest_kind, device_id):
    """A device destination with no device selected is not a destination.

    push_file(None, ...) writes to the path LOCALLY and returns happily, so the job
    reported ok and the user believed the archive was offsite when it never left the
    server — the failure mode a backup can least afford."""
    if dest_kind == "device" and not device_id:
        raise ValueError("this job pushes to a device but no device is selected")


def _is_sqlite(path):
    """True when the file really is a SQLite database, by its magic header."""
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _snapshot(files, dest_dir):
    """Consistent copies of `files` into dest_dir via SQLite's online backup.

    RAISES on any file it cannot capture. The old code fell back to `shutil.copy2` on
    sqlite3.Error and `continue`d on OSError, so a database that was locked hard was
    copied WITHOUT its -wal — the torn copy this module's docstring exists to prevent —
    and a database it could not read at all simply vanished from the archive. Both
    reported as a successful backup, which is the one outcome a backup must never
    produce: you find out at restore time. A job that fails loudly can be re-run.

    The one surviving copy2 is for a file that is not a SQLite database at all, where
    there is no online backup to do and a byte copy is the correct answer."""
    os.makedirs(dest_dir, exist_ok=True)
    os.chmod(dest_dir, 0o700)        # the staged copies are the same secrets as the originals
    out = []
    for fname in files:
        src, dst = os.path.join(DATA, fname), os.path.join(dest_dir, fname)
        s = d = None
        try:
            s = sqlite3.connect(src, timeout=15)
            d = sqlite3.connect(dst)
            with d:
                s.backup(d)
        except sqlite3.Error as e:
            if _is_sqlite(src):
                raise RuntimeError("could not snapshot %s: %s" % (fname, e))
            try:
                shutil.copy2(src, dst)          # not a sqlite database — a byte copy IS right
            except OSError as oe:
                raise RuntimeError("could not copy %s: %s" % (fname, oe))
        finally:
            for c in (s, d):
                if c is not None:
                    try:
                        c.close()
                    except sqlite3.Error:
                        pass
        out.append(dst)
    return out


def _zip(paths, zip_path, passphrase=""):
    """Zip the snapshot. AES-256 when a passphrase is given (openable by 7-Zip/Keka), a
    plain deflate zip otherwise."""
    # Create the file 0600 BEFORE anything is written into it, then let the zip writer
    # open the existing path — "w" does not reset an existing file's mode. Creating it
    # under the process umask and chmod'ing afterwards would leave the whole archive
    # world-readable for as long as the zip takes to write. shutil.copy2 and `rsync -a`
    # both carry this mode to the destination, so it is also what lands on the NAS.
    os.close(os.open(zip_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600))
    if passphrase:
        try:
            import pyzipper
        except ImportError:
            raise RuntimeError("encrypted backups need pyzipper (pip install pyzipper)")
        with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as z:
            z.setpassword(passphrase.encode())
            for p in paths:
                z.write(p, os.path.basename(p))
    else:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for p in paths:
                z.write(p, os.path.basename(p))
    return os.path.getsize(zip_path)


def _slug(name):
    return "".join(c if (c.isalnum() or c in "-_") else "-" for c in (name or "backup"))[:40] \
        or "backup"


def run_job(job_id, log=lambda m: None):
    """Snapshot -> zip -> push -> prune. Returns a summary dict. Raises on failure (the
    caller records it); last_* on the job row is always updated either way."""
    job = get_job(job_id, with_secret=True)
    if not job:
        raise RuntimeError("no such backup job %r" % job_id)
    import devices
    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime())
    prefix = "ludodex-%s-" % _slug(job["name"])
    fname = "%s%s.zip" % (prefix, stamp)
    stage = os.path.join(STAGE, str(job_id))
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)
    os.chmod(stage, 0o700)           # the staging dir holds the archive in the clear
    zip_path = os.path.join(stage, fname)
    try:
        # Checked here as well as in set_job: jobs written before these rules existed are
        # already in the table, and the scheduler runs them with nobody watching.
        _guard_contents(job["contents"], job["passphrase"])
        _guard_destination(job["dest_kind"], job["device_id"])
        files = db_files(job["contents"])
        if not files:
            raise RuntimeError("nothing selected to back up")
        log("snapshotting %d database(s)" % len(files))
        snaps = _snapshot(files, os.path.join(stage, "dbs"))
        log("zipping%s" % (" (encrypted)" if job["passphrase"] else ""))
        size = _zip(snaps, zip_path, job["passphrase"] or "")

        dest = (job["dest_path"] or "").rstrip("/")
        if not dest:
            raise RuntimeError("no destination path set")
        dev_id = job["device_id"] if job["dest_kind"] == "device" else None
        log("pushing to %s%s" % ("device %s:" % dev_id if dev_id else "", dest))
        devices.push_file(dev_id, zip_path, dest)

        kept = pruned = 0
        if int(job["retention"] or 0) > 0:
            kept, pruned = _prune(devices, dev_id, dest, prefix, int(job["retention"]))
            log("retention: kept %d, removed %d" % (kept, pruned))
        _mark(job_id, True, "", fname, size)
        # len(snaps), not len(files): report what is IN the archive. _snapshot raises
        # rather than skipping now, so the two agree — and if that ever stops being
        # true, the summary is the number that would have lied.
        return {"ok": True, "file": fname, "size": size, "databases": len(snaps),
                "pruned": pruned, "dest": dest, "device_id": dev_id}
    except Exception as e:
        _mark(job_id, False, str(e)[:400])
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _prune(devices, dev_id, dest, prefix, keep):
    """Keep the `keep` newest archives THIS job wrote (matched by its filename prefix, so
    two jobs pointing at one folder never delete each other's backups)."""
    names = devices.list_names(dev_id, dest)
    mine = sorted(n for n in names if n.startswith(prefix) and n.endswith(".zip"))
    doomed = mine[:-keep] if len(mine) > keep else []
    if doomed:
        devices.remove_paths(dev_id, ["%s/%s" % (dest, n) for n in doomed])
    return min(len(mine), keep), len(doomed)


def due_jobs(now=None):
    """Enabled jobs with a schedule whose next run has arrived."""
    now = now or time.time()
    out = []
    for j in all_jobs():
        every = int(j.get("every_minutes") or 0)
        if not j.get("enabled") or every <= 0:
            continue
        if now - (j.get("last_run") or 0) >= every * 60:
            out.append(j)
    return out


def list_archives(job):
    """Archives this job has written to its destination, newest first."""
    import devices
    dest = (job.get("dest_path") or "").rstrip("/")
    if not dest:
        return []
    dev_id = job["device_id"] if job.get("dest_kind") == "device" else None
    prefix = "ludodex-%s-" % _slug(job.get("name"))
    try:
        names = devices.list_names(dev_id, dest)
    except Exception:
        return []
    return sorted((n for n in names if n.startswith(prefix) and n.endswith(".zip")),
                  reverse=True)


def fetch_archive(job, name, into):
    """Bring one archive back to this server so it can be unpacked."""
    import devices
    dest = (job.get("dest_path") or "").rstrip("/")
    dev_id = job["device_id"] if job.get("dest_kind") == "device" else None
    return devices.pull_file(dev_id, "%s/%s" % (dest, name), into)


def unpack(zip_path, into, passphrase=""):
    """Extract an archive (encrypted or not) into `into`. Returns the db filenames found."""
    os.makedirs(into, exist_ok=True)
    if passphrase:
        import pyzipper
        with pyzipper.AESZipFile(zip_path) as z:
            z.setpassword(passphrase.encode())
            z.extractall(into)
    else:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(into)
    return sorted(f for f in os.listdir(into) if f.endswith(".sqlite"))
