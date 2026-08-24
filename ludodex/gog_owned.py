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
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
sys.path.insert(0, DIR)
import config

# GOG Galaxy's public OAuth client (same values shipped in every GOG client/tool);
# overridable via config, but the defaults work for everyone.
CLIENT_ID, CLIENT_SECRET = config.gog_creds()
REDIRECT = "https://embed.gog.com/on_login_success?origin=client"
TOKDIR = os.path.join(DATA, ".gog")
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
    # config.write_private_json, not a bare open(): this is a live GOG login. The write
    # is also atomic, which matters more here than anywhere else — GOG ROTATES the
    # refresh token on every use, so a half-written file is a lost account link that
    # only a fresh browser login can repair.
    tok["_saved_at"] = int(time.time())
    config.write_private_json(TOKFILE, tok)


def cached_access_token():
    """A usable access token, refreshing only when the cached one has expired.

    GOG's access token lasts an hour and its refresh token ROTATES on every use, so
    refreshing unconditionally on every invocation was both wasteful and a live race:
    gog_owned and gog_wishlist share this one file, and when the sync runs them close
    together the second refresh can be sent with a token the first already consumed,
    which invalidates the login outright.

    Returns None when there is nothing cached to use."""
    if not os.path.exists(TOKFILE):
        return None
    try:
        saved = json.load(open(TOKFILE, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    age = int(time.time()) - int(saved.get("_saved_at") or 0)
    if saved.get("access_token") and age < int(saved.get("expires_in") or 3600) - 120:
        return saved["access_token"]
    if not saved.get("refresh_token"):
        return None
    tok = refresh_tokens(saved["refresh_token"])
    save(tok)
    print("# GOG token refreshed", file=sys.stderr)
    return tok["access_token"]


def _page_products(access, page, hidden=False):
    """One page of getFilteredProducts. `hidden` asks for the products the account has
    HIDDEN from its library view — GOG defaults to non-hidden, so without this a hidden
    purchase is simply absent from the ownership list."""
    url = ("https://embed.gog.com/account/getFilteredProducts?mediaType=1&page=%d%s"
           % (page, "&hiddenFlag=1" if hidden else ""))
    return http_get(url, token=access)


def fetch_owned(access):
    """[(gog_id, title)] for the account, hidden products included.

    TWO PASSES, and the second is deliberately fail-soft. `hiddenFlag=1` is the
    documented-by-observation way to ask GOG for hidden products and is what every other
    GOG client sends; it has NOT been verified here against a live account, so if GOG
    rejects or ignores it the visible library must still be returned in full rather than
    the whole pull failing. A hidden game missing is a gap; a raised exception here is
    an empty ownership list, which the catalog would read as "you own no GOG games"."""
    rows, seen = [], set()

    def walk(hidden):
        page = 1
        while True:
            data = _page_products(access, page, hidden=hidden)
            for p in data.get("products", []):
                gid = p.get("id")
                if gid in seen:
                    continue
                seen.add(gid)
                rows.append((gid, p.get("title", "")))
            total = data.get("totalPages", 1) or 1
            if page >= total:
                break
            page += 1

    walk(False)
    try:
        walk(True)
    except Exception as e:                   # noqa: BLE001 — see the docstring
        print("# GOG hidden-products pass skipped (%s)" % str(e)[:120], file=sys.stderr)
    rows.sort(key=lambda x: (x[1] or "").lower())
    return rows


def main(argv):
    code = argv[1] if len(argv) > 1 and argv[0] == "--code" else None
    if code:
        tok = get_tokens_from_code(code)
        save(tok)
        print("# GOG tokens obtained + cached", file=sys.stderr)
        access = tok["access_token"]
    else:
        access = cached_access_token()
    if not access:
        sys.exit("no cached GOG token — run once with --code <code> "
                 "(see login URL in this file)")
    rows = fetch_owned(access)
    for gid, title in rows:
        print(config.tsv_row(gid, title))
    print("# owned GOG games: %d" % len(rows), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
