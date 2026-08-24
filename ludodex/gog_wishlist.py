#!/usr/bin/env python3
"""List a GOG account's WISHLIST (wanted, not owned) via the GOG Galaxy OAuth flow.

The Discover/"Wanted" mirror of gog_owned.py — reuses the same cached token
(.gog/tokens.json). Wishlist product ids come from embed.gog.com/user/wishlist.json
and titles from the public api.gog.com/products/<id>. Prints a TSV (gog_id<TAB>title)
to stdout, status to stderr — same shape as gog_owned.py.

Set GOG up once via gog_owned.py (--code <code>); this reuses that login.
"""
import os
import sys
import json
import time
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
sys.path.insert(0, DIR)
import config

CLIENT_ID, CLIENT_SECRET = config.gog_creds()
TOKFILE = os.path.join(DATA, ".gog", "tokens.json")


def http_get(url, token=None):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "GOGGalaxyClient")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def access_token():
    """Reuse the SHARED cached token; refresh only when it has actually expired.

    GOG rotates the refresh token on every use, so two scripts refreshing the same file
    is a race: run this alongside gog_owned during a sync and the second refresh goes
    out with a token the first already spent, which invalidates the login and forces a
    fresh browser sign-in. Honouring the hour-long access token removes the overlap in
    the common case; the rotated result still goes back to the shared file, atomically
    and 0600, so gog_owned keeps working."""
    if not os.path.exists(TOKFILE):
        sys.exit("no cached GOG token — set up GOG first: "
                 "python3 ludodex/gog_owned.py --code <code>")
    saved = json.load(open(TOKFILE, encoding="utf-8"))
    age = int(time.time()) - int(saved.get("_saved_at") or 0)
    if saved.get("access_token") and age < int(saved.get("expires_in") or 3600) - 120:
        return saved["access_token"]
    q = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token", "refresh_token": saved["refresh_token"]})
    tok = http_get("https://auth.gog.com/token?" + q)
    tok["_saved_at"] = int(time.time())
    try:
        config.write_private_json(TOKFILE, tok)
    except OSError:
        pass
    print("# GOG token refreshed", file=sys.stderr)
    return tok["access_token"]


def main():
    access = access_token()
    wl = http_get("https://embed.gog.com/user/wishlist.json", token=access)
    ids = [gid for gid, on in (wl.get("wishlist") or {}).items() if on]

    rows = []
    for gid in ids:
        title = ""
        try:                                 # public product info — no auth needed
            p = http_get("https://api.gog.com/products/%s" % gid)
            title = p.get("title", "") or ""
        except Exception:                    # noqa: BLE001 — leave title blank on miss
            pass
        rows.append((gid, title))
        time.sleep(0.15)

    rows.sort(key=lambda x: (x[1] or "~").lower())
    for gid, title in rows:
        print(config.tsv_row(gid, title))
    print("# GOG wishlist: %d titles (%d unresolved names)"
          % (len(rows), sum(1 for _, t in rows if not t)), file=sys.stderr)


if __name__ == "__main__":
    main()
