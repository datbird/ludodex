#!/usr/bin/env python3
"""List Steam games owned by an account, via the Steam Web API.

Usage: STEAM_API_KEY=xxxx steam_owned.py [vanity_or_steamid64]
With no argument, uses the `steam_id` from config (config.py). The Web API key
bypasses profile privacy only for its OWNER's SteamID, so set steam_id to the
account that generated the key. Prints a TSV (appid<TAB>name) to stdout.
"""
import os
import sys
import json
import ssl
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

KEY = os.environ.get("STEAM_API_KEY", "").strip()
# No arg -> the configured account. NOTE: a vanity URL can resolve to a DIFFERENT
# account than the one that owns the API key — prefer the explicit steam_id.
who = sys.argv[1] if len(sys.argv) > 1 else config.get("steam_id")
CTX = ssl.create_default_context()


def api(path):
    url = "https://api.steampowered.com/" + path
    with urllib.request.urlopen(url, context=CTX, timeout=20) as r:
        return json.load(r)


if not KEY:
    sys.exit("set STEAM_API_KEY")
if not who:
    sys.exit("no SteamID — set it with: python3 config.py set steam_id <id>")

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
free = "1" if config.get_bool("steam_include_free", True) else "0"
r = api("IPlayerService/GetOwnedGames/v1/?key=%s&steamid=%s"
        "&include_appinfo=1&include_played_free_games=%s&format=json"
        % (KEY, steamid, free))
games = (r.get("response") or {}).get("games")
if games is None:
    sys.exit("no games returned — is the profile's Game-details set to Public?")
games.sort(key=lambda g: g.get("name", "").lower())
for g in games:
    print("%s\t%s" % (g.get("appid"), g.get("name", "")))
print("# total Steam games owned: %d" % len(games), file=sys.stderr)
