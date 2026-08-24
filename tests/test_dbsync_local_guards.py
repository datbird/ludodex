#!/usr/bin/env python3
"""The LOCAL side of the two-way sync must obey the same rule as the remote side.

The remote adapters already say it in as many words: a failed read "MUST raise, never
return partial", because the three-way merge cannot tell "there are no rows" from "I
could not look", and reads the second as the first. The local reader broke that rule in
three ways, and each one destroys data in a different direction:

  * A LOCKED OR UNREADABLE DATABASE read as an empty store. Every row the shadow
    remembers then looks like a deliberate local DELETE, and the sync pushes those
    deletes to the backing store. The scheduler runs this unattended, so nobody is
    watching when it happens.
  * A VANISHED DATABASE read as "the user deleted everything". A store file that existed
    at the last sync and is gone now means a bad mount or a wrong LUDODEX_DATA far more
    often than it means intent. Refusing is recoverable; deleting the backing copy is not.
  * A NARROW COLUMN LIST taken as the whole row. When the local table does not exist yet,
    the reader fell back to the KEY columns alone; the pull then created a key-only table
    and dropped value/origin/... , and a push serialised key-only blobs OVER the remote's
    full rows. overrides.py carries a hand-written ALTER-add to heal the first half of
    that; ownership, pins and framing carry nothing.

An absent store on a machine that has never synced it is still a legitimate empty pull.
The distinction is the shadow: it is the record that these rows were here before.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support

DATA = test_support.isolate("ludodex-dbsync-guard-")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "ludodex"))

import dbsync                                                    # noqa: E402
import remote_db as _s                                           # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


STORE = {"name": "guard_tags", "db": "guard-tags.sqlite", "table": "user_tags",
         "key": ["norm_key", "tag"]}


def _seed_local(rows):
    con = sqlite3.connect(os.path.join(DATA, STORE["db"]))
    con.execute("CREATE TABLE IF NOT EXISTS user_tags(norm_key TEXT, tag TEXT, "
                "origin TEXT, created REAL, PRIMARY KEY(norm_key, tag))")
    con.executemany("INSERT OR REPLACE INTO user_tags VALUES(?,?,?,?)", rows)
    con.commit()
    con.close()


class FakeBackend:
    """Records what the sync asked it to do. Never partial, never lossy."""
    id = "fake"

    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.deleted = []
        self.written = {}

    def ensure(self, store, cols):
        pass

    def read_all(self, store, cols):
        return {k: dict(v) for k, v in self.rows.items()}

    def write(self, store, cols, upserts, deletes, remote_keys):
        self.deleted.extend(deletes)
        for k, row in upserts.items():
            # the adapters serialise only `cols`; anything missing from that list is
            # dropped from the backing copy, so record exactly what would survive.
            self.written[k] = {c: dbsync._cell(row.get(c)) for c in cols}
            self.rows[k] = self.written[k]
        for k in deletes:
            self.rows.pop(k, None)


def _with_backend(backend, fn):
    prev = dict(dbsync.BACKENDS)
    dbsync.BACKENDS["fake"] = lambda: backend
    try:
        return fn()
    finally:
        dbsync.BACKENDS.clear()
        dbsync.BACKENDS.update(prev)


def _clear_shadow():
    con = _s._cache_con()
    con.execute("DELETE FROM sync_state")
    con.commit()
    con.close()


def main():
    print("dbsync local-side guards")

    # ---- a read that FAILED is never an empty store ----------------------------- #
    # A genuine lock needs a second connection to sit on an EXCLUSIVE transaction and
    # then a five-second busy wait to observe; substituting the error the driver would
    # raise tests the same branch deterministically and instantly.
    _seed_local([("sonic", "fav", "manual", 1.0)])
    real_connect = sqlite3.connect

    class Locked:
        def __init__(self, *a, **k):
            pass

        row_factory = None

        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

        def close(self):
            pass

    sqlite3.connect = lambda *a, **k: Locked()
    try:
        raised = None
        try:
            dbsync._local_read(STORE)
        except Exception as e:                                    # noqa: BLE001
            raised = e
    finally:
        sqlite3.connect = real_connect
    check("a locked local database raises instead of reading as empty",
          raised is not None)
    check("the failure names the store so the operator can act",
          raised is not None and STORE["name"] in str(raised))

    # ---- a store that vanished is not a delete ---------------------------------- #
    _clear_shadow()
    back = FakeBackend()
    _seed_local([("sonic", "fav", "manual", 1.0), ("mario", "fav", "manual", 1.0)])
    prev_stores = dbsync.STORES
    dbsync.STORES = [STORE]
    try:
        _with_backend(back, lambda: dbsync.sync_all("fake"))
        check("first sync pushes the local rows to the backing store", len(back.rows) == 2)

        os.remove(os.path.join(DATA, STORE["db"]))               # bad mount, not intent
        rep = _with_backend(back, lambda: dbsync.sync_all("fake"))
        check("a vanished local store deletes NOTHING remotely", back.deleted == [])
        check("the backing store still holds both rows", len(back.rows) == 2)
        st = rep["stores"][0]
        check("the report says the store was skipped and why",
              bool(st.get("error")) and st.get("pushed_deleted") == 0)

        # ---- recovery is the one-way restore, and it heals the missing table ---- #
        # sync_all keeps refusing while the shadow remembers rows, which is the point:
        # the way back is the documented one-way pull, not a merge that guesses.
        _with_backend(back, lambda: dbsync.restore_from_remote("fake"))
        con = real_connect(os.path.join(DATA, STORE["db"]))
        cols = {r[1] for r in con.execute("PRAGMA table_info(user_tags)")}
        got = dict(con.execute("SELECT norm_key, origin FROM user_tags"))
        con.close()
        check("a restore onto a machine without the table keeps the non-key columns",
              {"norm_key", "tag", "origin", "created"} <= cols)
        check("and the restored rows carry their values, not just their keys",
              got.get("sonic") == "manual")
    finally:
        dbsync.STORES = prev_stores

    # ---- a narrow local table never narrows the backing copy -------------------- #
    _clear_shadow()
    os.remove(os.path.join(DATA, STORE["db"]))
    con = real_connect(os.path.join(DATA, STORE["db"]))
    con.execute("CREATE TABLE user_tags(norm_key TEXT, tag TEXT, "
                "PRIMARY KEY(norm_key, tag))")                   # stripped by an old pull
    con.execute("INSERT INTO user_tags VALUES('sonic','fav')")
    con.commit()
    con.close()
    back = FakeBackend({"sonic\x1ffav": {"norm_key": "sonic", "tag": "fav",
                                         "origin": "manual", "created": "1.0"}})
    dbsync.STORES = [STORE]
    try:
        _with_backend(back, lambda: dbsync.sync_all("fake"))
    finally:
        dbsync.STORES = prev_stores
    check("a locally-narrowed table does not strip the backing store's columns",
          back.rows["sonic\x1ffav"].get("origin") == "manual")

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
