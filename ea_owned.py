#!/usr/bin/env python3
"""Pull owned games from an EA account (EA app / Origin) — the 3rd PC-store leg.

EA sits behind Akamai Bot Manager, so auth has TWO paths (see AUTH.md § EA):

  A. remid cookie (preferred). Capture the durable "remember-me" cookie once; we
     exchange it for short-lived access tokens NON-INTERACTIVELY (``prompt=none``).
     This is set-and-forget WHERE IT WORKS — but Akamai frequently BLOCKS the silent
     refresh from datacenter/VPS/server IPs (it works from residential IPs). If it's
     blocked you'll get an auth error on fetch_token; use path B.
  B. browser-minted access token (always works, ~4h). Grab a token from the
     logged-in browser and inject it with --token; cached in .ea/token.json. This is
     a re-grab-on-demand flow — no headless refresh once it expires.

Uses EA's current (2026) GraphQL API at service-aggregation-layer.juno.ea.com;
the old api*.origin.com REST endpoints were degraded by the Origin -> EA-app
migration. Modeled on Lutris ``lutris/services/ea_app.py``. stdlib-only (urllib +
http.cookiejar + an SSL context that re-enables legacy renegotiation, which
accounts.ea.com still requires and OpenSSL 3 refuses by default).

  python3 ea_owned.py --login --remid <val>   # path A: cache the remid cookie
  python3 ea_owned.py --token '<json|value>'  # path B: inject a browser token (~4h)
  python3 ea_owned.py                  # print owned games as <offerId>\\t<title>
  python3 ea_owned.py > ea_games.tsv   # (update.sh does this)
  python3 ea_owned.py --whoami         # just verify auth (prints EA display name)

remid resolves env EA_REMID > .ea/cookies.json > config ea_remid. ludodex never
reads 1Password at runtime — the cookie/token live in config.sqlite or .ea/.
"""
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config

EA_DIR = os.path.join(DIR, ".ea")
COOKIES = os.path.join(EA_DIR, "cookies.json")
TOKEN = os.path.join(EA_DIR, "token.json")

AUTH = "https://accounts.ea.com/connect/auth"
API = "https://service-aggregation-layer.juno.ea.com/graphql"
# Public, hardcoded EA client ids (no secret): SPA for interactive login, JS_SDK
# for the silent token grant.
LOGIN_CLIENT = "ORIGIN_SPA_ID"
TOKEN_CLIENT = "ORIGIN_JS_SDK"
LOGIN_REDIRECT = "https://www.origin.com/views/login.html"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36 QtWebEngine/5.8.0")
LOGIN_URL = ("%s?response_type=code&client_id=%s&display=originXWeb/login"
             "&locale=en_US&release_type=prod&redirect_uri=%s"
             % (AUTH, LOGIN_CLIENT, LOGIN_REDIRECT))

# accounts.ea.com lacks RFC 5746; OpenSSL 3 refuses to connect without this.
SSL_OP_ALLOW_UNSAFE_LEGACY_RENEGOTIATION = 1 << 18


def _opener():
    ctx = ssl.create_default_context()
    ctx.options |= SSL_OP_ALLOW_UNSAFE_LEGACY_RENEGOTIATION
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


OPENER = _opener()


def _get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with OPENER.open(req, timeout=timeout) as r:
        return json.load(r)


def _post(url, body, headers=None, timeout=30):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"User-Agent": UA, "Content-Type": "application/json",
                 **(headers or {})})
    with OPENER.open(req, timeout=timeout) as r:
        return json.load(r)


# --------------------------------------------------------------------------- #
#  durable credential = the remid cookie (env > local file > config)
# --------------------------------------------------------------------------- #
def load_cookies():
    env = os.environ.get("EA_REMID", "").strip()
    if env:
        return {"remid": env}
    if os.path.exists(COOKIES):
        with open(COOKIES) as f:
            c = json.load(f)
        if c.get("remid"):
            return c
    remid = config.get("ea_remid")
    if remid:
        return {"remid": remid}
    return {}


def cookie_header(cookies):
    return "; ".join("%s=%s" % (k, v) for k, v in cookies.items() if v)


def save_cookies(cookies):
    os.makedirs(EA_DIR, exist_ok=True)
    with open(COOKIES, "w") as f:
        json.dump(cookies, f)
    os.chmod(COOKIES, 0o600)


# --------------------------------------------------------------------------- #
#  access token: cached, refreshed non-interactively from the cookie
# --------------------------------------------------------------------------- #
def fetch_token(cookies):
    q = urllib.parse.urlencode({"client_id": TOKEN_CLIENT, "response_type": "token",
                                "redirect_uri": "nucleus:rest", "prompt": "none"})
    data = _get("%s?%s" % (AUTH, q), headers={"Cookie": cookie_header(cookies)})
    if "access_token" not in data:
        raise SystemExit("ea: could not get an access token (%s). Your EA login "
                         "likely expired — re-run: python3 ea_owned.py --login"
                         % data.get("error", data))
    os.makedirs(EA_DIR, exist_ok=True)
    data["expires_at"] = int(time.time()) + int(data.get("expires_in", 3600)) - 60
    with open(TOKEN, "w") as f:
        json.dump(data, f)
    return data["access_token"]


def token(cookies, force=False):
    if not force and os.path.exists(TOKEN):
        with open(TOKEN) as f:
            t = json.load(f)
        if t.get("access_token") and t.get("expires_at", 0) > int(time.time()):
            return t["access_token"]
    return fetch_token(cookies)


def auth_headers(tok):
    return {"Authorization": "Bearer %s" % tok, "AuthToken": tok, "X-AuthToken": tok}


