#!/usr/bin/env python3
"""TheGamesDB (api.thegamesdb.net v2) client — metadata AND boxart in one call.

WHY THIS PROVIDER EXISTS AT ALL, given IGDB and ScreenScraper already cover more:
it is the other name this audience knows. ES-DE ships exactly two built-in scrapers,
ScreenScraper and TheGamesDB; Batocera lists four and this is one of them; RetroBat and
Skyscraper carry it too. A ludodex that offers ScreenScraper but not TheGamesDB reads as
missing something rather than as curated. It is a USER-CHOICE provider — selectable,
never a default, and ranked below IGDB and ScreenScraper because its coverage genuinely
is smaller.

THE BUDGET IS THE WHOLE DESIGN. A free key grants 1,000 requests PER MONTH. Not per day
— per month. ScreenScraper's free tier is roughly twenty thousand a day, so every habit
learned there is wrong here:

  * ONE CALL PER GAME IS A BUG. A 400-game library scraped naively is 400+ requests and
    the month is gone before the first sweep finishes. ByGameID, ByPlatformID and
    Games/Images all accept comma-delimited id lists, so this module batches and the
    callers never see a single-id path. Measured live: the server pages at 20 results
    regardless, so CHUNK is 20 — asking for 100 ids costs the same five requests as five
    chunks of twenty, and risks nothing.
  * BOXART COMES FREE. `include=boxart` rides along on a call already being made. A
    separate Games/Images pass for art we could have asked for is a doubled bill.
  * THE SERVER'S NUMBER IS THE TRUTH. Every response carries
    remaining_monthly_allowance, and /v1/API/Limit reports it for free (it does not count
    against the allowance — verified live). We never estimate what we have left when we
    can be told.

The configured limit is a CEILING WE IMPOSE, not our belief about the key. Those are
different things and conflating them fails in both directions: a user who buys a higher
Patreon tier and forgets to raise the setting would silently keep scraping at 1,000, and
a user whose key is smaller than the setting would blow through it. So the effective
budget is the smaller of the two, and when the server reports MORE than the configured
limit we say so rather than quietly leaving it unused.

Increases are bought, not requested: patreon.com/thegamesdb (Bronze $1 / Silver $5 /
Gold $15), and as of 2026-08 the tiers do not publish what limit each grants — ask
support@thegamesdb.net first.

Nothing fetched here is ever redistributed. This is a per-user key against a per-user
library; TheGamesDB data does not go into any shipped supplement.

stdlib only.
"""
import json
import os
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config                      # noqa: E402

API = "https://api.thegamesdb.net"
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
STATE_DB = os.path.join(DATA, "thegamesdb-state.sqlite")

# The server pages at 20 rows whatever we ask for (verified live 2026-08-16 with a
# 25-id request: count=20, next=page 2). Batching above the page size buys nothing and
# only lengthens the URL.
CHUNK = 20

# What a free key grants. Mirrored in config.SCHEMA as the default; kept here so the
# module is honest on its own if config is unavailable.
FREE_MONTHLY_LIMIT = 1000

# Held back so a bulk sweep cannot leave interactive lookups with nothing. 5% of the free
# tier — deliberately a fraction rather than a flat number, because a paid tier should get
# proportionally more headroom, not the same fifty.
RESERVE_FRACTION = 0.05
MIN_RESERVE = 10

# How long the cached allowance reading is trusted before we spend a free call to refresh
# it. Short, because /v1/API/Limit is free; not zero, because a sweep would otherwise
# re-ask between every chunk.
LIMIT_TTL = 300

# The fields worth asking for. Requesting them costs nothing extra — they ride on the
# same request — so the only reason to trim this list would be response size.
FIELDS = ("players,publishers,genres,overview,last_updated,rating,platform,coop,"
          "youtube,os,processor,ram,hdd,video,sound,alternates")

# TheGamesDB image `type` (+ `side` for boxart) -> our canonical media kind (media.KINDS).
# Anything unlisted becomes "other" rather than being dropped, same rule as every other
# provider here: an asset we do not recognise is still an asset.
MEDIA_KIND = {
    ("boxart", "front"): "cover",
    ("boxart", "back"): "box_back",
    ("fanart", None): "background",
    ("banner", None): "header",
    ("screenshot", None): "screenshot",
    ("clearlogo", None): "logo",
    ("titlescreen", None): "title_screen",
}


