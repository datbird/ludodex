"""Two-way sync of ludodex's durable user stores with a pluggable remote backend.

SQLite stays the fast LOCAL working store; the remote (PocketBase first, SQL adapters to
follow) is the durable BACKING store. Reconciliation is a three-way merge: local vs remote
vs the last-synced "shadow" (per-record hashes kept from the previous sync). That cleanly
separates a genuine local change from a genuine remote one, so adds / edits / deletes flow
in BOTH directions without a full overwrite. A record changed on BOTH sides since the last
sync is a conflict, resolved last-writer-wins by the record's timestamp column (else the
edit beats a delete — never lose data silently — else local wins).

Only user-authored durable stores sync; the catalog (game-library.sqlite) is a build OUTPUT
and is regenerated locally from these, so it never needs syncing.
"""
import hashlib
import json
import os
import sqlite3
import time

import config
import remote_db as _s                # http + PocketBase helpers + CACHE_DB

DATA = os.environ.get("LUDODEX_DATA",
                      os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# name: sync id (also the remote collection suffix). db/table: where it lives locally.
# key: the natural primary-key columns (portable across machines — never an autoincrement
# id). Columns are introspected from the table, so only the key must be declared here.
STORES = [
    {"name": "user_tags",    "db": "tags.sqlite",          "table": "user_tags",
     "key": ["norm_key", "tag"]},
    {"name": "overrides",    "db": "attr-overrides.sqlite", "table": "overrides",
     "key": ["norm_key", "kind"]},
    {"name": "art_pins",     "db": "pins.sqlite",           "table": "pins",
     "key": ["norm_key", "kind", "provider", "ref"]},
    {"name": "framing",      "db": "framing.sqlite",        "table": "framing",
     "key": ["norm_key", "kind"]},
    {"name": "hero_pref",    "db": "framing.sqlite",        "table": "hero_pref",
     "key": ["norm_key"]},
    {"name": "manual_games", "db": "manual-games.sqlite",   "table": "manual_games",
     "key": ["norm_key", "source", "platform"]},
    {"name": "ownership",    "db": "ownership.sqlite",      "table": "ownership",
     # the FULL natural PK — a reduced key made a pull recreate the table with the
     # wrong PRIMARY KEY (and collapse distinct per-platform/state rows)
     "key": ["norm_key", "form", "platform", "state"]},
]
_TS_COLS = ("updated", "updated_at", "modified", "mtime", "created", "added", "ts")
SEP = "\x1f"                            # composite-key join (never appears in values)


def _db_path(store):
    return os.path.join(DATA, store["db"])


def _columns(con, table):
    return [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]


def _cell(v):
    """Canonical string form of a cell. Remote backends (PocketBase text fields, and later
    SQL) coerce numbers to strings, so we compare/hash EVERYTHING as its string form — else
    a local float `created` and its stringified remote copy would hash differently and
    re-sync forever. None and '' both canonicalize to '' (a missing cell)."""
    return "" if v is None else str(v)


def _key_of(store, row):
    return SEP.join(_cell(row.get(c)) for c in store["key"])


def _hash(row, cols):
    """Stable content hash over the syncable columns, each canonicalized (see _cell) so a
    round-trip through a text-typed remote store is a no-op, not a spurious change."""
    return hashlib.sha1(json.dumps({c: _cell(row.get(c)) for c in cols},
                                   sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _ts(row):
    for c in _TS_COLS:
        if c in row and row[c] not in (None, ""):
            try:
                return float(row[c])
            except (TypeError, ValueError):
                pass
    return 0.0


# --------------------------------------------------------------------------- #
#  local store I/O (generic over any table with a natural key)
# --------------------------------------------------------------------------- #
class LocalReadError(RuntimeError):
    """The local store could not be read. NEVER the same thing as "it holds no rows"."""


def _local_read(store):
    """({key: row_dict}, syncable columns, present).

    `present` is False only when the database file or the table genuinely does not exist
    yet — a store this machine has never used, which is a legitimate empty pull.

    A read that FAILED is never reported as an empty store. The three-way merge cannot
    tell "no rows" from "could not look", so it reads a locked or unreadable database as
    the user having deleted every row, and pushes those deletes to the backing store. The
    remote adapters have followed the opposite rule from the start ("MUST raise, never
    return partial"); this is the same rule on the local side."""
    path = _db_path(store)
    if not os.path.exists(path):
        return {}, list(store["key"]), False
    try:
        con = sqlite3.connect(path)
    except sqlite3.Error as e:
        raise LocalReadError("could not open the local store for %s (%s): %s"
                             % (store["name"], path, e))
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=8000")
        cols = _columns(con, store["table"])
        if not cols:
            return {}, list(store["key"]), False
        sync_cols = [c for c in cols if c.lower() != "id" or c in store["key"]]
        rows = {}
        for r in con.execute("SELECT %s FROM %s" % (",".join(sync_cols), store["table"])):
            row = {c: r[c] for c in sync_cols}
            rows[_key_of(store, row)] = row
        return rows, sync_cols, True
    except sqlite3.Error as e:
        raise LocalReadError("could not read the local store for %s (%s): %s"
                             % (store["name"], path, e))
    finally:
        con.close()


def _widen(cols, *rowmaps):
    """The column list must cover every column EITHER side carries.

    Falling back to the key columns alone (an absent local table) made a pull create a
    key-only table and a push serialise key-only blobs over the remote's full rows — the
    stripping overrides.py has to hand-heal with an ALTER-add."""
    out = list(cols)
    for rows in rowmaps:
        for row in rows.values():
            for c in row:
                if c not in out:
                    out.append(c)
    return out


def _local_apply(store, cols, upserts, deletes):
    path = _db_path(store)
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA busy_timeout=8000")
        # make sure the table exists (a store never used locally yet) with EVERY column
        # the incoming rows carry, and widen one an earlier narrow pull already created.
        cols = _widen(cols, upserts)
        con.execute("CREATE TABLE IF NOT EXISTS %s (%s, PRIMARY KEY(%s))"
                    % (store["table"], ",".join("%s TEXT" % c for c in cols),
                       ",".join(store["key"])))
        have = {r[1] for r in con.execute("PRAGMA table_info(%s)" % store["table"])}
        for c in cols:
            if c not in have:
                con.execute("ALTER TABLE %s ADD COLUMN %s TEXT" % (store["table"], c))
        if upserts:
            ph = ",".join("?" * len(cols))
            con.executemany(
                "INSERT OR REPLACE INTO %s(%s) VALUES(%s)"
                % (store["table"], ",".join(cols), ph),
                [[row.get(c) for c in cols] for row in upserts.values()])
        if deletes:
            where = " AND ".join("%s IS ?" % c for c in store["key"])
            con.executemany("DELETE FROM %s WHERE %s" % (store["table"], where),
                            [k.split(SEP) for k in deletes])
        con.commit()
    finally:
        con.close()


# --------------------------------------------------------------------------- #
#  three-way merge (pure) — the correctness core
# --------------------------------------------------------------------------- #
def merge(local, remote, shadow, cols):
    """local/remote: {key: row}; shadow: {key: hash} from the last sync. Returns
    (local_upserts, local_deletes, remote_upserts, remote_deletes, new_shadow)."""
    lu, ld, ru, rd, new_shadow = {}, [], {}, [], {}
    for key in set(local) | set(remote) | set(shadow):
        lrow, rrow = local.get(key), remote.get(key)
        lh = _hash(lrow, cols) if lrow is not None else None
        rh = _hash(rrow, cols) if rrow is not None else None
        sh = shadow.get(key)
        if lh == rh:                          # already in agreement (same, or both gone)
            if lh is not None:
                new_shadow[key] = lh
            continue
        l_changed, r_changed = lh != sh, rh != sh
        take_local = None
        if l_changed and not r_changed:
            take_local = True                 # only local moved → push local
        elif r_changed and not l_changed:
            take_local = False                # only remote moved → pull remote
        else:                                 # both moved since last sync → conflict
            if lrow is None:                  # local delete vs remote edit → keep the edit
                take_local = False
            elif rrow is None:                # remote delete vs local edit → keep the edit
                take_local = True
            else:                             # edit vs edit → last-writer-wins (else local)
                take_local = _ts(lrow) >= _ts(rrow)
        if take_local:
            if lrow is None:
                rd.append(key)
            else:
                ru[key] = lrow
                new_shadow[key] = lh
        else:
            if rrow is None:
                ld.append(key)
            else:
                lu[key] = rrow
                new_shadow[key] = rh
    return lu, ld, ru, rd, new_shadow


# --------------------------------------------------------------------------- #
#  shadow state (last-synced hashes) — reuses sync.py's sync_state table
# --------------------------------------------------------------------------- #
def _shadow_load(backend, name):
    con = _s._cache_con()
    d = {k: h for k, h in con.execute(
        "SELECT key,hash FROM sync_state WHERE backend=? AND collection=?",
        (backend, name))}
    con.close()
    return d


def _shadow_save(backend, name, shadow):
    con = _s._cache_con()
    con.execute("DELETE FROM sync_state WHERE backend=? AND collection=?", (backend, name))
    con.executemany("INSERT INTO sync_state(backend,collection,key,hash) VALUES(?,?,?,?)",
                    [(backend, name, k, h) for k, h in shadow.items()])
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
#  PocketBase backend adapter (document store)
# --------------------------------------------------------------------------- #
class PocketBaseBackend:
    id = "pocketbase"

    def __init__(self):
        self.url = (config.get("pocketbase_url") or "").rstrip("/")
        email = config.get("pocketbase_admin_email")
        pw = config.pocketbase_password()
        if not (self.url and email and pw):
            raise RuntimeError("PocketBase not configured (pocketbase_url, "
                               "pocketbase_admin_email, password)")
        self.hdr = {"Authorization": _s.pb_auth(self.url, email, pw)}

    def _coll(self, store):
        return "ludodex_" + store["name"]

    def ensure(self, store, cols):
        # store the row as a single JSON blob under `data`, keyed by `k`. Only two fields,
        # NEITHER of which is a PocketBase system field (id/created/updated) — so a store
        # column named `created`/`updated` can't collide, and types round-trip exactly.
        _s.pb_ensure_collection(self.url, self.hdr, self._coll(store),
                                [{"name": "k", "type": "text"},
                                 {"name": "data", "type": "text"}])

    def read_all(self, store, cols):
        coll, out, page = self._coll(store), {}, 1
        while True:
            st, resp = _s.http("GET", "%s/api/collections/%s/records?perPage=500&page=%d"
                               % (self.url, coll, page), headers=self.hdr)
            if st == 404 and page == 1:
                return {}                   # collection not created yet — genuinely empty
            if st != 200 or not isinstance(resp, dict):
                # MUST raise, never return partial. A short read looks to the merge engine
                # like "the remote deleted these records", and it would delete them locally.
                raise RuntimeError("PocketBase read failed (%s) on %s page %d: %s"
                                   % (st, coll, page, str(resp)[:150]))
            for it in (resp.get("items") or []):
                try:
                    row = json.loads(it.get("data") or "{}")
                except ValueError:
                    continue
                out[it.get("k") or _key_of(store, row)] = row
            if page >= (resp.get("totalPages") or 1):
                break
            page += 1
        return out

    def write(self, store, cols, upserts, deletes, remote_keys):
        coll = self._coll(store)
        ups = []
        for key, row in upserts.items():
            data = json.dumps({c: _cell(row.get(c)) for c in cols},
                              sort_keys=True, ensure_ascii=False)
            ups.append((key, {"id": _s.pb_id(key), "k": key, "data": data}))
        dels = [_s.pb_id(k) for k in deletes]
        _s.pb_write(self.url, self.hdr, coll, ups, dels, set(remote_keys))


# --------------------------------------------------------------------------- #
#  SQL backend adapters (Postgres / Supabase / MySQL) — one table per store,
#  ludodex_<name>(k, data) with data = the row's JSON blob (same shape as
#  PocketBase, so the three-way merge engine is identical across every backend).
# --------------------------------------------------------------------------- #
class _SqlBackend:
    key_type = "TEXT"
    text_type = "TEXT"

    def _conn(self):                       # subclass: a fresh DB connection
        raise NotImplementedError

    def _upsert_sql(self, table):          # subclass: dialect INSERT..UPSERT on (k)
        raise NotImplementedError

    def _table(self, store):
        return "ludodex_" + store["name"]

    def ensure(self, store, cols):
        con = self._conn()
        try:
            cur = con.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS %s (k %s PRIMARY KEY, data %s)"
                        % (self._table(store), self.key_type, self.text_type))
            con.commit()
        finally:
            con.close()

    def read_all(self, store, cols):
        out = {}
        con = self._conn()
        try:
            cur = con.cursor()
            cur.execute("SELECT k, data FROM %s" % self._table(store))
            for k, data in cur.fetchall():
                try:
                    out[k] = json.loads(data)
                except (ValueError, TypeError):
                    pass
        finally:
            con.close()
        return out

    def write(self, store, cols, upserts, deletes, remote_keys):
        con = self._conn()
        try:
            cur = con.cursor()
            up = self._upsert_sql(self._table(store))
            for key, row in upserts.items():
                data = json.dumps({c: _cell(row.get(c)) for c in cols},
                                  sort_keys=True, ensure_ascii=False)
                cur.execute(up, (key, data))
            for key in deletes:
                cur.execute("DELETE FROM %s WHERE k=%%s" % self._table(store), (key,))
            con.commit()
        finally:
            con.close()


class PostgresBackend(_SqlBackend):
    id = "postgres"
    url_key = "postgres_url"
    prefix = "postgres"

    def __init__(self):
        import psycopg
        self._drv = psycopg
        url = config.get(self.url_key)
        if url:
            self.conninfo = url
        else:
            pw = config.get(self.prefix + "_password")
            host = config.get(self.prefix + "_host") or "localhost"
            if not (host and pw):
                raise RuntimeError("%s not configured (need %s_host + %s_password, or %s)"
                                   % (self.id, self.prefix, self.prefix, self.url_key))
            self.conninfo = ("host=%s port=%s dbname=%s user=%s password=%s" % (
                host, config.get(self.prefix + "_port") or "5432",
                config.get(self.prefix + "_db") or "ludodex",
                config.get(self.prefix + "_user") or "ludodex", pw))

    def _conn(self):
        return self._drv.connect(self.conninfo)

    def _upsert_sql(self, table):
        return ("INSERT INTO %s (k, data) VALUES (%%s, %%s) "
                "ON CONFLICT (k) DO UPDATE SET data = EXCLUDED.data" % table)


class SupabaseBackend(PostgresBackend):
    id = "supabase"
    url_key = "supabase_url"           # Supabase IS Postgres — just a connection string
    prefix = "supabase"


class MySQLBackend(_SqlBackend):
    id = "mysql"
    key_type = "VARCHAR(500)"
    text_type = "LONGTEXT"

    def __init__(self):
        import pymysql
        self._drv = pymysql
        pw = config.get("mysql_password")
        host = config.get("mysql_host") or "localhost"
        if not (host and pw):
            raise RuntimeError("mysql not configured (need mysql_host + mysql_password)")
        self.cfg = dict(host=host, port=int(config.get("mysql_port") or 3306),
                        database=config.get("mysql_db") or "ludodex",
                        user=config.get("mysql_user") or "ludodex",
                        password=pw, charset="utf8mb4", autocommit=False)

    def _conn(self):
        return self._drv.connect(**self.cfg)

    def _upsert_sql(self, table):
        # `VALUES(col)` in ON DUPLICATE is deprecated in 8.0.20+ but still works on mysql:8
        # and is the widest-compatible form (also MariaDB).
        return ("INSERT INTO %s (k, data) VALUES (%%s, %%s) "
                "ON DUPLICATE KEY UPDATE data = VALUES(data)" % table)


class FirestoreBackend:
    """Firebase/Firestore (document store). Each store is a collection ludodex_<name>; each
    record a document (id = hash of the key, real key kept in the `k` field) with a `data`
    field holding the row's JSON blob — same shape as PocketBase, so the merge engine is
    identical. Reuses sync.py's service-account token minting + Firestore REST."""
    id = "firebase"

    def __init__(self):
        pid = config.get("firebase_project_id")
        sa = config.get("firebase_sa_json")
        dbid = config.get("firebase_database") or "(default)"
        # Point at a local Firestore emulator instead of Google, for testing without a real
        # project or service account. Same env var the official SDKs use, so it also picks
        # up an emulator the surrounding tooling already exported. The emulator ignores
        # auth, so no service-account key is needed (or minted) in that mode.
        emu = os.environ.get("FIRESTORE_EMULATOR_HOST") or config.get("firestore_emulator_host")
        if emu:
            if not pid:
                pid = "ludodex-emulator"
            self.hdr = {"Authorization": "Bearer owner"}   # emulator accepts any token
            root = "http://%s/v1" % emu.replace("http://", "").rstrip("/")
        else:
            if not (pid and sa):
                raise RuntimeError("firebase not configured (firebase_project_id + "
                                   "firebase_sa_json)")
            self.hdr = {"Authorization": "Bearer " + _s.fb_token(sa)}
            root = "https://firestore.googleapis.com/v1"
        # TWO different things, easy to conflate: `base` is the URL we call, `docpath` is the
        # RESOURCE NAME prefix. A commit's update.name / delete must be the resource name
        # ("projects/../databases/../documents/coll/id") — passing the full URL there is a
        # 400 "Document name ... is not valid".
        self.docpath = "projects/%s/databases/%s/documents" % (pid, dbid)
        self.base = "%s/%s" % (root, self.docpath)

    def _coll(self, store):
        return "ludodex_" + store["name"]

    def _doc_id(self, key):
        return hashlib.sha1(("fs:" + key).encode()).hexdigest()[:40]

    def ensure(self, store, cols):
        pass                                # Firestore is schemaless — collections autocreate

    def read_all(self, store, cols):
        coll, out, tok = self._coll(store), {}, ""
        while True:
            u = "%s/%s?pageSize=300%s" % (self.base, coll, ("&pageToken=" + tok) if tok else "")
            st, resp = _s.http("GET", u, headers=self.hdr)
            if st == 404 and not tok:
                return {}                   # collection doesn't exist yet — genuinely empty
            if st != 200 or not isinstance(resp, dict):
                # MUST raise, never return what we have so far. A truncated read is
                # indistinguishable from "the remote deleted the rest", and the merge engine
                # would delete those records locally. An expired token (Google's last ~1h)
                # is exactly how this would fire in production.
                raise RuntimeError("Firestore read failed (%s) on %s: %s"
                                   % (st, coll, str(resp)[:150]))
            for d in resp.get("documents", []):
                f = d.get("fields", {})
                k = (f.get("k") or {}).get("stringValue")
                data = (f.get("data") or {}).get("stringValue")
                if k and data:
                    try:
                        out[k] = json.loads(data)
                    except ValueError:
                        pass
            tok = resp.get("nextPageToken") or ""
            if not tok:
                break
        return out

    def write(self, store, cols, upserts, deletes, remote_keys):
        coll, writes = self._coll(store), []
        for key, row in upserts.items():
            data = json.dumps({c: _cell(row.get(c)) for c in cols},
                              sort_keys=True, ensure_ascii=False)
            writes.append({"update": {
                "name": "%s/%s/%s" % (self.docpath, coll, self._doc_id(key)),
                "fields": {"k": {"stringValue": key}, "data": {"stringValue": data}}}})
        for key in deletes:
            writes.append({"delete": "%s/%s/%s" % (self.docpath, coll, self._doc_id(key))})
        for i in range(0, len(writes), 400):
            st, resp = _s.http("POST", self.base + ":commit", headers=self.hdr,
                               body={"writes": writes[i:i + 400]})
            if st != 200:
                raise RuntimeError("Firestore commit failed (%s): %s" % (st, str(resp)[:150]))


BACKENDS = {"pocketbase": PocketBaseBackend, "postgres": PostgresBackend,
            "supabase": SupabaseBackend, "mysql": MySQLBackend,
            "firebase": FirestoreBackend}


# --------------------------------------------------------------------------- #
#  orchestration
# --------------------------------------------------------------------------- #
def sync_all(backend_id="pocketbase", dry_run=False, only=None):
    """Two-way sync every store (or just `only`) against the backend. Returns a per-store
    summary of what moved each direction."""
    if backend_id not in BACKENDS:
        raise RuntimeError("unknown backend %r" % backend_id)
    backend = BACKENDS[backend_id]()
    shadow_key = "2way:" + backend_id
    stores = [s for s in STORES if not only or s["name"] in only]
    report = {"backend": backend_id, "dry_run": dry_run, "stores": [], "at": int(time.time())}
    for store in stores:
        try:
            local, cols, present = _local_read(store)
        except LocalReadError as e:
            # a read we could not make says nothing about what the store holds
            report["stores"].append({"name": store["name"], "error": str(e),
                                     "local": 0, "remote": 0, "pulled": 0,
                                     "pulled_deleted": 0, "pushed": 0, "pushed_deleted": 0})
            continue
        # union the local + key columns with a 'k' marker handled by the adapter
        if not dry_run:
            backend.ensure(store, cols)
        remote = backend.read_all(store, cols)
        shadow = _shadow_load(shadow_key, store["name"])
        if not present and shadow:
            # The store was here at the last sync and its file or table is gone now. That
            # is a bad mount or a wrong LUDODEX_DATA far more often than it is intent, and
            # every remembered row would read as a deliberate delete. Refusing is
            # recoverable; deleting the backing copy is not.
            report["stores"].append({
                "name": store["name"],
                "error": "local store %s is missing but the last sync recorded %d row(s); "
                         "refusing to treat that as a delete" % (store["db"], len(shadow)),
                "local": 0, "remote": len(remote), "pulled": 0, "pulled_deleted": 0,
                "pushed": 0, "pushed_deleted": 0})
            continue
        cols = _widen(cols, local, remote)
        lu, ld, ru, rd, new_shadow = merge(local, remote, shadow, cols)
        if not dry_run:
            _local_apply(store, cols, lu, ld)
            backend.write(store, cols, ru, rd, remote.keys())
            _shadow_save(shadow_key, store["name"], new_shadow)
        report["stores"].append({
            "name": store["name"], "local": len(local), "remote": len(remote),
            "pulled": len(lu), "pulled_deleted": len(ld),
            "pushed": len(ru), "pushed_deleted": len(rd)})
    return report


def restore_from_remote(backend_id="pocketbase", only=None, dry_run=False):
    """One-way PULL: rebuild the local stores from the remote, pushing NOTHING back.

    Deliberately not sync_all(). Restoring onto a machine whose local stores are empty or
    stale is exactly the case a two-way merge gets dangerously wrong: with a shadow that
    still remembers the old rows, every missing local record reads as a deliberate local
    DELETE, and a "restore" would erase the remote copy you were restoring from. So this
    only ever writes locally, and then rewrites the shadow to match what it pulled, leaving
    the next ordinary sync a clean no-op.

    Returns a per-store summary of what would be / was written."""
    if backend_id not in BACKENDS:
        raise RuntimeError("unknown backend %r" % backend_id)
    backend = BACKENDS[backend_id]()
    shadow_key = "2way:" + backend_id
    stores = [s for s in STORES if not only or s["name"] in only]
    report = {"backend": backend_id, "dry_run": dry_run, "stores": [],
              "at": int(time.time()), "restored": 0}
    for store in stores:
        local, cols, _present = _local_read(store)   # raises on a failed read, never partial
        remote = backend.read_all(store, cols)       # raises on a failed read, never partial
        cols = _widen(cols, local, remote)
        new_rows = {k: v for k, v in remote.items()
                    if k not in local or _hash(local[k], cols) != _hash(v, cols)}
        if not dry_run and remote:
            _local_apply(store, cols, remote, [])
            _shadow_save(shadow_key, store["name"],
                         {k: _hash(v, cols) for k, v in remote.items()})
        report["stores"].append({"name": store["name"], "remote": len(remote),
                                 "local_before": len(local), "written": len(new_rows)})
        report["restored"] += len(new_rows)
    return report


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    bid = next((a for a in sys.argv[1:] if not a.startswith("-")), "pocketbase")
    rep = sync_all(bid, dry_run=dry)
    print(json.dumps(rep, indent=2))
