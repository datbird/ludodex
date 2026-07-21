"""Offline protocol test for the Firestore backing-store adapter (task #2).

The live round-trip (test_dbsync_roundtrip.py's Firestore twin) needs a real project and
service-account key, which only the user can supply through Settings. This test proves
everything that does NOT need credentials, by standing a fake Firestore REST server in
front of sync.http:

  - read_all follows nextPageToken to the end (a >300-record store is one page in the
    API's eyes and would otherwise silently truncate, which the merge engine would read
    as "the remote deleted everything")
  - write batches commits at Firestore's 500-write transaction cap
  - document ids are stable and collision-free for the natural keys we use
  - the stored shape ({k, data:<json blob>}) round-trips byte-identically, so a re-sync
    with nothing changed is a genuine no-op rather than an endless re-push
  - deletes are emitted as delete writes

Usage: python3 test_dbsync_firestore.py     (no network, no creds)
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("LUDODEX_DATA", "/tmp/ludodex-fs-test")
os.makedirs(os.environ["LUDODEX_DATA"], exist_ok=True)

import config                                    # noqa: E402
import sync as _s                                # noqa: E402
import dbsync                                    # noqa: E402

FAIL = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAIL.append(label)


# --------------------------------------------------------------------------- #
#  fake Firestore
# --------------------------------------------------------------------------- #
DOCS = {}                     # coll -> {doc_id: {"k":..., "data":...}}
CALLS = {"list": 0, "commit": 0, "batch_sizes": []}
PAGE = 300                    # must match the adapter's pageSize


def fake_http(method, url, headers=None, body=None, tries=4):
    if method == "GET":
        CALLS["list"] += 1
        coll = url.split("/documents/")[1].split("?")[0]
        items = sorted(DOCS.get(coll, {}).items())
        tok = ""
        if "pageToken=" in url:
            tok = url.split("pageToken=")[1].split("&")[0]
        start = int(tok) if tok else 0
        page = items[start:start + PAGE]
        out = {"documents": [
            {"name": "%s/%s" % (coll, did),
             "fields": {"k": {"stringValue": rec["k"]},
                        "data": {"stringValue": rec["data"]}}}
            for did, rec in page]}
        if start + PAGE < len(items):
            out["nextPageToken"] = str(start + PAGE)
        return 200, out
    if method == "POST" and url.endswith(":commit"):
        CALLS["commit"] += 1
        writes = (body or {}).get("writes", [])
        CALLS["batch_sizes"].append(len(writes))
        if len(writes) > 500:
            return 400, {"error": "too many writes in one transaction"}
        for w in writes:
            if "update" in w:
                name = w["update"]["name"]
                coll, did = name.split("/documents/")[1].rsplit("/", 1)
                f = w["update"]["fields"]
                DOCS.setdefault(coll, {})[did] = {"k": f["k"]["stringValue"],
                                                  "data": f["data"]["stringValue"]}
            elif "delete" in w:
                coll, did = w["delete"].split("/documents/")[1].rsplit("/", 1)
                DOCS.get(coll, {}).pop(did, None)
        return 200, {"writeResults": [{} for _ in writes]}
    raise AssertionError("unexpected request %s %s" % (method, url))


_s.http = fake_http
_s.fb_token = lambda sa: "fake-token"
dbsync._s.http = fake_http
dbsync._s.fb_token = lambda sa: "fake-token"

_real_get = config.get
config.get = lambda k, *a, **kw: ({"firebase_project_id": "test-proj",
                                   "firebase_sa_json": "/dev/null",
                                   "firebase_database": "(default)"}.get(k)
                                  or _real_get(k, *a, **kw))

STORE = {"name": "user_tags", "key": ["norm_key", "tag"]}
COLS = ["norm_key", "tag", "created"]
be = dbsync.BACKENDS["firebase"]()

print("1. empty remote reads clean")
check(be.read_all(STORE, COLS) == {}, "no documents -> {}")

print("2. write then read round-trips identically")
rows = {"a\ttag1": {"norm_key": "a", "tag": "tag1", "created": "2026-01-01"},
        "b\ttag2": {"norm_key": "b", "tag": "tag2", "created": None},
        "c\tünïcode ✓": {"norm_key": "c", "tag": "ünïcode ✓", "created": 12345}}
be.write(STORE, COLS, rows, [], set())
got = be.read_all(STORE, COLS)
check(set(got) == set(rows), "all keys returned (got %d)" % len(got))
expect = {k: {c: dbsync._cell(v.get(c)) for c in COLS} for k, v in rows.items()}
check(got == expect, "values round-trip through _cell canonicalisation unchanged")

print("3. a re-write of identical data is a no-op for the merge engine")
before = json.dumps(DOCS, sort_keys=True)
be.write(STORE, COLS, rows, [], set())
check(json.dumps(DOCS, sort_keys=True) == before,
      "identical re-push leaves the stored documents byte-identical")

print("4. deletes remove documents")
be.write(STORE, COLS, {}, ["b\ttag2"], set(rows))
got = be.read_all(STORE, COLS)
check("b\ttag2" not in got and len(got) == 2, "deleted key gone, others intact")

print("5. pagination — a store larger than one page reads fully")
DOCS.clear()
many = {("k%04d" % i + "\tt"): {"norm_key": "k%04d" % i, "tag": "t", "created": i}
        for i in range(750)}
be.write(STORE, COLS, many, [], set())
CALLS["list"] = 0
got = be.read_all(STORE, COLS)
check(len(got) == 750, "all 750 records returned across pages (got %d)" % len(got))
check(CALLS["list"] >= 3, "followed nextPageToken (%d list calls)" % CALLS["list"])

print("6. commit batching stays under the Firestore transaction cap")
check(max(CALLS["batch_sizes"]) <= 500,
      "largest batch %d <= 500" % max(CALLS["batch_sizes"]))

print("7. document ids are stable and collision-free")
ids = {be._doc_id(k) for k in many}
check(len(ids) == len(many), "750 distinct ids (got %d)" % len(ids))
check(be._doc_id("a\ttag1") == be._doc_id("a\ttag1"), "same key -> same id")
check(be._doc_id("a\ttag1") != be._doc_id("a\ttag2"), "different keys -> different ids")

print("8. collection naming matches the other backends")
check(be._coll(STORE) == "ludodex_user_tags", "ludodex_<store> (got %s)" % be._coll(STORE))

print("9. a failed commit raises rather than silently losing data")
def boom(method, url, headers=None, body=None, tries=4):
    return (500, {"error": "backend unavailable"}) if method == "POST" else fake_http(
        method, url, headers, body, tries)
dbsync._s.http = boom
try:
    be.write(STORE, COLS, {"z\tz": {"norm_key": "z", "tag": "z", "created": 1}}, [], set())
    check(False, "commit failure raises")
except RuntimeError:
    check(True, "commit failure raises RuntimeError")
dbsync._s.http = fake_http

print()
if FAIL:
    print("FAILED (%d): %s" % (len(FAIL), "; ".join(FAIL)))
    sys.exit(1)
print("ALL CHECKS PASSED")
print("NB this is the protocol layer only — a live round-trip against a real project "
      "still needs firebase_project_id + a service-account key entered in Settings.")
