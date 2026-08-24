"""Cloudflare Access SSO passthrough — verify the Access JWT and extract the
authenticated email.

When ludodex sits behind a Cloudflare Access application, Cloudflare authenticates
the user and forwards every request to the origin with a signed JWT in the
`Cf-Access-Jwt-Assertion` header (also mirrored in the `CF_Authorization` cookie).
We verify that token against the team's public certs and the application's AUD
tag — never trusting the plaintext email header on its own, so a request that
bypasses Cloudflare can't spoof an identity.

Config (set in Settings → Account & Users → Cloudflare Access):
  team_domain  e.g. "yourteam.cloudflareaccess.com"  (certs live under it)
  aud          the Access application's Audience (AUD) tag
"""
import sys

import jwt
from jwt import PyJWKClient

# One PyJWKClient per team domain — it fetches + caches the signing keys.
_clients = {}

# Causes already reported. Every unauthenticated request reaches verify_email, so an
# unguarded log line would be a flood — but silence is what made a misconfigured
# team_domain indistinguishable from "nobody is logged in".
_logged = set()


def _log_once(key, msg):
    if key in _logged:
        return
    _logged.add(key)
    print(msg, file=sys.stderr, flush=True)


def _client(team_domain):
    url = "https://%s/cdn-cgi/access/certs" % team_domain.strip().strip("/")
    c = _clients.get(url)
    if c is None:
        c = PyJWKClient(url, cache_keys=True)
        _clients[url] = c
    return c


def verify_email(token, team_domain, aud):
    """Return the verified email from a Cloudflare Access JWT, or None if the
    token is missing/invalid/expired or the audience/issuer doesn't match."""
    if not token or not team_domain or not aud:
        return None
    team_domain = team_domain.strip().strip("/")
    try:
        signing_key = _client(team_domain).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, signing_key.key, algorithms=["RS256"],
            audience=aud, issuer="https://%s" % team_domain,
            options={"require": ["exp", "iss", "aud"]},
        )
    except (jwt.exceptions.PyJWKClientError, jwt.exceptions.PyJWKError) as e:
        # THE KEY SET, NOT THE TOKEN — and it must be caught BEFORE PyJWTError, because
        # PyJWKClientConnectionError inherits from it. Classifying an unreachable JWKS
        # endpoint as "bad token" is the same silence this fix exists to remove.
        _log_once("jwks-%s" % type(e).__name__,
                  "cf_access: could not fetch the signing keys for %s (%s: %s). Check "
                  "the team_domain and that this host can reach "
                  "https://%s/cdn-cgi/access/certs"
                  % (team_domain, type(e).__name__, str(e)[:200], team_domain))
        return None
    except jwt.PyJWTError as e:
        # A REJECTED TOKEN IS A NORMAL EVENT. Expired, wrong audience, forged — the
        # answer is "not this user", and returning None is right. Logged once per
        # distinct cause so a genuine misconfiguration that presents as a bad token is
        # still visible, without a line per unauthenticated request.
        _log_once("token-%s" % type(e).__name__,
                  "cf_access: token rejected (%s)" % type(e).__name__)
        return None
    except Exception as e:                       # noqa: BLE001
        # A BROKEN SETUP IS NOT. A JWKS fetch that fails, or a team_domain with a typo,
        # produced exactly the same silent None — so an admin debugging SSO saw a wall of
        # 401s with nothing anywhere saying the certs could not be fetched. Still None,
        # because failing open would be worse; but it says so, once per distinct cause,
        # so a restart loop cannot bury the message.
        _log_once("setup-%s" % type(e).__name__,
                  "cf_access: could not verify against %s — %s: %s. Check the "
                  "team_domain and that this host can reach "
                  "https://%s/cdn-cgi/access/certs"
                  % (team_domain, type(e).__name__, str(e)[:200], team_domain))
        return None
    email = (claims.get("email") or claims.get("identity") or "").strip().lower()
    return email or None
