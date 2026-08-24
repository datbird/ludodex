#!/usr/bin/env python3
"""List a Steam account's WISHLIST (wanted, not owned) via the Steam Web API.

The Discover/"Wanted" mirror of steam_owned.py — same Web API key + steam_id.
IWishlistService/GetWishlist returns appids (+priority); titles are resolved from
a cached ISteamApps/GetAppList map (one ~10 MB call, only when there are new
appids), with a small appdetails fallback for unreleased/coming-soon titles that
aren't in the app list yet. Prints a TSV (appid<TAB>name) to stdout, status to
stderr — same shape as steam_owned.py so the Wanted ingestion mirrors owned.

Usage: STEAM_API_KEY=xxxx steam_wishlist.py [vanity_or_steamid64]
With no argument, uses the `steam_id` from config. The key bypasses profile
privacy only for its OWNER's SteamID, so set steam_id to the key's account.
"""
import os
import sys
import ssl
import json
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
KEY = config.steam_key()                # env STEAM_API_KEY > config steam_api_key
who = sys.argv[1] if len(sys.argv) > 1 else config.get("steam_id")
CTX = ssl.create_default_context()
SDIR = os.path.join(DATA, ".steam")
os.makedirs(SDIR, exist_ok=True)
NAMECACHE = os.path.join(SDIR, "wishlist_names.json")   # appid -> title, incremental


def api(host, path):
    url = "https://%s/%s" % (host, path)
    with urllib.request.urlopen(url, context=CTX, timeout=45) as r:
        return json.load(r)


def _batch(chunk):
    """Names for one batch of appids. Raises whatever the transport raised."""
    inp = {"ids": [{"appid": a} for a in chunk],
           "context": {"language": "english", "country_code": "US", "steam_realm": 1},
           "data_request": {"include_basic_info": True}}
    d = api("api.steampowered.com",
            "IStoreBrowseService/GetItems/v1/?key=%s&input_json=%s"
            % (KEY, urllib.parse.quote(json.dumps(inp))))
    return {it.get("appid"): it.get("name", "")
            for it in (d.get("response") or {}).get("store_items") or []}


def resolve_names(appids, cache, fetch=None):
    """Fill `cache` (appid-as-string -> title) for anything missing. Returns it.

    A FAILED BATCH MUST NOT BE CACHED. The old loop wrote `cache[str(a)] = ""` for every
    appid in the chunk even when the call had raised, and the next run's `missing` test
    is `str(a) not in cache` — so the key was present, the retry never happened, and one
    429 turned a hundred wishlist entries into permanently blank names.

    An empty name is only recorded when the API ANSWERED and did not include that appid,
    which is a real "Steam has no store item for this" and worth remembering: that is the
    negative cache, and it is the only case where a blank is knowledge rather than a
    failure to look."""
    fetch = fetch or _batch
    missing = [a for a in appids if str(a) not in cache]
    wrote = False
    for i in range(0, len(missing), 100):        # batch 100 appids per call
        chunk = missing[i:i + 100]
        try:
            got = fetch(chunk)
        except Exception as e:                   # noqa: BLE001 — retry this chunk next run
            print("# StoreBrowse batch failed (%s) — %d name(s) left for the next run"
                  % (str(e)[:100], len(chunk)), file=sys.stderr)
            continue
        for a in chunk:
            cache[str(a)] = got.get(a, "")
        wrote = True
    if wrote:
        try:
            config.write_private_json(NAMECACHE, cache)
        except OSError:
            pass
    return cache


def main():
    if not KEY:
        sys.exit("set STEAM_API_KEY")
    if not who:
        sys.exit("no SteamID — set it with: python3 ludodex/config.py set steam_id <id>")

    # resolve vanity -> steamid64 if needed
    if who.isdigit() and len(who) >= 16:
        steamid = who
    else:
        r = api("api.steampowered.com",
                "ISteamUser/ResolveVanityURL/v1/?key=%s&vanityurl=%s" % (KEY, who))
        res = r.get("response", {})
        if res.get("success") != 1:
            sys.exit("could not resolve vanity %r (%s)" % (who, res.get("message")))
        steamid = res["steamid"]
    print("# steamid64:", steamid, file=sys.stderr)

    r = api("api.steampowered.com",
            "IWishlistService/GetWishlist/v1/?key=%s&steamid=%s" % (KEY, steamid))
    items = (r.get("response") or {}).get("items") or []
    appids = [it["appid"] for it in items if it.get("appid")]
    if not items:
        print("# wishlist empty, or the profile's wishlist isn't visible to this key",
              file=sys.stderr)

    cache = {}
    if os.path.exists(NAMECACHE):
        try:
            cache = json.load(open(NAMECACHE, encoding="utf-8"))
        except ValueError:
            cache = {}
    resolve_names(appids, cache)

    rows = [(a, cache.get(str(a), "")) for a in appids]
    rows.sort(key=lambda x: (x[1] or "~").lower())   # named titles first, alphabetical
    for appid, name in rows:
        print(config.tsv_row(appid, name))
    print("# Steam wishlist: %d titles (%d unresolved names)"
          % (len(rows), sum(1 for _, n in rows if not n)), file=sys.stderr)


if __name__ == "__main__":
    main()
