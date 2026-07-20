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
import sync as _s                     # reuse http + PocketBase helpers + CACHE_DB

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
     "key": ["norm_key", "form"]},
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
def _local_read(store):
    """{key: row_dict} for every row, plus the syncable column list. Absent db => empty."""
    path = _db_path(store)
    if not os.path.exists(path):
        return {}, list(store["key"])
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        cols = _columns(con, store["table"])
        if not cols:
            return {}, list(store["key"])
        sync_cols = [c for c in cols if c.lower() != "id" or c in store["key"]]
        rows = {}
        for r in con.execute("SELECT %s FROM %s" % (",".join(sync_cols), store["table"])):
            row = {c: r[c] for c in sync_cols}
            rows[_key_of(store, row)] = row
        return rows, sync_cols
    except sqlite3.OperationalError:
        return {}, list(store["key"])
    finally:
        con.close()


def _local_apply(store, cols, upserts, deletes):
    path = _db_path(store)
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA busy_timeout=8000")
        # make sure the table exists (a store never used locally yet)
        con.execute("CREATE TABLE IF NOT EXISTS %s (%s, PRIMARY KEY(%s))"
                    % (store["table"], ",".join("%s TEXT" % c for c in cols),
                       ",".join(store["key"])))
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
            if st != 200 or not isinstance(resp, dict):
                break
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


BACKENDS = {"pocketbase": PocketBaseBackend}


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
        local, cols = _local_read(store)
        # union the local + key columns with a 'k' marker handled by the adapter
        if not dry_run:
            backend.ensure(store, cols)
        remote = backend.read_all(store, cols) if BACKENDS else {}
        shadow = _shadow_load(shadow_key, store["name"])
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


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    bid = next((a for a in sys.argv[1:] if not a.startswith("-")), "pocketbase")
    rep = sync_all(bid, dry_run=dry)
    print(json.dumps(rep, indent=2))
