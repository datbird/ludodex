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
import jwt
from jwt import PyJWKClient

# One PyJWKClient per team domain — it fetches + caches the signing keys.
_clients = {}


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
    except Exception:
        return None
    email = (claims.get("email") or claims.get("identity") or "").strip().lower()
    return email or None
