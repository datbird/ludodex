#!/usr/bin/env python3
"""Pull owned games from an Xbox / Microsoft account — the 2nd console-store leg.

Xbox uses a self-contained Microsoft OAuth (no third party), then the Xbox Live
token dance, to reach the Microsoft Store's *collections* service — true owned
entitlements (not Game Pass play history). Auth is a one-time paste-code, like
Epic:

  1. Click Get Xbox code (opens login.live.com), sign in.
  2. It lands on a blank oauth20_desktop.srf page — the code is in the browser
     ADDRESS BAR (?code=M...). Copy that URL (or just the code) and paste it.

Token chain:  MS auth code -> MS access+refresh token (login.live.com)
              -> Xbox user token (user.auth.xboxlive.com)
              -> XSTS token, RelyingParty http://xboxlive.com
              -> titlehub.xboxlive.com  (the account's games, names included)

(The Store *collections* API would give true purchase entitlements, but only for
products registered to the querying app in Partner Center — a generic client gets
an empty list — so titlehub title history is used, like other 3rd-party tools.)

  python3 ludodex/xbox_owned.py --code '<code|url|json>'  # cache tokens, verify
  python3 ludodex/xbox_owned.py --whoami                  # verify auth (prints gamertag)
  python3 ludodex/xbox_owned.py                           # print owned games TSV

MS refresh token lives in .xbox/tokens.json (auto-refreshes). ludodex never reads
1Password at runtime. stdlib-only (urllib).
"""
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

XBOX_DIR = os.path.join(DATA, ".xbox")
TOKFILE = os.path.join(XBOX_DIR, "tokens.json")
os.makedirs(XBOX_DIR, exist_ok=True)

# Xbox's public OAuth client (the long-standing desktop client; no secret).
MS_CLIENT = "000000004c12ae6f"
MS_SCOPE = "Xboxlive.signin Xboxlive.offline_access"
MS_REDIRECT = "https://login.live.com/oauth20_desktop.srf"
AUTHORIZE = ("https://login.live.com/oauth20_authorize.srf?client_id=%s"
             "&response_type=code&approval_prompt=auto"
             "&scope=%s&redirect_uri=%s"
             % (MS_CLIENT, urllib.parse.quote(MS_SCOPE), urllib.parse.quote(MS_REDIRECT)))
MS_TOKEN = "https://login.live.com/oauth20_token.srf"
# Device-code flow endpoint — the reliable alternative to the address-bar code:
# Microsoft hands back a short user_code the person types at microsoft.com/link,
# and we poll MS_TOKEN until they approve. No self-erasing URL to race.
MS_DEVICE = "https://login.live.com/oauth20_connect.srf"
XASU = "https://user.auth.xboxlive.com/user/authenticate"
XSTS = "https://xsts.auth.xboxlive.com/xsts/authorize"
# xboxlive.com RelyingParty: its XSTS grants the uhs/xuid/gamertag titlehub needs.
RP_XBOXLIVE = "http://xboxlive.com"
# titlehub 'devices' entry -> catalog platform label
_DEV = {"XboxSeries": "xbox series", "XboxOne": "xbox one", "Xbox360": "xbox 360",
        "XboxOneStreaming": "xbox one", "Win32": "windows", "PC": "windows",
        "WindowsOneCore": "windows", "Mobile": "windows"}
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")


