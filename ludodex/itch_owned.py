#!/usr/bin/env python3
"""List itch.io games owned by the user, via the itch.io server-side API.

Reads the API key from ITCH_API_KEY env, else config (`config.py itch-key`).
Generate a key at https://itch.io/user/settings/api-keys (any scope works for
reading your library). Paginates /profile/owned-keys.

Output: TSV  game_id<TAB>title  to stdout; count to stderr.
"""
import os
import sys
import json
import ssl
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

KEY = (os.environ.get("ITCH_API_KEY", "").strip() or config.itch_key())
if not KEY:
    sys.exit("no itch.io API key — run ./scripts/setup.sh, or set it with "
             "config.py set itch_api_key <key> (get one at "
             "https://itch.io/user/settings/api-keys)")

CTX = ssl.create_default_context()


def api(path):
    req = urllib.request.Request(
        "https://api.itch.io/" + path,
        headers={"Authorization": "Bearer " + KEY})
    with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
        return json.load(r)


rows = []
page = 1
while page <= 500:                       # safety bound; loop really ends on empty page
    data = api("profile/owned-keys?page=%d" % page)
    keys = data.get("owned_keys") or []
    if not keys:
        break
    for k in keys:
        g = k.get("game") or {}
        title = g.get("title") or ""
        gid = g.get("id") or k.get("game_id") or ""
        if title:
            rows.append((str(gid), title))
    page += 1

rows.sort(key=lambda x: x[1].lower())
for gid, title in rows:
    print("%s\t%s" % (gid, title))
print("# total itch.io games owned: %d" % len(rows), file=sys.stderr)
