#!/usr/bin/env python3
"""Push the unified catalog (game-library.sqlite) to a remote DB mirror.

Targets: PocketBase and/or Firebase Firestore. One-way (local -> remote): the
remote ends up mirroring the local `games` and `sources` tables exactly.

  python3 sync.py                # use the configured sync_target
  python3 sync.py pocketbase     # force a target (pocketbase|firebase|both)
  python3 sync.py both --dry-run # show what would be pushed, write nothing

Config keys (see config.py): sync_target, pocketbase_url/admin_email/password,
firebase_project_id/sa_json/collection_prefix.
"""
import os
import sys
import ssl
import json
import sqlite3
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

CTX = ssl.create_default_context()
GAME_COLS = ["norm_key", "canonical_title", "sources_summary", "n_sources",
             "n_kinds", "has_emulation", "has_steam", "has_gog", "has_epic",
             "has_itch"]
SRC_COLS = ["game_norm", "source", "platform", "source_id", "title_raw", "detail"]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


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


def http(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
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
        return e.code, parsed
    except urllib.error.URLError as e:
        return 0, str(e)


# --------------------------------------------------------------------------- #
#  PocketBase
# --------------------------------------------------------------------------- #
PB_GAME_FIELDS = (
    [{"name": c, "type": "text"} for c in ("norm_key", "canonical_title",
                                           "sources_summary")] +
    [{"name": c, "type": "number"} for c in ("n_sources", "n_kinds")] +
    [{"name": c, "type": "bool"} for c in ("has_emulation", "has_steam",
                                           "has_gog", "has_epic", "has_itch")])
PB_SRC_FIELDS = [{"name": c, "type": "text"} for c in SRC_COLS]


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
    # try modern ("fields") then legacy ("schema") create payloads
    for key in ("fields", "schema"):
        st, resp = http("POST", url + "/api/collections", headers=hdr,
                        body={"name": name, "type": "base", key: fields})
        if st in (200, 201):
            log("  created PocketBase collection %r" % name)
            return
    sys.exit("could not create PocketBase collection %r (%s): %s" % (name, st, resp))


def pb_records(url, hdr, name):
    """Yield all existing record ids in a collection (paged)."""
    page = 1
    while True:
        st, resp = http("GET", "%s/api/collections/%s/records?perPage=500&page=%d"
                        "&fields=id" % (url, name, page), headers=hdr)
        if st != 200 or not isinstance(resp, dict):
            return
        items = resp.get("items") or []
        for it in items:
            yield it["id"]
        if page >= (resp.get("totalPages") or 1):
            return
        page += 1


def pb_batch(url, hdr, requests):
    """Try the PocketBase batch API; return True on success."""
    st, _ = http("POST", url + "/api/batch", headers=hdr, body={"requests": requests})
    return st in (200, 204)


def pb_threaded(fn, items):
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(fn, items))


def pb_sync(games, sources, dry):
    if dry:
        log("PocketBase [dry-run] would replace %d games + %d sources" %
            (len(games), len(sources)))
        return
    url = config.get("pocketbase_url").rstrip("/")
    email = config.get("pocketbase_admin_email")
    pw = config.pocketbase_password()
    if not (url and email and pw):
        sys.exit("PocketBase not configured (need pocketbase_url, "
                 "pocketbase_admin_email, and a password)")
    log("PocketBase -> %s" % url)
    token = pb_auth(url, email, pw)
    hdr = {"Authorization": token}
    for name, fields in (("games", PB_GAME_FIELDS), ("sources", PB_SRC_FIELDS)):
        pb_ensure_collection(url, hdr, name, fields)

    for name, rows in (("games", games), ("sources", sources)):
        # truncate
        ids = list(pb_records(url, hdr, name))
        if ids:
            dels = [{"method": "DELETE",
                     "url": "/api/collections/%s/records/%s" % (name, i)} for i in ids]
            if not _pb_chunked_batch(url, hdr, dels):
                pb_threaded(lambda i: http(
                    "DELETE", "%s/api/collections/%s/records/%s" % (url, name, i),
                    headers=hdr), ids)
        # insert
        creates = [{"method": "POST",
                    "url": "/api/collections/%s/records" % name, "body": r}
                   for r in rows]
        if not _pb_chunked_batch(url, hdr, creates):
            pb_threaded(lambda r: http(
                "POST", "%s/api/collections/%s/records" % (url, name),
                headers=hdr, body=r), rows)
        log("  %s: %d records mirrored" % (name, len(rows)))


