#!/usr/bin/env python3
"""Embedded GOG Galaxy OAuth client — public, shared, needed regardless of user.

GOG has no per-app registration: its API only accepts GOG Galaxy's own OAuth
client, so every GOG tool (Heroic, gogdl, Lutris, …) uses this exact client id +
secret. It ships inside every copy of GOG Galaxy and is public knowledge — it is
NOT a per-user secret and does not identify your account (that's the one-time
login `code` you paste). Kept here as a code constant — like the EA client ids
(ea_owned.py) and the ScreenScraper devid (_ssauth.py) — rather than in the
user-config DB, and out of the Settings UI: it's framework data, not your data.

Override via env GOG_CLIENT_ID / GOG_CLIENT_SECRET, or config keys
gog_client_id / gog_client_secret, only if GOG ever rotates it
(see config.gog_creds).
"""
CLIENT_ID = "46899977096215655"
CLIENT_SECRET = "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"


def client():
    """(client_id, client_secret) for the GOG Galaxy public OAuth client."""
    return CLIENT_ID, CLIENT_SECRET


if __name__ == "__main__":
    print("embedded GOG Galaxy client id:", CLIENT_ID)