def gql(query, variables, cookies, tok):
    """POST a GraphQL query; refresh the token once on invalid-token errors."""
    for attempt in (1, 2):
        res = _post(API, {"query": query, "variables": variables},
                    headers=auth_headers(tok))
        errs = res.get("errors")
        if errs and attempt == 1 and any("token" in json.dumps(e).lower()
                                         for e in errs):
            tok = fetch_token(cookies)        # stale token -> refresh and retry
            continue
        if errs:
            raise SystemExit("ea: GraphQL error: %s" % errs)
        return res["data"], tok
    raise SystemExit("ea: GraphQL kept failing after a token refresh")


# --------------------------------------------------------------------------- #
#  library
# --------------------------------------------------------------------------- #
def whoami(cookies, tok):
    data, tok = gql("query{me{player{pd psd displayName}}}", {}, cookies, tok)
    return data["me"]["player"], tok


def entitlements(cookies, tok):
    """All owned BASE_GAME offer ids (paginated)."""
    Q = """query getEntitlements($limit: Int, $next: String) {
      me { ownedGameProducts(locale:"DEFAULT" entitlementEnabled:true
            storefronts:[EA] type:[DIGITAL_FULL_GAME,PACKAGED_FULL_GAME]
            platforms:[PC] paging:{limit:$limit, next:$next}) {
        next items { originOfferId product { baseItem { gameType } } } } } }"""
    offers, nxt = [], None
    while True:
        data, tok = gql(Q, {"limit": 100, "next": nxt}, cookies, tok)
        page = data["me"]["ownedGameProducts"]
        for it in page["items"]:
            prod = it.get("product")
            if prod and prod["baseItem"]["gameType"] == "BASE_GAME":
                offers.append(it["originOfferId"])
        nxt = page["next"]
        if not nxt:
            break
    return offers, tok


def resolve_titles(offers, cookies, tok):
    """offerId -> title, batched 100 at a time."""
    Q = """query getOffers($offerIds: [String!]!) {
      gameProducts(offerIds:$offerIds, locale:"DEFAULT") {
        items { originOfferId gameSlug baseItem { title } } } }"""
    titles = {}
    for i in range(0, len(offers), 100):
        chunk = offers[i:i + 100]
        data, tok = gql(Q, {"offerIds": chunk}, cookies, tok)
        for p in (data.get("gameProducts") or {}).get("items", []):
            oid = p.get("originOfferId")
            t = (p.get("baseItem") or {}).get("title")
            if oid and t:
                titles[oid] = t
    return titles, tok


def do_login():
    print("EA login (one-time). Steps:", file=sys.stderr)
    print("  1. Open this URL in a browser and sign in to your EA account:\n",
          file=sys.stderr)
    print("     " + LOGIN_URL + "\n", file=sys.stderr)
    print("  2. After you're logged in, open the browser dev tools (F12) ->",
          file=sys.stderr)
    print("     Application/Storage -> Cookies -> https://accounts.ea.com", file=sys.stderr)
    print("  3. Copy the VALUE of the 'remid' cookie (the long durable one).\n",
          file=sys.stderr)
    remid = (sys.argv[sys.argv.index("--remid") + 1] if "--remid" in sys.argv
             else input("Paste remid cookie value: ").strip())
    if not remid:
        raise SystemExit("ea: no remid provided.")
    cookies = {"remid": remid}
    save_cookies(cookies)
    tok = fetch_token(cookies)                # validate immediately
    player, _ = whoami(cookies, tok)
    print("ea: logged in as %s (pid %s). Cookie saved to %s"
          % (player.get("displayName"), player.get("pd"), COOKIES), file=sys.stderr)


def save_token(access_token, ttl=14400):
    """Cache a browser-minted access token (EA's Akamai shield blocks the
    headless cookie->token refresh, so a token grabbed from the logged-in
    browser is the reliable way in)."""
    os.makedirs(EA_DIR, exist_ok=True)
    with open(TOKEN, "w") as f:
        json.dump({"access_token": access_token,
                   "expires_at": int(time.time()) + int(ttl) - 60}, f)
    os.chmod(TOKEN, 0o600)


def main(argv):
    if "--login" in argv:
        do_login()
        return
    if "--token" in argv:
        i = argv.index("--token")
        raw = argv[i + 1]
        del argv[i:i + 2]
        try:                                  # accept the whole JSON or just the value
            raw = json.loads(raw).get("access_token", raw)
        except (ValueError, AttributeError):
            pass
        save_token(raw.strip())
        print("ea: saved access token. Pulling…", file=sys.stderr)
    if not config.source_enabled("ea"):
        print("ea: source disabled (config.py enable ea)", file=sys.stderr)
        return
    cookies = load_cookies()
    # a valid cached (browser-minted) token works even without the cookie jar
    have_token = os.path.exists(TOKEN) and json.load(open(TOKEN)).get(
        "expires_at", 0) > int(time.time())
    if not cookies and not have_token:
        print("ea: not logged in — run: python3 ea_owned.py --login (or pass "
              "--token <browser access_token>)", file=sys.stderr)
        return
    tok = token(cookies) if cookies else json.load(open(TOKEN))["access_token"]
    if "--whoami" in argv:
        player, _ = whoami(cookies, tok)
        print("%s (pid %s)" % (player.get("displayName"), player.get("pd")))
        return
    offers, tok = entitlements(cookies, tok)
    titles, tok = resolve_titles(offers, cookies, tok)
    n = 0
    for oid in offers:
        t = titles.get(oid)
        if t:
            print("%s\t%s" % (oid, t))
            n += 1
    print("ea: %d owned games (%d offers, %d unresolved)"
          % (n, len(offers), len(offers) - n), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
