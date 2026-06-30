#!/usr/bin/env python3
"""Mirror the unified catalog to a remote DB — production-grade, incremental.

Targets: PocketBase and/or Firebase Firestore. The remote ends up mirroring the
local `games` and `sources` tables.

Design:
  * Deterministic record ids (hash of the natural key) -> every write is an
    idempotent upsert; re-running converges with no duplicates.
  * Content-hash cache (sync_cache.sqlite) -> only new/changed/removed records
    are pushed. A steady-state re-sync does ~zero writes.
  * Retry with exponential backoff on transient errors (429 / 5xx / network).
  * --reconcile self-heals: reconciles against the live remote (repairs drift,
    a lost cache, partial prior failures, or manual remote edits).

  python3 sync.py [pocketbase|firebase|both] [--dry-run] [--reconcile]
  (no target -> uses the configured sync_target)
"""
import os
import sys
import ssl
import json
import time
import hashlib
import sqlite3
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DB = os.path.join(DIR, "sync_cache.sqlite")
CTX = ssl.create_default_context()
WORKERS = 12

GAME_COLS = ["norm_key", "canonical_title", "sources_summary", "n_sources",
             "n_kinds", "has_emulation", "has_steam", "has_gog", "has_epic",
             "has_itch"]
SRC_COLS = ["game_norm", "source", "platform", "source_id", "title_raw", "detail"]
FB_BOOL_COLS = {"has_emulation", "has_steam", "has_gog", "has_epic", "has_itch"}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
#  catalog + keys + hashing
# --------------------------------------------------------------------------- #
def load_catalog():
    db = config.get("library_db")
    if not db or not os.path.exists(db):
        sys.exit("no catalog at %r — run update.sh first" % db)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    games = [dict(r) for r in con.execute(
        "SELECT %s FROM games" % ",".join(GAME_COLS))]
    sources = [dict(r) for r in con.execute(
        "SELECT g.norm_key AS game_norm, s.source, s.platform, s.source_id, "
        "s.title_raw, s.detail FROM sources s JOIN games g ON g.id=s.game_id")]
    con.close()
    return games, sources


def game_key(r):
    return r["norm_key"]


def src_key(r):
    # stable, collision-free identity for a source row (excludes `detail` so a
    # region/detail change is an in-place update, not a delete+recreate).
    return "|".join((r["game_norm"], r["source"], r["platform"],
                     r["source_id"], r["title_raw"]))


def row_hash(row):
    return hashlib.sha1(
        json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def compute_delta(rows, keyfn, cache):
    """Return desired{key:(row,hash)}, upsert_keys, delete_keys vs the cache."""
    desired = {}
    for r in rows:
        k = keyfn(r)
        desired[k] = (r, row_hash(r))
    upserts = [k for k, (_, h) in desired.items() if cache.get(k) != h]
    deletes = [k for k in cache if k not in desired]
    return desired, upserts, deletes


# --------------------------------------------------------------------------- #
#  sync-state cache (content hashes per backend/collection)
# --------------------------------------------------------------------------- #
def _cache_con():
    con = sqlite3.connect(CACHE_DB)
    con.execute("CREATE TABLE IF NOT EXISTS sync_state("
                "backend TEXT, collection TEXT, key TEXT, hash TEXT, "
                "PRIMARY KEY(backend, collection, key))")
    return con


def cache_load(backend, coll):
    con = _cache_con()
    d = {k: h for k, h in con.execute(
        "SELECT key,hash FROM sync_state WHERE backend=? AND collection=?",
        (backend, coll))}
    con.close()
    return d


def cache_save(backend, coll, desired):
    con = _cache_con()
    con.execute("DELETE FROM sync_state WHERE backend=? AND collection=?",
                (backend, coll))
    con.executemany(
        "INSERT INTO sync_state(backend,collection,key,hash) VALUES(?,?,?,?)",
        [(backend, coll, k, h) for k, (_, h) in desired.items()])
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
#  HTTP with retry/backoff
# --------------------------------------------------------------------------- #
_RETRY = {429, 500, 502, 503, 504}


def http(method, url, headers=None, body=None, tries=4):
    data = json.dumps(body).encode() if body is not None else None
    delay = 1.0
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, context=CTX, timeout=120) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = raw.decode("utf-8", "replace")
            if e.code in _RETRY and attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return e.code, parsed
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return 0, str(e)
    return 0, "retries exhausted"


def threaded(fn, items):
    """Run fn over items concurrently; return True only if all succeed."""
    if not items:
        return True
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        return all(ex.map(fn, items))


# --------------------------------------------------------------------------- #
#  PocketBase
# --------------------------------------------------------------------------- #
PB_GAME_FIELDS = (
    [{"name": c, "type": "text"} for c in ("norm_key", "canonical_title",
                                           "sources_summary")] +
    [{"name": c, "type": "number"} for c in ("n_sources", "n_kinds")] +
    [{"name": c, "type": "bool"} for c in FB_BOOL_COLS])
