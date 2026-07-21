"""Shared PocketBase / Firestore plumbing.

Extracted from the old sync.py. That module did two unrelated things: it held these
primitives, and it implemented a one-way "publish the catalog to PocketBase/Firestore"
mirror. The mirror was retired (nothing ever read what it published, and its name kept
being confused with the two-way backing store that actually protects your data), but
dbsync.py — the backing store — is built on these primitives, so they live here now under
a name that says what they are.

Nothing in here knows about games, sources, or ludodex's schema: it is HTTP-with-retry,
PocketBase auth/collection/record helpers, and a Firestore service-account token mint.
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


DATA = os.environ.get("LUDODEX_DATA", DIR)


CACHE_DB = os.path.join(DATA, "sync_cache.sqlite")


CTX = ssl.create_default_context()


WORKERS = 12


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def _cache_con():
    con = sqlite3.connect(CACHE_DB)
    con.execute("CREATE TABLE IF NOT EXISTS sync_state("
                "backend TEXT, collection TEXT, key TEXT, hash TEXT, "
                "PRIMARY KEY(backend, collection, key))")
    return con


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
    st, resp = http("GET", "%s/api/collections/%s" % (url, name), headers=hdr)
    if st == 200 and isinstance(resp, dict):
        # migrate: append any fields the live collection is missing (preserve ids)
        key = "fields" if "fields" in resp else "schema"
        existing = resp.get(key) or []
        have = {f.get("name") for f in existing}
        missing = [f for f in fields if f["name"] not in have]
        if missing:
            mst, _ = http("PATCH", "%s/api/collections/%s" % (url, resp["id"]),
                          headers=hdr, body={key: existing + missing})
            log("  %s: added %d field(s) [%s]" %
                (name, len(missing), "ok" if mst == 200 else "patch failed %s" % mst))
        return
    for key in ("fields", "schema"):
        st, resp = http("POST", url + "/api/collections", headers=hdr,
                        body={"name": name, "type": "base", key: fields})
        if st in (200, 201):
            log("  created PocketBase collection %r" % name)
            return
    sys.exit("could not create PocketBase collection %r (%s): %s" % (name, st, resp))


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
        # RuntimeError, not sys.exit: this runs inside the server's backing-store job,
        # where exiting takes down the worker instead of reporting a failed sync.
        raise RuntimeError("PocketBase %s: some writes failed after retries" % coll)


def _pb_batch(url, hdr, reqs, size=100):
    for i in range(0, len(reqs), size):
        st, _ = http("POST", url + "/api/batch", headers=hdr,
                     body={"requests": reqs[i:i + size]})
        if st not in (200, 204):
            return False
    return True


def fb_token(sa_path):
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests as gar
    except ImportError:
        # RuntimeError, not sys.exit: this is called from the server's backing-store job
        # too, where exiting would take down the worker instead of reporting a failure.
        raise RuntimeError("Firebase needs google-auth: "
                           "python3 -m pip install -r requirements-firebase.txt")
    creds = service_account.Credentials.from_service_account_file(
        sa_path, scopes=["https://www.googleapis.com/auth/datastore"])
    creds.refresh(gar.Request())
    return creds.token
