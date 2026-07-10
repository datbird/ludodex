#!/usr/bin/env python3
"""Pull owned games from a Nintendo Account — the 3rd console-store leg.

Nintendo has no official library API (like PSN/Xbox), and it's the fiddliest of
the three: the account OAuth is the Nintendo Switch Online app's PKCE flow, and a
usable "what do I own" surface has to be reverse-engineered. Auth is verified and
solid; the GAMES-LIST fetch is pinned against a live token (see fetch_owned()).

Auth is a two-step connect, like Epic/EA/PSN but PKCE so it needs both a "get the
link" step and a "paste the result" step:

  1. Get the sign-in link:   python3 nintendo_owned.py --authorize
     Open it, sign in. On the "Select this account" screen, DON'T just click the
     red button — RIGHT-CLICK it and "Copy Link Address" (it points at an
     npf…://auth#session_token_code=… URL the browser can't follow itself).
  2. Paste that whole link back:
       python3 nintendo_owned.py --login '<paste the copied link (or just the code)>'
     We extract the session_token_code, exchange it (with the PKCE verifier saved
     in step 1) for a durable session_token, cache it, and verify (whoami).

  python3 nintendo_owned.py --whoami   # verify auth (prints your Nintendo nickname)
  python3 nintendo_owned.py            # print owned games TSV
  python3 nintendo_owned.py > nintendo_games.tsv   # (update.sh does this)

Tokens live only in .nintendo/ (session_token is long-lived; access tokens are
refreshed non-interactively). ludodex never reads 1Password at runtime. stdlib-only.
"""
import base64
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config

# The Nintendo Switch Online app's public OAuth client — the same value shipped in
# the app and used by every NSO tool (nxapi, pynintendoparental, …). Not a secret,
# doesn't identify you (that's the login). Overridable via config if Nintendo rotates.
CLIENT_ID = os.environ.get("NINTENDO_CLIENT_ID", "") or \
    (config.get("nintendo_client_id") if hasattr(config, "get") else "") or "71b963c1b7b6d119"
REDIRECT = "npf%s://auth" % CLIENT_ID
SCOPE = "openid user user.birthday user.mii user.screenName"
UA = "com.nintendo.znca/2.10.0 (Android/13)"

ACCOUNTS = "https://accounts.nintendo.com"
API_ACCOUNTS = "https://api.accounts.nintendo.com"

NDIR = os.path.join(DATA, ".nintendo")
PKCE_FILE = os.path.join(NDIR, "pkce.json")       # verifier held between step 1 and 2
TOKENS = os.path.join(NDIR, "tokens.json")        # {session_token, access_token, expires_at, ...}
os.makedirs(NDIR, exist_ok=True)
CTX = ssl.create_default_context()


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _post(url, data, headers, timeout=30):
    body = data if isinstance(data, bytes) else urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
        return json.load(r)


def _get(url, headers, timeout=30):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
        return json.load(r)