def _pb_chunked_batch(url, hdr, reqs, size=200):
    if not reqs:
        return True
    ok = True
    for i in range(0, len(reqs), size):
        if not pb_batch(url, hdr, reqs[i:i + size]):
            return False        # batch unsupported/disabled -> caller falls back
    return ok


# --------------------------------------------------------------------------- #
#  Firebase (Firestore REST)
# --------------------------------------------------------------------------- #
def fb_token(sa_path):
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests as gar
    except ImportError:
        sys.exit("Firebase sync needs the google-auth package: "
                 "uv pip install google-auth  (or pipx inject)")
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


def _fb_doc_id(s):
    return ("".join(c if c.isalnum() else "_" for c in str(s)))[:1400] or "_"


def fb_sync(games, sources, dry):
    if dry:
        log("Firestore [dry-run] would upsert %d games + %d sources" %
            (len(games), len(sources)))
        return
    pid = config.get("firebase_project_id")
    sa = config.get("firebase_sa_json")
    pre = config.get("firebase_collection_prefix") or ""
    if not (pid and sa):
        sys.exit("Firebase not configured (need firebase_project_id + firebase_sa_json)")
    log("Firestore -> project %s" % pid)
    token = fb_token(sa)
    hdr = {"Authorization": "Bearer " + token}
    base = ("https://firestore.googleapis.com/v1/projects/%s/databases/(default)"
            "/documents" % pid)
    gcol, scol = pre + "games", pre + "sources"

    def commit(writes):
        for i in range(0, len(writes), 400):
            st, resp = http("POST", base + ":commit", headers=hdr,
                            body={"writes": writes[i:i + 400]})
            if st != 200:
                sys.exit("Firestore commit failed (%s): %s" % (st, resp))

    def upsert(col, rows, idfn):
        writes = []
        for r in rows:
            name = "%s/%s/%s" % (base, col, idfn(r))
            writes.append({"update": {"name": name,
                                      "fields": {k: _fb_value(v) for k, v in r.items()}}})
        commit(writes)

    # prune docs that no longer exist, then upsert current set
    def prune(col, keep_ids):
        keep = set(keep_ids)
        page_token, dels = "", []
        while True:
            u = "%s/%s?pageSize=300&mask.fieldPaths=__name__" % (base, col)
            if page_token:
                u += "&pageToken=" + page_token
            st, resp = http("GET", u, headers=hdr)
            if st != 200 or not isinstance(resp, dict):
                break
            for d in resp.get("documents", []):
                did = d["name"].rsplit("/", 1)[-1]
                if did not in keep:
                    dels.append({"delete": d["name"]})
            page_token = resp.get("nextPageToken") or ""
            if not page_token:
                break
        if dels:
            commit(dels)

    gid = lambda r: _fb_doc_id(r["norm_key"])
    sid = lambda r: _fb_doc_id("%s__%s__%s" % (r["game_norm"], r["source"],
                                               r["source_id"]))
    prune(gcol, [gid(r) for r in games])
    prune(scol, [sid(r) for r in sources])
    upsert(gcol, games, gid)
    upsert(scol, sources, sid)
    log("  games: %d, sources: %d upserted" % (len(games), len(sources)))


# --------------------------------------------------------------------------- #
def main(argv):
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    target = argv[0] if argv else config.get("sync_target")
    if not target:
        sys.exit("no sync target — pass pocketbase|firebase|both, or set sync_target")
    games, sources = load_catalog()
    log("catalog: %d games, %d source rows" % (len(games), len(sources)))
    if target in ("pocketbase", "both"):
        pb_sync(games, sources, dry)
    if target in ("firebase", "both"):
        fb_sync(games, sources, dry)
    if target not in ("pocketbase", "firebase", "both"):
        sys.exit("unknown target %r" % target)
    log("sync done.")


if __name__ == "__main__":
    main(sys.argv[1:])
