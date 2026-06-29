#!/usr/bin/env python3
"""List Steam games owned by an account, via the Steam Web API.

Usage: STEAM_API_KEY=xxxx steam_owned.py [vanity_or_steamid64]
Defaults the account to vanity 'datbird'. Needs the profile's Game-details
privacy = Public. Prints a TSV (appid<TAB>name) to stdout, count to stderr.
"""
import os
import sys
import json
import ssl
import urllib.request

KEY = os.environ.get("STEAM_API_KEY", "").strip()
# Default to the real logged-in account (<steam-id>, AccountName "datbird").
# NOTE: the vanity URL /id/datbird is a DIFFERENT account (<steam-id-2>,
# persona "arkkytori999") — do NOT resolve by vanity here.
who = sys.argv[1] if len(sys.argv) > 1 else "<steam-id>"
CTX = ssl.create_default_context()


def api(path):
    url = "https://api.steampowered.com/" + path
    with urllib.request.urlopen(url, context=CTX, timeout=20) as r:
        return json.load(r)


if not KEY:
    sys.exit("set STEAM_API_KEY")

# resolve vanity -> steamid64 if needed
if who.isdigit() and len(who) >= 16:
    steamid = who
else:
    r = api("ISteamUser/ResolveVanityURL/v1/?key=%s&vanityurl=%s" % (KEY, who))
    res = r.get("response", {})
    if res.get("success") != 1:
        sys.exit("could not resolve vanity %r (%s)" % (who, res.get("message")))
    steamid = res["steamid"]

print("# steamid64:", steamid, file=sys.stderr)
r = api("IPlayerService/GetOwnedGames/v1/?key=%s&steamid=%s"
        "&include_appinfo=1&include_played_free_games=1&format=json" % (KEY, steamid))
games = (r.get("response") or {}).get("games")
if games is None:
    sys.exit("no games returned — is the profile's Game-details set to Public?")
games.sort(key=lambda g: g.get("name", "").lower())
for g in games:
    print("%s\t%s" % (g.get("appid"), g.get("name", "")))
print("# total Steam games owned: %d" % len(games), file=sys.stderr)