class TGDBError(Exception):
    def __init__(self, kind, msg=""):
        self.kind = kind               # badkey | quota | error
        super().__init__("%s: %s" % (kind, msg))


# --------------------------------------------------------------------------- #
#  credentials
# --------------------------------------------------------------------------- #
def api_key():
    """The user's own key. env > config. Empty string means "not configured"."""
    return (os.environ.get("TGDB_API_KEY", "").strip()
            or (config.get("thegamesdb_api_key") or "").strip())


def configured_limit():
    """The monthly ceiling WE impose. Defaults to what a free key grants."""
    raw = (config.get("thegamesdb_monthly_limit") or "").strip()
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = FREE_MONTHLY_LIMIT
    return n if n > 0 else FREE_MONTHLY_LIMIT


def reserve():
    """Requests held back for interactive use while a sweep runs."""
    raw = (config.get("thegamesdb_reserve") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    return max(MIN_RESERVE, int(configured_limit() * RESERVE_FRACTION))


# --------------------------------------------------------------------------- #
#  state — a cache of what the SERVER last told us, never our own tally
# --------------------------------------------------------------------------- #
def _state():
    con = sqlite3.connect(STATE_DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, val TEXT)")
    con.commit()
    return con


def _get(con, k, d=None):
    r = con.execute("SELECT val FROM state WHERE key=?", (k,)).fetchone()
    return r["val"] if r else d


def _put(con, k, v):
    con.execute("INSERT INTO state(key,val) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET val=excluded.val", (k, str(v)))


def _record_allowance(payload):
    """Every response carries the allowance. Believe it, and write it down."""
    if not isinstance(payload, dict):
        return
    rem = payload.get("remaining_monthly_allowance")
    if rem is None:
        return
    con = _state()
    try:
        _put(con, "remaining", int(rem))
        _put(con, "extra", int(payload.get("extra_allowance") or 0))
        _put(con, "refresh_secs", int(payload.get("allowance_refresh_timer") or 0))
        _put(con, "checked_at", int(time.time()))
        con.commit()
    finally:
        con.close()


def cached_allowance():
    """(remaining, extra, checked_at) as last reported, or (None, 0, 0)."""
    con = _state()
    try:
        rem = _get(con, "remaining")
        return ((int(rem) if rem is not None else None),
                int(_get(con, "extra", 0) or 0),
                int(_get(con, "checked_at", 0) or 0))
    finally:
        con.close()


# --------------------------------------------------------------------------- #
#  transport
# --------------------------------------------------------------------------- #
def _request(path, params=None, timeout=30, attempts=3, key=None):
    """GET a v1/v1.1 endpoint -> parsed JSON. Classifies failures; records allowance.

    Retries only on TIMEOUT. A 401 is a decision (bad key) and a 403 is the rate-limit
    cap; retrying either just spends the clock, and in the cap's case would spend the
    allowance too."""
    k = key if key is not None else api_key()
    if not k:
        raise TGDBError("badkey", "no TheGamesDB API key configured")
    q = dict(params or {})
    q["apikey"] = k
    url = "%s%s?%s" % (API, path, urllib.parse.urlencode(q))
    req = urllib.request.Request(url, headers={"User-Agent": "ludodex",
                                               "Accept": "application/json"})
    last = None
    for attempt in range(max(1, attempts)):
        try:
            return _read(req, timeout)
        except (socket.timeout, TimeoutError) as e:
            last = e
        except urllib.error.URLError as e:
            if not isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
                raise
            last = e
        if attempt + 1 < attempts:
            time.sleep(2 * (attempt + 1))
    raise TGDBError("error", "timed out after %d attempts: %s" % (attempts, str(last)[:90]))


def _read(req, timeout):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:                                   # noqa: BLE001
            pass
        if e.code == 401:
            raise TGDBError("badkey", _msg(body) or "unauthorised — check the API key")
        if e.code in (403, 429):
            raise TGDBError("quota", _msg(body) or "monthly allowance exhausted")
        raise TGDBError("error", "HTTP %s %s" % (e.code, _msg(body)))
    try:
        payload = json.loads(body)
    except ValueError:
        raise TGDBError("error", "non-JSON response: %s" % body[:120])
    _record_allowance(payload)
    code = payload.get("code")
    if code == 401:
        raise TGDBError("badkey", payload.get("status") or "unauthorised")
    if code in (403, 429):
        raise TGDBError("quota", payload.get("status") or "allowance exhausted")
    if code and int(code) >= 400:
        raise TGDBError("error", "%s %s" % (code, payload.get("status") or ""))
    return payload


def _msg(body):
    try:
        return (json.loads(body).get("status") or "")[:120]
    except Exception:                                       # noqa: BLE001
        return body[:120]


# --------------------------------------------------------------------------- #
#  budget
# --------------------------------------------------------------------------- #
def limit_status(force=False, key=None):
    """What we may spend. Asks the server when the cached reading is stale.

    /v1/API/Limit does not count against the allowance (their docs say so, and a live
    check confirmed it), so this is cheap to be right about."""
    rem, extra, checked = cached_allowance()
    stale = force or rem is None or (time.time() - checked) > LIMIT_TTL
    err = None
    if stale:
        try:
            p = _request("/v1/API/Limit", key=key)
            rem = int(p.get("remaining_monthly_allowance") or 0)
            extra = int(p.get("extra_allowance") or 0)
            checked = int(time.time())
        except TGDBError as e:
            err = str(e)
    cap = configured_limit()
    res = reserve()
    server = (rem + extra) if rem is not None else None
    # The ceiling and the truth are different claims. Take the smaller to decide what to
    # spend, and keep both so the caller can explain itself.
    allowed = cap if server is None else min(cap, server)
    return {
        "configured_limit": cap,
        "reserve": res,
        "remaining_reported": rem,
        "extra_allowance": extra,
        "checked_at": checked,
        "budget": max(0, allowed - res),
        # A key that grants more than we are configured to use is not an error, but it IS
        # something the user paid for and is not getting. Say it out loud.
        "underconfigured": bool(server is not None and server > cap),
        "error": err,
    }


def budget(key=None):
    """How many requests we are willing to make right now."""
    return limit_status(key=key)["budget"]


def _spend(n=1):
    """Optimistically decrement the cached reading so a loop between server readings
    cannot overrun. The next response corrects it."""
    con = _state()
    try:
        rem = _get(con, "remaining")
        if rem is not None:
            _put(con, "remaining", max(0, int(rem) - n))
            con.commit()
    finally:
        con.close()


def _chunks(seq, n=CHUNK):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# --------------------------------------------------------------------------- #
#  data
# --------------------------------------------------------------------------- #
def search(name, platform_ids=None, page=1, natural=False, key=None):
    """Search by name -> list of game dicts. Uses v1.1, which handles `mode` correctly.

    A miss is a RESULT, not a failure: an empty list means TheGamesDB does not have it,
    which is a fact worth caching, not an error to retry."""
    q = {"name": name, "fields": FIELDS, "include": "boxart,platform", "page": page}
    if platform_ids:
        q["filter[platform]"] = ",".join(str(x) for x in platform_ids)
    if natural:
        q["mode"] = "natural"
    _spend()
    p = _request("/v1.1/Games/ByGameName", q, key=key)
    return _games(p), _boxart(p)


def by_ids(ids, key=None):
    """Fetch many games by id, batched. -> (games, boxart_by_id).

    There is deliberately no by_id(): a single-id path is how a monthly budget gets
    spent one call at a time."""
    games, art = [], {}
    for chunk in _chunks(ids):
        _spend()
        p = _request("/v1/Games/ByGameID",
                     {"id": ",".join(str(x) for x in chunk),
                      "fields": FIELDS, "include": "boxart,platform"}, key=key)
        games.extend(_games(p))
        art.update(_boxart(p))
    return games, art


def images(game_ids, kinds=None, key=None):
    """Full image sets for games, batched. Only needed for art `include=boxart` omits —
    screenshots, clear logos, fanart, title screens."""
    out = {}
    for chunk in _chunks(game_ids):
        q = {"games_id": ",".join(str(x) for x in chunk)}
        if kinds:
            q["filter[type]"] = ",".join(kinds)
        _spend()
        p = _request("/v1/Games/Images", q, key=key)
        inc = p.get("data") or {}
        base = inc.get("base_url") or {}
        for gid, rows in (inc.get("images") or {}).items():
            out[str(gid)] = [_asset(r, base) for r in rows or []]
    return out


def by_hash(value, platform_ids=None, hash_type=None, key=None):
    """Look a ROM up by hash. Cheap identity when the file is an untouched dump."""
    q = {"hash": value}
    if platform_ids:
        q["filter[platform]"] = ",".join(str(x) for x in platform_ids)
    if hash_type:
        q["filter[type]"] = hash_type
    _spend()
    return _games(_request("/v1/Games/ByGameHash", q, key=key))


def by_unique_id(uid, platform_ids=None, key=None):
    """Look a game up by its external identifier — a disc serial, typically.

    Worth having because a serial survives what a hash does not: a CHD or RVZ is
    re-encoded, so its checksum matches nothing in any dump database, while the serial
    printed on the disc is still readable inside the image."""
    q = {"uid": uid}
    if platform_ids:
        q["filter[platform]"] = ",".join(str(x) for x in platform_ids)
    _spend()
    return _games(_request("/v1/Games/ByGameUniqueID", q, key=key))


def platforms(key=None):
    """The full platform list — one request, and it changes about never, so callers
    should cache it rather than re-ask per sweep."""
    _spend()
    p = _request("/v1/Platforms", {"fields": "icon,console,controller,developer,"
                                             "manufacturer,media,cpu,memory,graphics,"
                                             "sound,maxcontrollers,display,overview,"
                                             "youtube"}, key=key)
    data = (p.get("data") or {}).get("platforms") or {}
    return list(data.values()) if isinstance(data, dict) else list(data)


def by_platform(platform_ids, page=1, key=None):
    """Every game on a platform. Paginated at 20 by the server, so a whole platform is
    a long walk — the caller decides how far to go, this only fetches a page."""
    _spend()
    p = _request("/v1/Games/ByPlatformID",
                 {"id": ",".join(str(x) for x in platform_ids), "fields": FIELDS,
                  "include": "boxart,platform", "page": page}, key=key)
    return _games(p), _boxart(p), (p.get("pages") or {})


def videos(game_ids, key=None):
    """Trailers/clips, batched. Separate from images because the API keeps them apart —
    and unlike boxart they do NOT ride along on a metadata call."""
    out = {}
    for chunk in _chunks(game_ids):
        _spend()
        p = _request("/v1/Games/Videos",
                     {"games_id": ",".join(str(x) for x in chunk)}, key=key)
        d = p.get("data") or {}
        base = d.get("base_url") or ""
        for gid, rows in (d.get("videos") or {}).items():
            out[str(gid)] = [{"type": (r.get("type") or ""),
                              "url": (base + (r.get("filename") or ""))
                              if r.get("filename") else ""}
                             for r in rows or []]
    return out


# The five lookup tables. A game row carries IDS — `genres: [15]`, `developers: [7574,
# 7979]`, `region_id: 2` — so without these the pull is unreadable. They are small,
# essentially static, and cost five requests ONCE; caching them is not an optimisation,
# it is the difference between five requests and five per sweep.
VOCAB_ENDPOINTS = {
    "genres": ("/v1/Genres", "genres"),
    "developers": ("/v1/Developers", "developers"),
    "publishers": ("/v1/Publishers", "publishers"),
    "regions": ("/v1/Regions", "regions"),
    "countries": ("/v1/Countries", "countries"),
}
VOCAB_TTL = 90 * 24 * 3600          # re-fetch quarterly; new studios do get added


def vocabulary(force=False, key=None):
    """{table: {id: name}} for all five lookup tables, cached in the state db."""
    con = _state()
    try:
        raw = _get(con, "vocab")
        age = time.time() - float(_get(con, "vocab_at", 0) or 0)
        if raw and not force and age < VOCAB_TTL:
            try:
                return json.loads(raw)
            except ValueError:
                pass
    finally:
        con.close()

    out = {}
    for name, (path, root) in VOCAB_ENDPOINTS.items():
        _spend()
        p = _request(path, key=key)
        data = (p.get("data") or {}).get(root) or {}
        if isinstance(data, dict):
            out[name] = {str(k): (v.get("name") if isinstance(v, dict) else v)
                         for k, v in data.items()}
        else:
            out[name] = {str(v.get("id")): v.get("name") for v in data}
    con = _state()
    try:
        _put(con, "vocab", json.dumps(out))
        _put(con, "vocab_at", int(time.time()))
        con.commit()
    finally:
        con.close()
    return out


def resolve_names(row, vocab):
    """A game row's id lists -> names. Unknown ids are DROPPED, not rendered as ids:
    a genre facet reading "15" is worse than one missing an entry."""
    def names(field, table):
        t = vocab.get(table) or {}
        return [t[str(i)] for i in (row.get(field) or []) if str(i) in t]
    return {"genres": names("genres", "genres"),
            "developers": names("developers", "developers"),
            "publishers": names("publishers", "publishers")}


def updates(last_edit_id=0, time_minutes=None, key=None):
    """The edit log, for incremental refresh instead of re-scraping.

    This is the endpoint that makes a monthly budget survivable across months: after the
    first pass, only what CHANGED costs anything."""
    q = {}
    if time_minutes:
        q["time"] = int(time_minutes)
    else:
        q["last_edit_id"] = int(last_edit_id)
    _spend()
    p = _request("/v1/Games/Updates", q, key=key)
    return (p.get("data") or {}).get("updates") or []


# --------------------------------------------------------------------------- #
#  shaping
# --------------------------------------------------------------------------- #
def _games(payload):
    d = payload.get("data") or {}
    g = d.get("games")
    if isinstance(g, dict):
        return list(g.values())
    return list(g or [])


def _boxart(payload):
    """`include=boxart` -> {game_id: [asset, ...]}, already URL-resolved."""
    inc = (payload.get("include") or {}).get("boxart") or {}
    base = inc.get("base_url") or {}
    out = {}
    for gid, rows in (inc.get("data") or {}).items():
        out[str(gid)] = [_asset(r, base) for r in rows or []]
    return out


def _asset(row, base):
    """One image row -> a media reference in our own vocabulary.

    Resolution is carried through when the server states it, because the deterministic
    picker ranks on shape and size and an unmeasured asset is treated as unknown rather
    than as small."""
    typ = (row.get("type") or "").lower()
    side = (row.get("side") or None)
    kind = MEDIA_KIND.get((typ, side)) or MEDIA_KIND.get((typ, None)) or "other"
    w = h = None
    res = row.get("resolution") or ""
    if "x" in res:
        a, _, b = res.partition("x")
        if a.isdigit() and b.isdigit():
            w, h = int(a), int(b)
    fn = row.get("filename") or ""
    return {"kind": kind, "type": typ, "side": side, "filename": fn,
            "width": w, "height": h,
            "url": (base.get("original") or "") + fn if fn else "",
            "thumb": (base.get("thumb") or "") + fn if fn else ""}


# --------------------------------------------------------------------------- #
#  cli
# --------------------------------------------------------------------------- #
def main(argv):
    if "--status" in argv or not argv:
        s = limit_status(force="--refresh" in argv)
        print("thegamesdb: key %s" % ("configured" if api_key() else "NOT CONFIGURED"))
        print("  configured monthly limit : %s" % s["configured_limit"])
        print("  reserve                  : %s" % s["reserve"])
        print("  remaining (server)       : %s" % s["remaining_reported"])
        print("  extra allowance          : %s" % s["extra_allowance"])
        print("  spendable now            : %s" % s["budget"])
        if s["underconfigured"]:
            print("  NOTE: your key grants more than the configured limit — raise "
                  "thegamesdb_monthly_limit to use what you are paying for.")
        if s["error"]:
            print("  error: %s" % s["error"])
        return 0
    if argv[0] == "--search" and len(argv) > 1:
        games, art = search(" ".join(argv[1:]))
        for g in games[:10]:
            print("%8s  %-44s platform=%s  %s" % (
                g.get("id"), (g.get("game_title") or "")[:44], g.get("platform"),
                (g.get("release_date") or "")[:10]))
        print("(%d results, %d with boxart)" % (len(games), len(art)))
        return 0
    print(__doc__.strip().splitlines()[0])
    print("usage: thegamesdb.py [--status [--refresh]] | --search <name>")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
