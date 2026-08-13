#!/usr/bin/env python3
"""Pull owned games from a PlayStation Network account — the console-store leg.

PSN has no official public library API, but the PS App's OAuth is stable and
reachable from anywhere (unlike EA's Akamai block). Auth is a one-time paste of
the ``npsso`` token, exactly like the Epic/EA connect flow:

  1. Sign in at https://www.playstation.com , then open (same browser):
       https://ca.account.sony.com/api/v1/ssocookie
     It returns {"npsso":"<64-char token>"} — copy that value.
  2. Paste it here / in ludodex's Sync menu. We exchange npsso -> a short-lived
     access token + a ~2-month refresh token (cached in .psn/tokens.json) and
     refresh non-interactively after that.

  python3 ludodex/psn_owned.py --npsso '<npsso|json|url>'  # cache tokens, verify
  python3 ludodex/psn_owned.py --whoami                     # verify auth only
  python3 ludodex/psn_owned.py                              # print owned games TSV
  python3 ludodex/psn_owned.py > psn_games.tsv              # (update.sh does this)

npsso resolves env PSN_NPSSO > the --npsso arg. ludodex never reads 1Password at
runtime — tokens live only in .psn/. stdlib-only (urllib).

NOTE (data fetch): the purchased-games list lives behind PSN's *persisted*
GraphQL on web.np.playstation.com (arbitrary queries return "Query not
whitelisted"). The exact whitelisted operation hash is pinned in PURCHASED_OP
below and validated against a live token — see fetch_owned().
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
sys.path.insert(0, DIR)
import config

PSN_DIR = os.path.join(DATA, ".psn")
TOKFILE = os.path.join(PSN_DIR, "tokens.json")
os.makedirs(PSN_DIR, exist_ok=True)

# The PS App's public OAuth client (well-known; shipped in every PS mobile app).
CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
CLIENT_SECRET = "ucPjka5tntB2KqsP"
_BASIC = base64.b64encode(("%s:%s" % (CLIENT_ID, CLIENT_SECRET)).encode()).decode()
REDIRECT = "com.scee.psxandroid.scecompcall://redirect"
SCOPE = "psn:mobile.v2.core psn:clientapp"
AUTHZ_URL = "https://ca.account.sony.com/api/authz/v3/oauth/authorize"
TOKEN_URL = "https://ca.account.sony.com/api/authz/v3/oauth/token"
GRAPHQL = "https://web.np.playstation.com/api/graphql/v1/op"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")

# Persisted "purchased game list" operation. PSN whitelists GraphQL by sha256Hash;
# this one is the PS App's getPurchasedGameList (validated against a live token —
# returns true purchases, not play history). If Sony rotates it you'll get a
# "Query … not whitelisted" 400 and need to refresh the hash.
PURCHASED_OP = {
    "operationName": "getPurchasedGameList",
    "hash": "2c045408b0a4d0264bb5a3edfed4efd49fb4749cf8d216be9043768adff905e2",
}

# PSN platform field -> catalog platform label (matches emulation's ps3/psx style)
_PLAT = {"PS5": "ps5", "PS4": "ps4", "PS3": "ps3", "PSVITA": "psvita", "PSP": "psp"}


class _CaptureRedirect(urllib.request.HTTPRedirectHandler):
    """PSN returns the auth code as a 302 to a custom-scheme URL
    (com.scee.psxandroid…). urllib's default http_error_302 rejects non-http
    schemes *before* redirect_request runs, so override it outright and grab the
    Location header instead of following it."""
    def http_error_302(self, req, fp, code, msg, headers):
        raise _Redirected(headers.get("Location", ""))
    http_error_301 = http_error_303 = http_error_307 = http_error_302


class _Redirected(Exception):
    def __init__(self, url):
        self.url = url


def _extract_npsso(raw):
    """Tolerate the raw 64-char token, the {"npsso":"..."} JSON, or the whole
    ssocookie URL/response pasted in."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("npsso"):
            return str(obj["npsso"]).strip()
    except (ValueError, TypeError):
        pass
    import re
    # `npsso: "..."`, `npsso=...`, or the tabular `npsso   "..."` (whitespace sep,
    # as copied from the ssocookie page alongside the expires_in line).
    m = re.search(r'["\']?npsso["\']?\s*[:=\s]\s*["\']?([A-Za-z0-9_-]{40,})', raw)
    if m:
        return m.group(1)
    return raw.strip().strip('"\'')


def _save(tok):
    tok["_saved_at"] = int(time.time())
    json.dump(tok, open(TOKFILE, "w"))


def code_from_npsso(npsso):
    """Exchange the npsso cookie for a one-shot authorization code."""
    q = urllib.parse.urlencode({
        "access_type": "offline", "client_id": CLIENT_ID,
        "response_type": "code", "scope": SCOPE, "redirect_uri": REDIRECT})
    req = urllib.request.Request(AUTHZ_URL + "?" + q, method="GET")
    req.add_header("Cookie", "npsso=%s" % npsso)
    req.add_header("User-Agent", UA)
    opener = urllib.request.build_opener(_CaptureRedirect)
    try:
        opener.open(req, timeout=30)
    except _Redirected as r:
        qs = urllib.parse.urlparse(r.url).query
        code = urllib.parse.parse_qs(qs).get("code", [None])[0]
        if not code:
            raise SystemExit("PSN: no code in redirect — npsso likely expired")
        return code
    raise SystemExit("PSN: expected a redirect with the auth code, got none")


