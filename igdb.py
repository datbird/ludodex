#!/usr/bin/env python3
"""IGDB (igdb.com) metadata provider — shared API client + record mapping.

IGDB is a METADATA PROVIDER, not an ownership source: it is consulted to fill in
MISSING attributes (genres, themes, game modes, developers/publishers, release
dates, ratings) on games already in the catalog. It never adds ownership.

Auth is via Twitch application credentials (Client ID + Secret) exchanged for an
OAuth client-credentials token. Used by:
  - igdb_enrich.py     — resolve catalog games to IGDB ids + cache their records
  - build_library.py   — merge cached records into game_attributes (fill-gaps)

Attribute names map onto the same vocabulary as the Playnite interchange
(genres / developers / publishers / series / release_year / description / scores)
plus a few IGDB-specific kinds (themes, game_modes, player_perspectives).
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.igdb.com/v4"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"

# IGDB external_games.external_game_source -> store. Used to resolve a game by
# its store id. NOTE: IGDB deprecated the old `category` field on external_games
# in favour of `external_game_source` (Steam stayed 1; others were renumbered).
EXTERNAL_SOURCE = {"steam": 1, "gog": 5, "microsoft": 11, "epic": 26,
                   "itch": 30}
STEAM_SOURCE = 1

# Fields requested for a full game record (with nested expansions).
GAME_FIELDS = (
    "id,name,slug,summary,first_release_date,"
    "genres.name,themes.name,game_modes.name,player_perspectives.name,"
    "franchises.name,involved_companies.developer,"
    "involved_companies.publisher,involved_companies.company.name,"
    "total_rating,aggregated_rating"
)

MIN_INTERVAL = 0.26            # IGDB rate limit ~4 req/s — throttle to stay under
_last = [0.0]


def _throttle():
    dt = time.time() - _last[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last[0] = time.time()


def get_token(client_id, client_secret):
    """Mint a Twitch OAuth app token (client-credentials). -> (token, ttl_secs)."""
    data = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.load(r)
    return j["access_token"], int(j.get("expires_in", 3600))


def query(endpoint, body, client_id, token, retries=4):
    """POST an APICalypse query; return the parsed JSON list. Retries 429/5xx."""
    url = "%s/%s" % (API, endpoint)
    headers = {"Client-ID": client_id, "Authorization": "Bearer " + token,
               "Accept": "application/json"}
    delay = 1.0
    for attempt in range(retries + 1):
        _throttle()
        req = urllib.request.Request(url, data=body.encode(), headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    return []


def _names(items):
    return [i["name"] for i in (items or [])
            if isinstance(i, dict) and i.get("name")]


def map_record(g):
    """IGDB game JSON -> ludodex attribute dict (TEXTUAL only; no artwork yet).

    Returns {kind: list-or-scalar}; build_library writes them fill-gaps."""
    out = {}
    for kind, key in (("genres", "genres"), ("themes", "themes"),
                      ("game_modes", "game_modes"),
                      ("player_perspectives", "player_perspectives"),
                      ("series", "franchises")):
        vals = _names(g.get(key))
        if vals:
            out[kind] = vals
    devs, pubs = [], []
    for ic in (g.get("involved_companies") or []):
        nm = (ic.get("company") or {}).get("name")
        if not nm:
            continue
        if ic.get("developer"):
            devs.append(nm)
        if ic.get("publisher"):
            pubs.append(nm)
    if devs:
        out["developers"] = sorted(set(devs))
    if pubs:
        out["publishers"] = sorted(set(pubs))
    ts = g.get("first_release_date")
    if ts:
        t = time.gmtime(ts)
        out["release_date"] = time.strftime("%Y-%m-%d", t)
        out["release_year"] = t.tm_year
    if g.get("summary"):
        out["description"] = g["summary"]
    if g.get("total_rating"):
        out["community_score"] = round(g["total_rating"])
    if g.get("aggregated_rating"):
        out["critic_score"] = round(g["aggregated_rating"])
    return out
