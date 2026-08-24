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
import random
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

# Fields requested for a full game record (with nested expansions). Includes the
# platforms the game released on and per-platform release dates, so ludodex can
# offer "this game also came out on …" in the ownership overlay.
GAME_FIELDS = (
    # game_type distinguishes a BUNDLE/pack from a game (3 = bundle). Authoritative,
    # free, and the deterministic half of match verification: a bundle's identity may
    # never define an individually-owned app (see the match-verification design).
    # parent_game: which game an add-on extends. The MIRROR carries this as a column and
    # is what build_library reads, but a cached record that lacks it can never say
    # what it belongs to, so ask for it here too.
    "id,name,slug,summary,first_release_date,alternative_names.name,game_type,"
    "parent_game,"
    "genres.name,themes.name,game_modes.name,player_perspectives.name,"
    "franchises.name,involved_companies.developer,"
    "involved_companies.publisher,involved_companies.company.name,"
    "platforms.name,platforms.abbreviation,"
    "release_dates.y,release_dates.human,release_dates.platform.name,"
    "release_dates.platform.abbreviation,"
    "total_rating,total_rating_count,aggregated_rating,aggregated_rating_count,"
    "rating,rating_count,"
    # Age ratings, in IGDB's CURRENT shape. The older `age_ratings.category` /
    # `.rating` enums were replaced by named `organization` / `rating_category`
    # objects; asking for the old names now returns an error, not empty data, so
    # this was verified against the live API rather than written from memory.
    "age_ratings.organization.name,age_ratings.rating_category.rating,"
    "age_ratings.rating_content_descriptions.description"
)

# The rating bodies worth storing, and the order a badge should prefer them in.
# ESRB is what this library is browsed by; the rest cost nothing to keep and make
# an import from a PAL/JP-heavy source legible.
_RATING_BODIES = ("ESRB", "PEGI", "CERO", "ACB", "USK", "CLASS_IND", "GRAC")


def age_ratings(g):
    """-> (esrb_rating, [content descriptors], ["ESRB: M", "PEGI: 18", …]).

    ESRB's own content descriptors are preferred for the descriptor list, since that
    is the body being displayed; another body's descriptors are used only when ESRB
    supplied none, so the words never contradict the badge beside them."""
    esrb, all_ratings, esrb_desc, other_desc = None, [], [], []
    for ar in (g.get("age_ratings") or []):
        org = ((ar.get("organization") or {}).get("name") or "").strip()
        rating = ((ar.get("rating_category") or {}).get("rating") or "").strip()
        if not org or not rating:
            continue
        descs = [d.get("description") for d in
                 (ar.get("rating_content_descriptions") or []) if d.get("description")]
        if org == "ESRB":
            esrb = rating
            esrb_desc = descs
        elif org in _RATING_BODIES:
            other_desc = other_desc or descs
        if org in _RATING_BODIES:
            all_ratings.append("%s: %s" % (org, rating))
    return esrb, sorted(set(esrb_desc or other_desc)), all_ratings

# Vendor words dropped when slugging a platform that has no abbreviation, so
# "Nintendo GameCube" -> "gamecube" rather than "nintendogamecube".
_PLATFORM_VENDORS = {"nintendo", "sony", "microsoft", "sega", "atari", "nec",
                     "snk", "commodore", "sinclair", "bandai", "nintendo's"}


def _platform_slug(abbr, name):
    """A terse, stable id for a platform — the abbreviation if present (SNES->snes,
    PS4->ps4), else the name minus a leading vendor word, alphanumerics only."""
    import re
    base = (abbr or "").strip()
    if not base:
        words = [w for w in re.split(r"\s+", (name or "").strip()) if w]
        if words and words[0].lower() in _PLATFORM_VENDORS:
            words = words[1:]
        base = "".join(words)
    return re.sub(r"[^a-z0-9]", "", base.lower())


def platform_entry(p):
    """{id,name,abbr} from an IGDB platform dict (or a {'name','abbreviation'})."""
    if not isinstance(p, dict):
        return None
    name = (p.get("name") or "").strip()
    abbr = (p.get("abbreviation") or "").strip()
    if not name and not abbr:
        return None
    return {"id": _platform_slug(abbr, name) or (name or abbr).lower(),
            "name": name or abbr, "abbr": abbr}