def _post_json(url, payload, headers=None):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _extract_code(raw):
    """Bare code, a code=... pair, or the whole oauth20_desktop.srf?code=…&lc=…
    URL. The address-bar copy is percent-encoded (e.g. the trailing `$$` shows as
    %24%24), so URL-decode whatever we pull out before returning it."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("code"):
            return str(obj["code"]).strip()          # JSON value is already decoded
    except (ValueError, TypeError):
        pass
    import re
    # a code=… anywhere (full URL or a code=… pair) — stop at & and decode
    m = re.search(r'[?&]?code=([^&\s"\']+)', raw)
    if m:
        return urllib.parse.unquote(m.group(1))
    # bare value straight off the address bar — may still be percent-encoded
    return urllib.parse.unquote(raw.strip().strip('"\''))


def _save(tok):
    tok["_saved_at"] = int(time.time())
    json.dump(tok, open(TOKFILE, "w"))


# --- Microsoft OAuth ------------------------------------------------------- #
def ms_tokens_from_code(code):
    return _post_form(MS_TOKEN, {
        "client_id": MS_CLIENT, "code": code, "grant_type": "authorization_code",
        "redirect_uri": MS_REDIRECT, "scope": MS_SCOPE})


def ms_refresh(refresh_token):
    return _post_form(MS_TOKEN, {
        "client_id": MS_CLIENT, "refresh_token": refresh_token,
        "grant_type": "refresh_token", "scope": MS_SCOPE})


def ms_access_token():
    """Fresh MS access token from the cached refresh token."""
    if not os.path.exists(TOKFILE):
        raise SystemExit("Xbox: not connected — run --code <code> first")
    tok = json.load(open(TOKFILE))
    fresh = ms_refresh(tok["refresh_token"])   # cheap; always refresh for a clean token
    _save(fresh)
    return fresh["access_token"]


# --- Xbox Live token chain ------------------------------------------------- #
def xbl_user_token(ms_access):
    data = _post_json(XASU, {
        "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT",
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                       "RpsTicket": "d=%s" % ms_access}},
        {"x-xbl-contract-version": "1"})
    return data["Token"]


def xsts_token(user_token, relying_party):
    data = _post_json(XSTS, {
        "RelyingParty": relying_party, "TokenType": "JWT",
        "Properties": {"UserTokens": [user_token], "SandboxId": "RETAIL"}},
        {"x-xbl-contract-version": "1"})
    claims = (data.get("DisplayClaims", {}).get("xui") or [{}])[0]
    return data["Token"], claims  # claims has uhs, and (for xboxlive RP) xid/gtg


def _auth_header(uhs, xsts):
    return "XBL3.0 x=%s;%s" % (uhs, xsts)


# --- Owned games (titlehub) ------------------------------------------------ #
# NOTE: the Microsoft Store *collections* API (true purchase entitlements) only
# returns products registered to the querying app in Partner Center, so a generic
# third-party client gets an EMPTY collection — verified against a live account.
# titlehub (the account's title history: games launched/owned across Xbox + PC) is
# the path every third-party Xbox tool uses; type==Game drops apps. It's play-
# history-based, so it can include Game Pass titles and miss never-launched ones.
def fetch_owned(ms_access):
    """Owned/played Xbox games as (titleId, name) rows, via titlehub."""
    user_tok = xbl_user_token(ms_access)
    xsts, claims = xsts_token(user_tok, RP_XBOXLIVE)
    xuid = claims.get("xid")
    url = ("https://titlehub.xboxlive.com/users/xuid(%s)/titles/titlehistory/"
           "decoration/detail" % xuid)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", _auth_header(claims["uhs"], xsts))
    req.add_header("x-xbl-contract-version", "2")
    req.add_header("Accept-Language", "en-US")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    seen, out = set(), []
    for t in data.get("titles", []):
        if t.get("type") != "Game":          # drop apps/media
            continue
        name = t.get("name") or ""
        if not name:
            continue
        tid = t.get("titleId") or t.get("modernTitleId") or ""
        # one row per console the title is available on (Xbox devices array)
        devs = [_DEV.get(d, None) for d in (t.get("devices") or [])]
        for plat in (sorted(set(p for p in devs if p)) or ["xbox"]):
            key = (name.lower(), plat)
            if key not in seen:
                seen.add(key)
                out.append((tid, name, plat))
    out.sort(key=lambda x: (x[1] or "").lower())
    return out


def connect(code):
    tok = ms_tokens_from_code(code)
    _save(tok)
    # prove the whole chain works while the code is fresh
    xsts, claims = xsts_token(xbl_user_token(tok["access_token"]), RP_XBOXLIVE)
    return claims.get("gtg", "")


# --- Device-code flow (no address-bar code to copy) ------------------------ #
def device_start():
    """Begin the device-code flow. Returns Microsoft's response dict:
    {user_code, device_code, verification_uri, interval, expires_in}."""
    return _post_form(MS_DEVICE, {
        "client_id": MS_CLIENT, "scope": MS_SCOPE, "response_type": "device_code"})


def device_poll(device_code):
    """Poll once for the device-code result. Returns (status, gamertag):
      ("connected", gtg) — approved; tokens saved + full chain verified
      ("pending",  "")   — not finished yet; keep polling
      ("expired",  "")   — code expired / invalid; start over
      ("declined", "")   — user declined
    """
    try:
        tok = _post_form(MS_TOKEN, {
            "client_id": MS_CLIENT, "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get("error", "")
        except Exception:
            err = ""
        if err in ("authorization_pending", "slow_down"):
            return "pending", ""
        if err == "authorization_declined":
            return "declined", ""
        return "expired", ""      # expired_token / bad_verification_code / invalid_grant
    _save(tok)
    # prove the whole chain works while the token is fresh (same as connect())
    _xsts, claims = xsts_token(xbl_user_token(tok["access_token"]), RP_XBOXLIVE)
    return "connected", claims.get("gtg", "")


def main(argv):
    if len(argv) > 1 and argv[1] == "--code":
        code = _extract_code(argv[2] if len(argv) > 2 else "")
        if not code:
            raise SystemExit("Xbox: no auth code provided")
        gtg = connect(code)
        print("# Xbox connected%s" % (" (%s)" % gtg if gtg else ""), file=sys.stderr)
        return
    if len(argv) > 1 and argv[1] == "--device-start":
        print(json.dumps(device_start()))
        return
    if len(argv) > 1 and argv[1] == "--device-poll":
        status, gtg = device_poll(argv[2] if len(argv) > 2 else "")
        print(json.dumps({"status": status, "account": gtg}))
        return
    if len(argv) > 1 and argv[1] == "--whoami":
        tok = ms_access_token()
        xsts, claims = xsts_token(xbl_user_token(tok), RP_XBOXLIVE)
        print("# Xbox auth OK — %s (xuid %s)"
              % (claims.get("gtg", "?"), claims.get("xid", "?")), file=sys.stderr)
        return
    rows = fetch_owned(ms_access_token())
    for pid, title, plat in rows:
        print("%s\t%s\t%s" % (pid, title, plat))
    print("# owned Xbox games: %d" % len(rows), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv)