PB_SRC_FIELDS = [{"name": c, "type": "text"} for c in SRC_COLS]


def pb_id(key):
    return hashlib.sha1(("pb:" + key).encode()).hexdigest()[:15]


def pb_auth(url, email, pw):
    for ep in ("/api/collections/_superusers/auth-with-password",
               "/api/admins/auth-with-password"):
        st, resp = http("POST", url + ep, body={"identity": email, "password": pw})
        if st == 200 and isinstance(resp, dict) and resp.get("token"):
            return resp["token"]
    sys.exit("PocketBase auth failed (%s): %s" % (st, resp))


def pb_ensure_collection(url, hdr, name, fields):
    st, _ = http("GET", "%s/api/collections/%s" % (url, name), headers=hdr)
    if st == 200:
        return
    for key in ("fields", "schema"):
        st, resp = http("POST", url + "/api/collections", headers=hdr,
                        body={"name": name, "type": "base", key: fields})
        if st in (200, 201):
            log("  created PocketBase collection %r" % name)
            return
    sys.exit("could not create PocketBase collection %r (%s): %s" % (name, st, resp))


def pb_all_ids(url, hdr, coll):
    ids, page = [], 1
    while True:
        st, resp = http("GET", "%s/api/collections/%s/records?perPage=500&page=%d"
                        "&fields=id" % (url, coll, page), headers=hdr)
        if st != 200 or not isinstance(resp, dict):
            break
        ids += [it["id"] for it in (resp.get("items") or [])]
        if page >= (resp.get("totalPages") or 1):
            break
        page += 1
    return ids


def _pb_body(coll, key, row):
    b = dict(row)
    b["id"] = pb_id(key)
    return b


def pb_one_upsert(url, hdr, coll, key, row, exists):
    """Idempotent single-record upsert with create<->patch self-heal."""
    body = _pb_body(coll, key, row)
    rid = body["id"]
    first = "PATCH" if exists else "POST"
    order = [first, "PATCH" if first == "POST" else "POST"]
    for m in order:
        if m == "POST":
            st, _ = http("POST", "%s/api/collections/%s/records" % (url, coll),
                         headers=hdr, body=body)
        else:
            st, _ = http("PATCH", "%s/api/collections/%s/records/%s" % (url, coll, rid),
                         headers=hdr, body=body)
        if st in (200, 201):
            return True
    return False


def pb_one_delete(url, hdr, coll, rid):
    st, _ = http("DELETE", "%s/api/collections/%s/records/%s" % (url, coll, rid),
                 headers=hdr)
    return st in (200, 204, 404)        # 404 == already gone


def pb_write(url, hdr, coll, upserts, deletes, cache):
    """upserts: [(key,row)]; deletes: [record_id]. Batch first, else per-record."""
    reqs = []
    for key, row in upserts:
        body = _pb_body(coll, key, row)
        if key in cache:
            reqs.append({"method": "PATCH",
                         "url": "/api/collections/%s/records/%s" % (coll, body["id"]),
                         "body": body})
        else:
            reqs.append({"method": "POST",
                         "url": "/api/collections/%s/records" % coll, "body": body})
    for rid in deletes:
        reqs.append({"method": "DELETE",
                     "url": "/api/collections/%s/records/%s" % (coll, rid)})
    if not reqs:
        return
    if _pb_batch(url, hdr, reqs):
        return
    log("  (batch unavailable -> per-record with self-heal)")
    ok = threaded(lambda kr: pb_one_upsert(url, hdr, coll, kr[0], kr[1],
                                           kr[0] in cache), upserts)
    ok = threaded(lambda rid: pb_one_delete(url, hdr, coll, rid), deletes) and ok
    if not ok:
        sys.exit("  PocketBase %s: some writes failed after retries" % coll)


def _pb_batch(url, hdr, reqs, size=100):
    for i in range(0, len(reqs), size):
        st, _ = http("POST", url + "/api/batch", headers=hdr,
                     body={"requests": reqs[i:i + size]})
        if st not in (200, 204):
            return False
    return True


def pb_sync(games, sources, dry, reconcile):
    if dry:
        log("PocketBase [dry-run] %d games + %d sources (delta computed at run)" %
            (len(games), len(sources)))
        return
    url = config.get("pocketbase_url").rstrip("/")
    email = config.get("pocketbase_admin_email")
    pw = config.pocketbase_password()
    if not (url and email and pw):
        sys.exit("PocketBase not configured (need pocketbase_url, "
                 "pocketbase_admin_email, and a password)")
    log("PocketBase -> %s%s" % (url, "  [reconcile]" if reconcile else ""))
    token = pb_auth(url, email, pw)
    hdr = {"Authorization": token}
    for name, fields in (("games", PB_GAME_FIELDS), ("sources", PB_SRC_FIELDS)):
        pb_ensure_collection(url, hdr, name, fields)

    for coll, rows, keyfn in (("games", games, game_key),
                              ("sources", sources, src_key)):
        cache = {} if reconcile else cache_load("pocketbase", coll)
        desired, upsert_keys, delete_keys = compute_delta(rows, keyfn, cache)
        up = [(k, desired[k][0]) for k in upsert_keys]
        if reconcile:
            up = [(k, desired[k][0]) for k in desired]   # force every record
            want = {pb_id(k) for k in desired}
            dels = [rid for rid in pb_all_ids(url, hdr, coll) if rid not in want]
        else:
            dels = [pb_id(k) for k in delete_keys]
        pb_write(url, hdr, coll, up, dels, cache)
        cache_save("pocketbase", coll, desired)
        log("  %s: %d upserted, %d deleted (%d unchanged)" %
            (coll, len(up), len(dels), len(desired) - len(up)))