def releases(g):
    """Distinct per-platform releases for an IGDB game record ->
    [{id,name,abbr,year,human}] sorted by year then name. Prefers release_dates
    (per-platform year); platforms with no dated release are still listed."""
    out = {}
    for rd in (g.get("release_dates") or []):
        e = platform_entry(rd.get("platform"))
        if not e:
            continue
        y = rd.get("y")
        prev = out.get(e["id"])
        # keep the earliest known year for a platform (first release, not a re-release)
        if not prev or (y and (not prev.get("year") or y < prev["year"])):
            out[e["id"]] = {**e, "year": y, "human": rd.get("human")}
    for p in (g.get("platforms") or []):
        e = platform_entry(p)
        if e:
            out.setdefault(e["id"], {**e, "year": None, "human": None})
    return sorted(out.values(), key=lambda r: (r.get("year") or 9999, r["name"]))

MIN_INTERVAL = 0.26            # IGDB rate limit ~4 req/s — throttle to stay under
_last = [0.0]
# A caller doing a long sweep can ask for a gentler sustained pace than the ceiling
# (see igdb_mirror). Set, don't reassign MIN_INTERVAL: the floor stays the floor.
_pace = [0.0]
# How many 429s this process has taken — the mirror reads it to back its pace off.
_throttled = [0]


def set_pace(seconds):
    """Minimum seconds between requests for THIS process, on top of MIN_INTERVAL."""
    _pace[0] = max(0.0, float(seconds or 0))


def _throttle():
    gap = max(MIN_INTERVAL, _pace[0])
    dt = time.time() - _last[0]
    if dt < gap:
        time.sleep(gap - dt)
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


def query(endpoint, body, client_id, token, retries=4, reauth=None):
    """POST an APICalypse query; return the parsed JSON list. Retries 429/5xx.

    `reauth(client_id) -> new_token` is called ONCE on a 401. The OAuth token is cached
    against the TTL Twitch reported and reused until that clock runs out — 60 days —
    and nothing ever asked whether it still WORKS. A token invalidated server-side
    before it expires then fails every call until an expiry it has not reached: live
    2026-08-09, a full re-resolve died on its first request while the cached row still
    had 5,187,755 seconds on it and a fresh mint succeeded immediately. Expiry is a
    hint; the 401 is the authority.

    Once, deliberately. A second 401 carrying a brand-new token is a real credentials
    problem and must surface rather than spin."""
    url = "%s/%s" % (API, endpoint)
    delay = 1.0
    reauthed = False
    for attempt in range(retries + 1):
        _throttle()
        headers = {"Client-ID": client_id, "Authorization": "Bearer " + token,
                   "Accept": "application/json"}
        req = urllib.request.Request(url, data=body.encode(), headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 401 and reauth and not reauthed:
                fresh = reauth(client_id)
                if fresh:
                    token, reauthed = fresh, True
                    continue
                raise
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                # Honour Retry-After when the server sends one: a 429 that says how
                # long to wait is an instruction, and guessing over the top of it is
                # how a client turns a throttle into a ban. Jitter otherwise, so N
                # retries started together do not resynchronise on every round.
                ra = 0.0
                try:
                    ra = float(e.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    ra = 0.0
                time.sleep(ra if ra > 0 else delay * (1.0 + random.random() * 0.4))
                delay *= 2
                if e.code == 429:
                    _throttled[0] += 1
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(delay * (1.0 + random.random() * 0.4))
                delay *= 2
                continue
            raise
    # UNREACHABLE, and it used to be `return []`. Every path out of the loop above
    # returns or raises, so the only way here would be retries < 0 — and an empty list
    # is the one answer that must never be invented: the mirror reads it as "the source
    # is exhausted" and closes a sweep on it.
    raise RuntimeError("igdb.query: retries=%r left the loop with no answer" % retries)


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
    esrb, descs, all_ratings = age_ratings(g)
    if esrb:
        # its own kind, not parsed back out of "ESRB: M", because the library facet
        # groups on this value and a facet that has to string-split is a facet that
        # will one day split the wrong thing.
        out["esrb_rating"] = esrb
    if descs:
        out["content_descriptors"] = descs
    if all_ratings:
        out["age_ratings"] = all_ratings
    return out
