#!/usr/bin/env python3
"""List GOG-owned games via the GOG Galaxy OAuth flow.

First run:  gog_owned.py --code <code-from-login-redirect>
  -> exchanges the code for tokens, caches them, lists games.
Later runs: gog_owned.py
  -> uses the cached refresh token (no login).

Login URL to get a code (open in a browser, log in, copy the `code=` value from
the final redirect URL):
  https://auth.gog.com/auth?client_id=46899977096215655&redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient&response_type=code&layout=client2

Output: TSV  gog_id<TAB>title  to stdout; status to stderr.
"""
import os
import sys
import json
import time
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config

# GOG Galaxy's public OAuth client (same values shipped in every GOG client/tool);
# overridable via config, but the defaults work for everyone.
CLIENT_ID = config.get("gog_client_id")
CLIENT_SECRET = config.get("gog_client_secret")
REDIRECT = "https://embed.gog.com/on_login_success?origin=client"
TOKDIR = os.path.join(DIR, ".gog")
TOKFILE = os.path.join(TOKDIR, "tokens.json")
os.makedirs(TOKDIR, exist_ok=True)


def http_get(url, token=None):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "GOGGalaxyClient")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_tokens_from_code(code):
    q = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT})
    return http_get("https://auth.gog.com/token?" + q)


def refresh_tokens(refresh_token):
    q = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token", "refresh_token": refresh_token})
    return http_get("https://auth.gog.com/token?" + q)


def save(tok):
    tok["_saved_at"] = int(time.time())
    json.dump(tok, open(TOKFILE, "w"))


code = None
if len(sys.argv) > 2 and sys.argv[1] == "--code":
    code = sys.argv[2]

if code:
    tok = get_tokens_from_code(code)
    save(tok)
    print("# GOG tokens obtained + cached", file=sys.stderr)
elif os.path.exists(TOKFILE):
    saved = json.load(open(TOKFILE))
    tok = refresh_tokens(saved["refresh_token"])
    save(tok)
    print("# GOG token refreshed", file=sys.stderr)
else:
    sys.exit("no cached GOG token — run once with --code <code> (see login URL in this file)")

access = tok["access_token"]

# Paginate the owned-games library (titles + ids) via getFilteredProducts.
rows = []
page = 1
while True:
    data = http_get("https://embed.gog.com/account/getFilteredProducts?"
                    "mediaType=1&page=%d" % page, token=access)
    for p in data.get("products", []):
        rows.append((p.get("id"), p.get("title", "")))
    total = data.get("totalPages", 1)
    if page >= total:
        break
    page += 1

rows.sort(key=lambda x: (x[1] or "").lower())
for gid, title in rows:
    print("%s\t%s" % (gid, title))
print("# owned GOG games: %d" % len(rows), file=sys.stderr)
