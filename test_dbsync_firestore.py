"""Offline protocol test for the Firestore backing-store adapter (task #2).

Fast, no-dependency companion to test_dbsync_live.py (which does a real round-trip against
a Firestore emulator, or a real project). This one stands a fake Firestore REST server in
front of sync.http, so it runs anywhere with no container and no creds:

  - read_all follows nextPageToken to the end (a >300-record store is one page in the
    API's eyes and would otherwise silently truncate, which the merge engine would read
    as "the remote deleted everything")
  - write batches commits at Firestore's 500-write transaction cap
  - document ids are stable and collision-free for the natural keys we use
  - the stored shape ({k, data:<json blob>}) round-trips byte-identically, so a re-sync
    with nothing changed is a genuine no-op rather than an endless re-push
  - deletes are emitted as delete writes
  - update.name / delete are RESOURCE NAMES, not URLs (a real 400 once slipped past an
    earlier, laxer version of this fake server)

Usage: python3 test_dbsync_firestore.py     (no network, no creds)
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("LUDODEX_DATA", "/tmp/ludodex-fs-test")
os.makedirs(os.environ["LUDODEX_DATA"], exist_ok=True)

import config                                    # noqa: E402
import remote_db as _s                           # noqa: E402
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
                # Firestore requires a RESOURCE NAME here, not a URL. Being lax about this
                # is how a real 400 ("Document name ... is not valid") slipped past this
                # test once — so reject anything URL-shaped, exactly as the API does.
                if name.startswith("http") or not name.startswith("projects/"):
                    return 400, {"error": {"code": 400,
                                           "message": 'Document name "%s" is not valid' % name}}
                coll, did = name.split("/documents/")[1].rsplit("/", 1)
                f = w["update"]["fields"]
                DOCS.setdefault(coll, {})[did] = {"k": f["k"]["stringValue"],
                                                  "data": f["data"]["stringValue"]}
            elif "delete" in w:
                dn = w["delete"]
                if dn.startswith("http") or not dn.startswith("projects/"):
                    return 400, {"error": {"code": 400,
                                           "message": 'Document name "%s" is not valid' % dn}}
                coll, did = dn.split("/documents/")[1].rsplit("/", 1)
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

print("10. a failed READ raises instead of looking like an empty remote")
# This is the data-loss case: if read_all swallowed an error and returned {}, the merge
# engine would see "remote lost every record" and delete them locally. An expired Google
# token (they last ~1h) is exactly how that would fire.
def read_401(method, url, headers=None, body=None, tries=4):
    if method == "GET":
        return 401, {"error": {"code": 401, "message": "Request had invalid credentials."}}
    return fake_http(method, url, headers, body, tries)
dbsync._s.http = read_401
try:
    be.read_all(STORE, COLS)
    check(False, "401 on read raises")
except RuntimeError as e:
    check("401" in str(e), "401 on read raises RuntimeError (%s)" % str(e)[:50])

# A failure PART-WAY through pagination must not return the pages it already had.
state = {"n": 0}
def read_flaky(method, url, headers=None, body=None, tries=4):
    if method == "GET":
        state["n"] += 1
        if state["n"] > 1:
            return 500, {"error": "boom"}
    return fake_http(method, url, headers, body, tries)
dbsync._s.http = read_flaky
try:
    be.read_all(STORE, COLS)
    check(False, "mid-pagination failure raises")
except RuntimeError:
    check(True, "mid-pagination failure raises rather than returning a partial set")

# A collection that does not exist yet IS legitimately empty, and must not raise.
def read_404(method, url, headers=None, body=None, tries=4):
    return (404, {"error": "not found"}) if method == "GET" else fake_http(
        method, url, headers, body, tries)
dbsync._s.http = read_404
try:
    check(be.read_all(STORE, COLS) == {}, "404 (collection not created yet) reads as empty")
except RuntimeError:
    check(False, "404 (collection not created yet) reads as empty")
dbsync._s.http = fake_http

print("11. service-account token mint builds a valid signed assertion")
# Can't call Google, but the whole local half — parsing the key, scopes, and signing the
# JWT assertion that gets exchanged for a token — runs offline against a generated key.
try:
    import json as _j, base64, tempfile as _tf
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.oauth2 import service_account
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    sa = {"type": "service_account", "project_id": "test-proj",
          "private_key_id": "k1", "private_key": pem,
          "client_email": "ludodex@test-proj.iam.gserviceaccount.com",
          "client_id": "1", "token_uri": "https://oauth2.googleapis.com/token"}
    f = _tf.NamedTemporaryFile("w", suffix=".json", delete=False)
    _j.dump(sa, f); f.close()
    creds = service_account.Credentials.from_service_account_file(
        f.name, scopes=["https://www.googleapis.com/auth/datastore"])
    check(creds.service_account_email == sa["client_email"], "SA email parsed")
    assertion = creds._make_authorization_grant_assertion()
    payload = _j.loads(base64.urlsafe_b64decode(
        assertion.split(b".")[1] + b"=" * (-len(assertion.split(b".")[1]) % 4)))
    check(payload.get("iss") == sa["client_email"], "assertion iss = service account")
    check(payload.get("scope") == "https://www.googleapis.com/auth/datastore",
          "assertion carries the datastore scope (got %r)" % payload.get("scope"))
    check(payload.get("aud") == sa["token_uri"], "assertion aud = token endpoint")
    os.unlink(f.name)
except ImportError as e:
    print("  skip  google-auth/cryptography not installed here (%s)" % str(e)[:40])

print()
if FAIL:
    print("FAILED (%d): %s" % (len(FAIL), "; ".join(FAIL)))
    sys.exit(1)
print("ALL CHECKS PASSED")
print("NB protocol layer only. For a real round-trip: run a Firestore emulator and use "
      "test_dbsync_live.py firebase with FIRESTORE_EMULATOR_HOST set.")
