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
DATA = os.environ.get("LUDODEX_DATA", DIR)
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


if not os.path.exists(TOKFILE):
    sys.exit("no cached GOG token — set up GOG first: python3 gog_owned.py --code <code>")

saved = json.load(open(TOKFILE))
q = urllib.parse.urlencode({
    "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
    "grant_type": "refresh_token", "refresh_token": saved["refresh_token"]})
tok = http_get("https://auth.gog.com/token?" + q)
# save the rotated tokens back to the SHARED cache so gog_owned stays valid too
tok["_saved_at"] = int(time.time())
try:
    json.dump(tok, open(TOKFILE, "w"))
except OSError:
    pass
access = tok["access_token"]
print("# GOG token refreshed", file=sys.stderr)

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
    print("%s\t%s" % (gid, title))
print("# GOG wishlist: %d titles (%d unresolved names)"
      % (len(rows), sum(1 for _, t in rows if not t)), file=sys.stderr)