# --------------------------------------------------------------------------- #
#  step 1: the sign-in link (PKCE) — saves the verifier for step 2
# --------------------------------------------------------------------------- #
def authorize_url():
    verifier = _b64url(os.urandom(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _b64url(os.urandom(36))
    with open(PKCE_FILE, "w") as f:
        json.dump({"verifier": verifier, "state": state, "at": int(time.time())}, f)
    os.chmod(PKCE_FILE, 0o600)
    params = {
        "state": state, "redirect_uri": REDIRECT, "client_id": CLIENT_ID,
        "scope": SCOPE, "response_type": "session_token_code",
        "session_token_code_challenge": challenge,
        "session_token_code_challenge_method": "S256", "theme": "login_form",
    }
    return ACCOUNTS + "/connect/1.0.0/authorize?" + urllib.parse.urlencode(params)


def _extract_code(pasted):
    """Accept the whole copied 'Select this account' link, a raw code, or JSON —
    whatever's easiest to paste. Returns the session_token_code."""
    s = (pasted or "").strip().strip('"').strip("'")
    if not s:
        return ""
    if s.startswith("{"):
        try:
            return json.loads(s).get("session_token_code", "")
        except ValueError:
            pass
    if "session_token_code=" in s:                # a full npf…://auth#…=… link
        frag = s.split("#", 1)[-1] if "#" in s else s
        for part in frag.replace("?", "&").split("&"):
            if part.startswith("session_token_code="):
                return urllib.parse.unquote(part.split("=", 1)[1])
    return s                                       # assume they pasted just the code


# --------------------------------------------------------------------------- #
#  step 2: exchange the code -> a durable session_token
# --------------------------------------------------------------------------- #
def exchange(code):
    if not os.path.exists(PKCE_FILE):
        sys.exit("nintendo: run `nintendo_owned.py --authorize` first (need the "
                 "PKCE verifier from that step)")
    verifier = json.load(open(PKCE_FILE)).get("verifier")
    try:
        res = _post(ACCOUNTS + "/connect/1.0.0/api/session_token",
                    {"client_id": CLIENT_ID, "session_token_code": code,
                     "session_token_code_verifier": verifier},
                    {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"})
    except urllib.error.HTTPError as e:
        sys.exit("nintendo: code exchange failed (%s) — the code may have expired "
                 "(they're short-lived); re-run --authorize. %s"
                 % (e.code, e.read().decode()[:200]))
    st = res.get("session_token")
    if not st:
        sys.exit("nintendo: no session_token returned: %s" % json.dumps(res)[:200])
    save_tokens({"session_token": st})
    try:
        os.remove(PKCE_FILE)
    except OSError:
        pass
    return st


def save_tokens(update):
    cur = {}
    if os.path.exists(TOKENS):
        try:
            cur = json.load(open(TOKENS))
        except ValueError:
            cur = {}
    cur.update(update)
    with open(TOKENS, "w") as f:
        json.dump(cur, f)
    os.chmod(TOKENS, 0o600)


# --------------------------------------------------------------------------- #
#  session_token -> short-lived access token (refreshed non-interactively)
# --------------------------------------------------------------------------- #
def access_token(force=False):
    if not os.path.exists(TOKENS):
        sys.exit("nintendo: not logged in — run `nintendo_owned.py --authorize` "
                 "then `--login`")
    tok = json.load(open(TOKENS))
    if not force and tok.get("access_token") and tok.get("expires_at", 0) > time.time():
        return tok["access_token"]
    st = tok.get("session_token")
    if not st:
        sys.exit("nintendo: no session_token cached — re-run --authorize/--login")
    try:
        res = _post(ACCOUNTS + "/connect/1.0.0/api/token",
                    json.dumps({"client_id": CLIENT_ID, "session_token": st,
                                "grant_type": "urn:ietf:params:oauth:grant-type:"
                                "jwt-bearer-session-token"}).encode(),
                    {"User-Agent": UA, "Content-Type": "application/json",
                     "Accept": "application/json"})
    except urllib.error.HTTPError as e:
        sys.exit("nintendo: token refresh failed (%s) — your login may have been "
                 "revoked; re-run --authorize/--login. %s"
                 % (e.code, e.read().decode()[:200]))
    at = res.get("access_token")
    save_tokens({"access_token": at, "id_token": res.get("id_token"),
                 "expires_at": int(time.time()) + int(res.get("expires_in", 900)) - 60})
    return at


def whoami():
    me = _get(API_ACCOUNTS + "/2.0.0/users/me",
              {"Authorization": "Bearer " + access_token(), "User-Agent": UA,
               "Accept": "application/json"})
    return me


# --------------------------------------------------------------------------- #
#  library — the reverse-engineered part (pinned against a live token)
# --------------------------------------------------------------------------- #
def fetch_owned():
    """Owned/played Nintendo titles as (id, title) rows.

    NOTE: Nintendo exposes no clean owned-games API. This resolves against a LIVE
    token (run --whoami first to confirm auth). The candidate source is the account
    access token's reachable surfaces; if the games list needs an endpoint we can't
    reach with the account token alone (eShop device auth / the NSO f-token play-log),
    fetch_owned stays empty and prints why — so the rest of the sync never breaks.
    """
    me = whoami()                                   # proves the token chain works
    print("# nintendo: authenticated as %s (%s)"
          % (me.get("nickname") or me.get("id"), me.get("country") or "?"),
          file=sys.stderr)
    rows = []
    # Nintendo has NO server-readable owned/purchases API (purchase history is locked
    # to the console via a device certificate). The only reachable "your games" surface
    # is the Switch Online PLAY ACTIVITY, which requires an f-token from a third-party
    # helper (imink) — and as of this writing that helper is down (its TLS cert expired
    # 2026-07-05). So this returns empty until the play-activity route is wired against
    # a working f-token provider. Auth itself is fine (whoami above succeeded).
    if not rows:
        print("# nintendo: auth OK. Owned games unavailable — Nintendo exposes no "
              "purchases API to a server; the play-activity route needs the imink "
              "f-token helper, currently unavailable.", file=sys.stderr)
    return rows


# --------------------------------------------------------------------------- CLI
def main(argv):
    if "--authorize" in argv:
        url = authorize_url()
        print("Open this URL, sign in, then RIGHT-CLICK 'Select this account' and "
              "Copy Link Address:\n", file=sys.stderr)
        print(url)
        print("\nThen: python3 nintendo_owned.py --login '<paste the copied link>'",
              file=sys.stderr)
        return 0
    if "--login" in argv:
        i = argv.index("--login")
        pasted = argv[i + 1] if i + 1 < len(argv) else ""
        code = _extract_code(pasted)
        if not code:
            sys.exit("nintendo: pass the copied link or code: --login '<link|code>'")
        exchange(code)
        me = whoami()
        print("# nintendo: logged in as %s ✓" % (me.get("nickname") or me.get("id")),
              file=sys.stderr)
        return 0
    if "--whoami" in argv:
        me = whoami()
        print(me.get("nickname") or me.get("id") or "(unknown)")
        return 0
    rows = fetch_owned()
    rows.sort(key=lambda x: (x[1] or "").lower())
    for gid, title in rows:
        if title:
            print("%s\t%s" % (gid, title))
    print("# owned Nintendo games: %d" % len(rows), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
