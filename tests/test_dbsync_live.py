"""Live two-way round-trip against a real backing-store backend (task #2).

Backend-agnostic twin of test_dbsync_roundtrip.py (which is PocketBase-only). Proves the
whole merge engine end-to-end against a REAL database:

  1. local add            -> pushed to the remote
  2. re-sync, no changes  -> a genuine no-op (0 in both directions)
  3. remote add           -> pulled into local sqlite
  4. local delete         -> deleted on the remote
  5. remote delete        -> deleted locally

Runs against an ISOLATED LUDODEX_DATA (its own sqlite stores + shadow state), so it never
touches the real library. Backend credentials are copied out of the live config.sqlite, so
no secrets live in this file. Remote rows are namespaced by a sentinel key and removed at
the end.

Usage (inside the container, where the drivers are installed):
    LUDODEX_DATA=/tmp/dbsync-live python3 test_dbsync_live.py postgres
    LUDODEX_DATA=/tmp/dbsync-live python3 test_dbsync_live.py mysql
"""
import os
import sqlite3
import sys

if os.environ.get("LUDODEX_LIVE_TESTS") != "1":
    # Its sqlite side is isolated, but it still creates and deletes rows in a REAL
    # remote backing store using the live credentials. Same opt-in as the others.
    sys.exit("SKIPPED: live test. It writes to a real backing-store backend with the "
             "instance's credentials. Re-run with LUDODEX_LIVE_TESTS=1 and a backend "
             "argument (postgres|mysql|pocketbase|firestore).")

BACKEND = sys.argv[1] if len(sys.argv) > 1 else "postgres"
LIVE_CONFIG = os.environ.get("LUDODEX_LIVE_CONFIG", "/data/config.sqlite")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "/app")

# This file had its own hand-rolled version of this check (`SCRATCH in ("/data","/app")`),
# which is the same rule written twice — and the copy missed the host-side data dir. One
# implementation, shared with every other test.
import test_support                                 # noqa: E402
SCRATCH = test_support.assert_isolated()
os.makedirs(SCRATCH, exist_ok=True)

import config                                       # noqa: E402
import dbsync                                       # noqa: E402

FAIL = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAIL.append(label)


# ---- copy just this backend's connection settings out of the live config ----
WANT = ("postgres_", "mysql_", "supabase_", "pocketbase_", "firebase_")
try:
    lc = sqlite3.connect("file:%s?mode=ro" % LIVE_CONFIG, uri=True)
    copied = 0
    for k, v in lc.execute("SELECT key, value FROM config"):
        if v and any(k.startswith(p) for p in WANT):
            config.set_(k, v)
            copied += 1
    lc.close()
    print("copied %d connection setting(s) from the live config" % copied)
except sqlite3.Error as e:
    print("could not read live config (%s) — relying on env/config already present" % e)

STORE = next(s for s in dbsync.STORES if s["name"] == "user_tags")
NK = "__dbsync_livetest__"
DBP = dbsync._db_path(STORE)


def local_conn():
    return sqlite3.connect(DBP)


def seed_local_schema():
    c = local_conn()
    c.execute("CREATE TABLE IF NOT EXISTS user_tags (norm_key TEXT, tag TEXT, created INT, "
              "PRIMARY KEY (norm_key, tag))")
    c.commit()
    c.close()


def local_tags():
    c = local_conn()
    try:
        return {t for (t,) in c.execute("SELECT tag FROM user_tags WHERE norm_key=?", (NK,))}
    finally:
        c.close()


def add_local(tag):
    c = local_conn()
    c.execute("INSERT OR REPLACE INTO user_tags VALUES (?,?,?)", (NK, tag, 1))
    c.commit()
    c.close()


def del_local(tag):
    c = local_conn()
    c.execute("DELETE FROM user_tags WHERE norm_key=? AND tag=?", (NK, tag))
    c.commit()
    c.close()


def sync():
    return dbsync.sync_all(BACKEND, only=["user_tags"])["stores"][0]


def rkey(tag):
    """Composite key exactly as dbsync builds it — dbsync.SEP (\x1f), not a tab. Getting
    this wrong makes the remote look empty and the merge engine look broken."""
    return dbsync._key_of(STORE, {"norm_key": NK, "tag": tag})


def remote_tags(be, cols):
    pre = NK + dbsync.SEP
    return {k.split(dbsync.SEP)[1] for k in be.read_all(STORE, cols) if k.startswith(pre)}


print("backend: %s   scratch: %s" % (BACKEND, SCRATCH))
seed_local_schema()
be = dbsync.BACKENDS[BACKEND]()
_, COLS = dbsync._local_read(STORE)
be.ensure(STORE, COLS)

# start from a clean slate for our sentinel
stale = [k for k in be.read_all(STORE, COLS) if k.startswith(NK + dbsync.SEP)]
if stale:
    be.write(STORE, COLS, {}, stale, set())
    print("cleared %d stale sentinel row(s)" % len(stale))

try:
    print("1. local add -> push")
    add_local("alpha")
    add_local("beta")
    r = sync()
    check(r["pushed"] >= 2, "pushed >= 2 (got %d)" % r["pushed"])
    check(remote_tags(be, COLS) == {"alpha", "beta"}, "remote holds alpha+beta")

    print("2. re-sync with nothing changed is a true no-op")
    r = sync()
    check(r["pushed"] == 0 and r["pulled"] == 0
          and r["pushed_deleted"] == 0 and r["pulled_deleted"] == 0,
          "0 in every direction (got %s)" % r)

    print("3. remote add -> pull")
    be.write(STORE, COLS, {rkey("gamma"): {"norm_key": NK, "tag": "gamma", "created": 2}},
             [], set())
    r = sync()
    check(r["pulled"] >= 1, "pulled >= 1 (got %d)" % r["pulled"])
    check("gamma" in local_tags(), "gamma landed in local sqlite")

    print("4. local delete -> remote delete")
    del_local("alpha")
    r = sync()
    check(r["pushed_deleted"] >= 1, "pushed_deleted >= 1 (got %d)" % r["pushed_deleted"])
    check("alpha" not in remote_tags(be, COLS), "alpha gone from the remote")

    print("5. remote delete -> local delete")
    be.write(STORE, COLS, {}, [rkey("beta")], set())
    r = sync()
    check(r["pulled_deleted"] >= 1, "pulled_deleted >= 1 (got %d)" % r["pulled_deleted"])
    check("beta" not in local_tags(), "beta gone from local sqlite")

    print("6. converged state matches on both sides")
    check(local_tags() == remote_tags(be, COLS) == {"gamma"},
          "both sides hold exactly {gamma} (local=%s remote=%s)"
          % (local_tags(), remote_tags(be, COLS)))
finally:
    left = [k for k in be.read_all(STORE, COLS) if k.startswith(NK + dbsync.SEP)]
    if left:
        be.write(STORE, COLS, {}, left, set())
    print("cleaned up %d sentinel row(s) from the remote" % len(left))

print()
if FAIL:
    print("FAILED (%d): %s" % (len(FAIL), "; ".join(FAIL)))
    sys.exit(1)
print("ALL CHECKS PASSED against %s" % BACKEND)