def _token_request(data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    req.add_header("Authorization", "Basic %s" % _BASIC)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def tokens_from_code(code):
    return _token_request({
        "code": code, "redirect_uri": REDIRECT,
        "grant_type": "authorization_code", "token_format": "jwt"})


def refresh_tokens(refresh_token):
    return _token_request({
        "grant_type": "refresh_token", "refresh_token": refresh_token,
        "scope": SCOPE, "token_format": "jwt"})


def connect(npsso):
    """npsso -> cached tokens. Raises on failure (bad/expired npsso)."""
    tok = tokens_from_code(code_from_npsso(npsso))
    _save(tok)
    return tok


def access_token():
    """Return a valid access token, refreshing (or failing) as needed."""
    if not os.path.exists(TOKFILE):
        env = os.environ.get("PSN_NPSSO")
        if env:
            return connect(env)["access_token"]
        raise SystemExit("PSN not connected — reconnect: paste a fresh npsso token.")
    tok = json.load(open(TOKFILE))
    age = int(time.time()) - tok.get("_saved_at", 0)
    if age < tok.get("expires_in", 3600) - 120:
        return tok["access_token"]
    try:
        tok = refresh_tokens(tok["refresh_token"])   # transparent refresh
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        # Sony revokes refresh tokens early (signing in elsewhere, session
        # rotation) → invalid_grant. Non-interactive refresh can't recover; the
        # user must re-paste a fresh npsso. Surface a clean, actionable message
        # (the Sync menu keys off "reconnect"/"npsso" to offer the connect flow).
        if e.code in (400, 401) or "invalid_grant" in body:
            raise SystemExit("PSN session expired — reconnect: paste a fresh npsso "
                             "token (Sony revoked the old sign-in).")
        raise
    _save(tok)
    return tok["access_token"]


def account_id(token):
    """Best-effort account id from the JWT access token (no extra API call)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims.get("sub") or claims.get("account_id") or ""
    except Exception:
        return ""


def fetch_owned(token):
    """Purchased (truly owned) games as (titleId, name) rows, paginated. Hits the
    PS App's whitelisted getPurchasedGameList; isActive filters to still-owned
    titles and subscriptionService=NONE excludes PS-Plus-catalog-only entries."""
    rows, start, size = [], 0, 50
    while True:
        variables = {"isActive": True, "platform": ["ps3", "ps4", "ps5"],
                     "start": start, "size": size, "subscriptionService": "NONE"}
        ext = {"persistedQuery": {"version": 1,
                                  "sha256Hash": PURCHASED_OP["hash"]}}
        q = urllib.parse.urlencode({
            "operationName": PURCHASED_OP["operationName"],
            "variables": json.dumps(variables),
            "extensions": json.dumps(ext)})
        req = urllib.request.Request(GRAPHQL + "?" + q, method="GET")
        req.add_header("Authorization", "Bearer %s" % token)
        req.add_header("User-Agent", UA)
        # PSN's Apollo gateway blocks GETs without an operation-name header as CSRF.
        req.add_header("x-apollo-operation-name", PURCHASED_OP["operationName"])
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        if data.get("errors"):
            raise SystemExit("PSN: %s" % json.dumps(data["errors"])[:300])
        block = (((data.get("data") or {}).get("purchasedTitlesRetrieve")) or {})
        for g in block.get("games") or []:
            plat = _PLAT.get((g.get("platform") or "").upper(), "playstation")
            rows.append((g.get("titleId") or g.get("conceptId") or "",
                         g.get("name") or "", plat))
        info = block.get("pageInfo") or {}
        if info.get("isLast") or not block.get("games"):
            break
        start += size
    # keep every console a title is owned on (cross-gen returns PS4 + PS5 rows);
    # de-dup only exact (title, console) repeats
    seen, out = set(), []
    for gid, title, plat in rows:
        key = ((title or "").lower(), plat)
        if title and key not in seen:
            seen.add(key)
            out.append((gid, title, plat))
    out.sort(key=lambda x: (x[1] or "").lower())
    return out


def main(argv):
    if len(argv) > 1 and argv[1] == "--npsso":
        npsso = _extract_npsso(argv[2] if len(argv) > 2 else "")
        if not npsso:
            raise SystemExit("PSN: no npsso value provided")
        tok = connect(npsso)
        aid = account_id(tok["access_token"])
        print("# PSN connected%s" % (" (account %s)" % aid if aid else ""),
              file=sys.stderr)
        return
    if len(argv) > 1 and argv[1] == "--whoami":
        tok = access_token()
        aid = account_id(tok)
        print("# PSN auth OK%s" % (" — account %s" % aid if aid else ""),
              file=sys.stderr)
        return
    token = access_token()
    rows = fetch_owned(token)
    for gid, title, plat in rows:
        print("%s\t%s\t%s" % (gid, title, plat))
    print("# owned PSN games: %d" % len(rows), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv)