# --------------------------------------------------------------------------- #
#  Firebase (Firestore REST)
# --------------------------------------------------------------------------- #
def fb_token(sa_path):
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests as gar
    except ImportError:
        sys.exit("Firebase sync needs google-auth: "
                 "python3 -m pip install --user -r requirements-firebase.txt")
    creds = service_account.Credentials.from_service_account_file(
        sa_path, scopes=["https://www.googleapis.com/auth/datastore"])
    creds.refresh(gar.Request())
    return creds.token


def _fb_value(v):
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    return {"stringValue": "" if v is None else str(v)}


def _fb_fields(row):
    out = {}
    for k, v in row.items():
        out[k] = {"booleanValue": bool(v)} if k in FB_BOOL_COLS else _fb_value(v)
    return out


def fb_id(key):
    return ("".join(c if c.isalnum() else "_" for c in str(key)))[:1400] or "_"


def fb_sync(games, sources, dry, reconcile):
    if dry:
        log("Firestore [dry-run] %d games + %d sources (delta computed at run)" %
            (len(games), len(sources)))
        return
    pid = config.get("firebase_project_id")
    sa = config.get("firebase_sa_json")
    pre = config.get("firebase_collection_prefix") or ""
    if not (pid and sa):
        sys.exit("Firebase not configured (need firebase_project_id + firebase_sa_json)")
    dbid = config.get("firebase_database") or "(default)"
    token = fb_token(sa)
    hdr = {"Authorization": "Bearer " + token}
    base = ("https://firestore.googleapis.com/v1/projects/%s/databases/%s"
            "/documents" % (pid, dbid))
    log("Firestore -> project %s%s" % (pid, "  [reconcile]" if reconcile else ""))

    def commit(writes):
        for i in range(0, len(writes), 400):
            st, resp = http("POST", base + ":commit", headers=hdr,
                            body={"writes": writes[i:i + 400]})
            if st != 200:
                sys.exit("Firestore commit failed (%s): %s" % (st, resp))

    def remote_ids(col):
        ids, tok = set(), ""
        while True:
            u = "%s/%s?pageSize=300" % (base, col)
            if tok:
                u += "&pageToken=" + tok
            st, resp = http("GET", u, headers=hdr)
            if st != 200 or not isinstance(resp, dict):
                break
            for d in resp.get("documents", []):
                ids.add(d["name"].rsplit("/", 1)[-1])
            tok = resp.get("nextPageToken") or ""
            if not tok:
                break
        return ids

    for coll, rows, keyfn in (("games", games, game_key),
                              ("sources", sources, src_key)):
        col = pre + coll
        cache = {} if reconcile else cache_load("firebase", coll)
        desired, upsert_keys, delete_keys = compute_delta(rows, keyfn, cache)
        if reconcile:
            upsert_keys = list(desired)
            want = {fb_id(k) for k in desired}
            del_ids = [i for i in remote_ids(col) if i not in want]
        else:
            del_ids = [fb_id(k) for k in delete_keys]
        writes = [{"update": {"name": "%s/%s/%s" % (base, col, fb_id(k)),
                              "fields": _fb_fields(desired[k][0])}}
                  for k in upsert_keys]
        writes += [{"delete": "%s/%s/%s" % (base, col, i)} for i in del_ids]
        commit(writes)
        cache_save("firebase", coll, desired)
        log("  %s: %d upserted, %d deleted (%d unchanged)" %
            (coll, len(upsert_keys), len(del_ids), len(desired) - len(upsert_keys)))


# --------------------------------------------------------------------------- #
def main(argv):
    dry = "--dry-run" in argv
    reconcile = "--reconcile" in argv
    rest = [a for a in argv if not a.startswith("--")]
    target = rest[0] if rest else config.get("sync_target")
    if not target:
        sys.exit("no sync target — pass pocketbase|firebase|both, or set sync_target")
    if target not in ("pocketbase", "firebase", "both"):
        sys.exit("unknown target %r" % target)
    games, sources = load_catalog()
    log("catalog: %d games, %d source rows" % (len(games), len(sources)))
    if target in ("pocketbase", "both"):
        pb_sync(games, sources, dry, reconcile)
    if target in ("firebase", "both"):
        fb_sync(games, sources, dry, reconcile)
    log("sync done.")


if __name__ == "__main__":
    main(sys.argv[1:])
