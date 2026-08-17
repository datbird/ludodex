#!/usr/bin/env python3
"""MobyGames API v1 — 332,414 games, and the cheapest id space in the stack.

WHY IT BEATS THE ALTERNATIVES ON IDS. TheGamesDB rations 12,000 requests a MONTH for
~123,000 games. MobyGames rations 720 an HOUR for 332,414 — but `/games` pages 100 at a
time, so the whole id space is 3,325 requests, which is FOUR AND A HALF HOURS. Two and a
half times the catalogue, in an afternoon instead of a month, for $9.99 instead of $29.

THE LIMIT IS PER HOUR, NOT PER MONTH, AND THAT CHANGES THE ENGINEERING. The only cost of
a request is time, so the client does not count a budget — it goes AS FAST AS THE HOUR
ALLOWS. Their docs give 720/hour ("one every five seconds") with 1/sec as a burst
ceiling, and both numbers matter: a 3,325-page walk converges on 720/hour whatever it
does, but a 100-game enrichment fits entirely inside the hour and has no reason to crawl.
So `_pace` bursts at 1/sec while the ROLLING HOUR has headroom past a small reserve, and
settles to even 5s spacing once it does not. Long jobs are unchanged; short ones finish
five times sooner.

THE WINDOW IS PERSISTED, not counted in memory. A four-hour walk restarts, and an
in-memory counter comes back believing it has spent nothing — which is exactly how a
paced client becomes an unpaced one after a container restart, with the server the only
thing that notices.

FORMAT=NORMAL IS FREE. `format=id`, `brief` and `normal` all page at 100 and all cost one
request, so asking for ids alone is leaving the genres, platforms, release dates,
alternate titles, moby_score and sample art on the table for nothing. The walk takes
`normal` and the default says so.

PRODUCT CODES ARE THE EXPENSIVE THING, and they are gated OFF because of it. The 260,337
product codes — disc serials, the identifier that survives CHD and RVZ conversion — live
at /games/{id}/platforms/{pid}, ONE REQUEST PER (game, platform) PAIR with no batching.
That is roughly 430,000 requests, about 25 DAYS of continuous polling. A quarter-hour job
and a month-long job must not share a switch, so `mobygames_product_codes` defaults to
off and the walk never turns it on for you.

Non-commercial terms on every tier: this data can never end up in anything sold.
"""
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config                       # noqa: E402

DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
API = "https://api.mobygames.com/v1"

# Their documented non-commercial ceiling. Legacy keys get 360; a commercial agreement
# gets whatever it gets, which is why this is a setting rather than a constant.
DEFAULT_HOURLY = 720
# Their 429 says "wait at least 1 seconds between requests" — the burst floor, never
# crossed even if someone sets an implausibly high hourly limit.
MIN_INTERVAL_MS = 1000
PAGE = 100                         # `limit`: default 100, max 100, documented

FORMATS = ("id", "brief", "normal")

# MobyGames cover `scan_of` -> our canonical media kind. Their cover groups are per
# COUNTRY, so the same game legitimately has many fronts; media_index dedupes by URL.
COVER_KIND = {
    "front cover": "cover", "back cover": "box_back", "media": "physical_media",
    "spine/sides": "box_spine", "inside cover": "other", "manual": "manual",
}


class MobyError(Exception):
    def __init__(self, kind, msg=""):
        self.kind = kind               # badkey | quota | notfound | error
        super().__init__("%s: %s" % (kind, msg))


def api_key():
    return (os.environ.get("MOBYGAMES_API_KEY", "").strip()
            or (config.get("mobygames_api_key") or "").strip())


def enabled():
    return config.get_bool("metadata_mobygames_enabled", False)


def hourly_limit():
    try:
        n = int((config.get("mobygames_hourly_limit") or "").strip())
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else DEFAULT_HOURLY


def _interval():
    """The SUSTAINED spacing implied by the hourly limit — the floor a long job settles
    into once its burst allowance is gone."""
    return 3600.0 / max(1, hourly_limit())


def _burst_floor():
    """The fastest we may ever go. Their 429: 'wait at least 1 seconds between requests'."""
    try:
        ms = int((config.get("mobygames_min_interval_ms") or "").strip())
    except (TypeError, ValueError):
        ms = MIN_INTERVAL_MS
    return max(ms, 0) / 1000.0


def _reserve():
    """Requests held out of the burst allowance so a long job cannot spend the whole
    hour in twelve minutes and leave an interactive lookup waiting forty-eight."""
    try:
        n = int((config.get("mobygames_burst_reserve") or "").strip())
    except (TypeError, ValueError):
        n = -1
    return n if n >= 0 else max(20, int(hourly_limit() * 0.1))


STATE_DB = os.path.join(DATA, "mobygames-state.sqlite")


def _state():
    con = sqlite3.connect(STATE_DB, timeout=30)
    con.execute("CREATE TABLE IF NOT EXISTS req(at REAL)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_req_at ON req(at)")
    return con


def _spend_window():
    """(requests in the last hour, oldest timestamp still inside it).

    PERSISTED, because a four-hour walk restarts. An in-memory counter resets to zero on
    every relaunch, which is precisely how a paced client turns into an unpaced one after
    a container restart — and the server only tells you by starting to 429."""
    now = time.time()
    con = _state()
    try:
        con.execute("DELETE FROM req WHERE at < ?", (now - 3600.0,))
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM req").fetchone()[0]
        oldest = con.execute("SELECT MIN(at) FROM req").fetchone()[0]
        return n, oldest
    finally:
        con.close()


def _note_request():
    con = _state()
    try:
        con.execute("INSERT INTO req(at) VALUES(?)", (time.time(),))
        con.commit()
    finally:
        con.close()


_last = [0.0]


def _pace():
    """Go as fast as the hour allows, and no faster.

    THIS IS THE WHOLE POINT OF THE HOURLY MODEL. Even pacing at 5s is correct for a
    3,325-page walk, but it is five times too slow for a 100-game enrichment that would
    fit inside the burst allowance with room to spare. So: burst at 1/sec while the
    rolling hour has headroom beyond the reserve, and fall back to even spacing once it
    does not. A long job converges on 720/hour either way; a short one finishes five
    times sooner.

    The window is PERSISTED rather than counted in memory, because a walk that restarts
    would otherwise believe it had spent nothing."""
    used, oldest = _spend_window()
    budget = max(1, hourly_limit() - _reserve())
    if used < budget:
        gap = _burst_floor()                     # headroom: go at the burst ceiling
    elif oldest:
        # No headroom. Wait for the oldest request to age out of the rolling hour, which
        # is exactly when one more becomes affordable — never a blind sleep.
        gap = max(_burst_floor(), (oldest + 3600.0) - _last[0])
    else:
        gap = _interval()
    wait = gap - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    _note_request()


def _get(path, params=None, timeout=60, attempts=3):
    """GET an endpoint -> parsed JSON. Paced, classified, retried only where sensible."""
    k = api_key()
    if not k:
        raise MobyError("badkey", "no MobyGames API key configured")
    q = dict(params or {})
    # Their docs are explicit: every argument must be urlencoded, INCLUDING the key —
    # an unencoded '+' in a key silently invalidates it.
    q["api_key"] = k
    url = "%s%s?%s" % (API, path, urllib.parse.urlencode(q, doseq=True))
    req = urllib.request.Request(url, headers={"User-Agent": "ludodex",
                                               "Accept": "application/json"})
    for attempt in range(max(1, attempts)):
        _pace()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:                       # noqa: BLE001
                pass
            if e.code == 401:
                raise MobyError("badkey", _msg(body) or "unauthorised")
            if e.code == 404:
                return None                          # a miss is a RESULT
            if e.code == 429:
                # Backing off and trying again is the correct response to "too fast" —
                # this is the one error worth retrying, and the wait grows each time.
                if attempt + 1 < attempts:
                    time.sleep(_interval() * (attempt + 2))
                    continue
                raise MobyError("quota", _msg(body) or "rate limited")
            raise MobyError("error", "HTTP %s %s" % (e.code, _msg(body)))
        except Exception as e:                      # noqa: BLE001
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
                continue
            raise MobyError("error", str(e)[:120])
    raise MobyError("error", "exhausted attempts")


def _msg(body):
    try:
        d = json.loads(body)
        return ("%s %s" % (d.get("error") or "", d.get("message") or ""))[:140].strip()
    except Exception:                               # noqa: BLE001
        return body[:120]


# --------------------------------------------------------------------------- #
#  vocabularies — small, static, fetched once
# --------------------------------------------------------------------------- #
def platforms():
    return ((_get("/platforms") or {}).get("platforms")) or []


def genres():
    return ((_get("/genres") or {}).get("genres")) or []


def groups(limit=PAGE, offset=0):
    return ((_get("/groups", {"limit": limit, "offset": offset}) or {})
            .get("groups")) or []


# --------------------------------------------------------------------------- #
#  games
# --------------------------------------------------------------------------- #
def games(offset=0, limit=PAGE, fmt="normal", platform=None, genre=None,
          group=None, title=None, ids=None):
    """One page of games. -> list (of ints when fmt='id', dicts otherwise).

    `ids` wins over every other filter — their docs say so explicitly — so it is passed
    alone rather than alongside filters that would be silently ignored."""
    if fmt not in FORMATS:
        raise ValueError("format must be one of %s" % (FORMATS,))
    if ids:
        q = {"id": list(ids), "format": fmt}
    else:
        q = {"limit": min(int(limit), PAGE), "offset": int(offset), "format": fmt}
        if platform:
            q["platform"] = list(platform) if isinstance(platform, (list, tuple)) \
                else [platform]
        if genre:
            q["genre"] = list(genre) if isinstance(genre, (list, tuple)) else [genre]
        if group:
            q["group"] = list(group) if isinstance(group, (list, tuple)) else [group]
        if title:
            # Their 422 is explicit: the title filter must be <= 128 characters.
            q["title"] = title[:128]
    return ((_get("/games", q) or {}).get("games")) or []


def game(game_id, fmt="normal"):
    """One game, or None. /games/{id} is their own documented shorthand for ?id=."""
    d = _get("/games/%s" % game_id, {"format": fmt})
    if not d:
        return None
    return d.get("games", [d])[0] if isinstance(d.get("games"), list) else d


def recent(age=21, limit=PAGE, offset=0, fmt="id"):
    """Games modified in the last `age` days (their max is 21).

    This is what makes the walk a one-off: after the first pass, staying current is a
    handful of requests a month rather than another four hours."""
    return ((_get("/games/recent", {"age": min(int(age), 21), "limit": limit,
                                    "offset": offset, "format": fmt}) or {})
            .get("games")) or []


def game_platform(game_id, platform_id):
    """The per-platform release record — attributes, ratings, releases, PRODUCT CODES.

    ONE REQUEST PER (game, platform) PAIR, with no batching anywhere in their API. At
    720/hour that is ~25 days for the whole catalogue, which is why nothing calls this in
    bulk unless `mobygames_product_codes` is switched on deliberately."""
    return _get("/games/%s/platforms/%s" % (game_id, platform_id))


def product_codes(game_id, platform_id):
    """-> [{code, type, company, country}] flattened out of the release records."""
    rec = game_platform(game_id, platform_id) or {}
    out = []
    for rel in (rec.get("releases") or []):
        for pc in (rel.get("product_codes") or []):
            code = (pc.get("product_code") if isinstance(pc, dict) else pc) or ""
            code = str(code).strip()
            if not code:
                continue
            out.append({"code": code,
                        "type": (pc.get("product_code_type") if isinstance(pc, dict)
                                 else "") or "",
                        "countries": rel.get("countries") or []})
    return out


# MEASURED 2026-08-17 WITH A REAL KEY: the UNFILTERED /games list stops returning rows
# between offset 205,000 (100 rows, first id 264310) and 210,000 (zero rows). There is NO
# error and NO 429 — just an empty list, which is indistinguishable from the end of the
# catalogue. MobyGames holds 332,414 games, so a global walk would quietly stop ~124,000
# short and report success. That is the fail-open shape in a new costume, and the reason
# `walk_ids` refuses rather than trusting the empty page.
#
# A PLATFORM-FILTERED window does not hit it (platform=3 at offset 40,000 answers fine),
# so the real walk goes per platform. Not merely a workaround — it is the shape ludodex
# wants anyway: it yields (game, platform) pairs directly instead of a game carrying a
# platform list to explode afterwards.
GLOBAL_OFFSET_CEILING = 205000


def walk_platform(platform_id, fmt="normal", start_offset=0, max_pages=None):
    """Page ONE platform. Yields (offset, rows) so a caller can checkpoint.

    A zero-length page ends THIS WINDOW. It does not mean the catalogue is finished, and
    conflating the two is exactly how a global walk lies about being done."""
    off, pages = int(start_offset), 0
    while True:
        rows = games(offset=off, limit=PAGE, fmt=fmt, platform=platform_id)
        if not rows:
            return
        yield off, rows
        off += len(rows)
        pages += 1
        if len(rows) < PAGE or (max_pages and pages >= max_pages):
            return


def walk_ids(fmt="normal", platform=None, start_offset=0, max_pages=None,
             progress=None):
    """Page a filtered window. Yields (offset, rows) so a 4-hour job can be resumed.

    UNFILTERED PAGING IS REFUSED past the measured ceiling rather than allowed to return
    an empty page and be read as 'done'. For the whole catalogue use walk_all()."""
    off, pages = int(start_offset), 0
    while True:
        if platform is None and off > GLOBAL_OFFSET_CEILING:
            raise MobyError("error",
                            "unfiltered paging silently returns nothing past offset "
                            "~%d; MobyGames holds 332,414 games, so this walk would "
                            "stop short and look finished. Use walk_all()."
                            % GLOBAL_OFFSET_CEILING)
        rows = games(offset=off, limit=PAGE, fmt=fmt, platform=platform)
        if not rows:
            return
        yield off, rows
        if progress:
            progress(off, len(rows))
        off += len(rows)
        pages += 1
        if len(rows) < PAGE or (max_pages and pages >= max_pages):
            return


def walk_all(fmt="normal", platform_ids=None, progress=None, max_requests=None):
    """The whole catalogue, platform by platform. Yields (platform_id, offset, rows).

    Deduping is the CALLER's job, deliberately: a game on three platforms comes back
    three times, and for ludodex those are three ENTRIES rather than one game to
    collapse. Discarding that here would throw away the fact the entry model rests on."""
    plats = platform_ids or [p.get("platform_id") for p in platforms()]
    spent = 0
    for pid in plats:
        for off, rows in walk_platform(pid, fmt=fmt):
            spent += 1
            yield pid, off, rows
            if progress:
                progress(pid, off, len(rows))
            if max_requests and spent >= max_requests:
                return


# --------------------------------------------------------------------------- #
#  shaping
# --------------------------------------------------------------------------- #
def extract_metadata(rec):
    """A `format=normal` game -> {ludodex attribute kind: value}.

    Their genre list is FLAT but CATEGORISED, and the categories are the useful part:
    'Basic Genres' really are genres, while 'Perspective' is IGDB's player_perspectives
    and 'Setting'/'Narrative Theme' are its themes. Dumping all of them into `genres`
    would bury 'Adventure' among '1st-person' and 'Sci-Fi / Futuristic'."""
    if not rec:
        return {}
    out = {}
    title = (rec.get("title") or "").strip()
    if title:
        out["name"] = title
    desc = (rec.get("description") or "").strip()
    if desc:
        out["description"] = desc
    genres_, themes, persp = [], [], []
    for g in (rec.get("genres") or []):
        cat = (g.get("genre_category") or "").strip().lower()
        nm = (g.get("genre_name") or "").strip()
        if not nm:
            continue
        if cat == "perspective":
            persp.append(nm)
        elif cat in ("setting", "narrative theme/topic"):
            themes.append(nm)
        elif cat in ("basic genres", "gameplay", "sports themes", "vehicular themes",
                     "educational categories"):
            genres_.append(nm)
        # 'Other Attributes' (Licensed Title, …) is deliberately dropped: it is neither
        # a genre nor a theme, and inventing a facet for it here would be a second
        # opinion about a vocabulary we do not own.
    if genres_:
        out["genres"] = genres_
    if themes:
        out["themes"] = themes
    if persp:
        out["player_perspectives"] = persp
    score = rec.get("moby_score")
    if score not in (None, ""):
        try:
            # Moby Score is 0-10; every other community_score here is 0-100.
            out["community_score"] = round(float(score) * 10)
        except (TypeError, ValueError):
            pass
    plats = rec.get("platforms") or []
    years = [str(p.get("first_release_date") or "")[:4] for p in plats]
    years = sorted(y for y in years if len(y) == 4 and y.isdigit())
    if years:
        out["release_year"] = years[0]              # the FIRST release, across platforms
    return out


def extract_media(rec):
    """Sample cover + screenshots from a `format=normal` record.

    They arrive WITH width and height, which matters: the deterministic picker ranks on
    measured shape and size, and an asset that has to be fetched before it can be judged
    is an asset that gets judged late or not at all."""
    out = []
    cov = rec.get("sample_cover") or {}
    if cov.get("image"):
        out.append({"kind": "cover", "type": "sample_cover", "url": cov["image"],
                    "width": cov.get("width"), "height": cov.get("height")})
    for s in (rec.get("sample_screenshots") or []):
        if s.get("image"):
            out.append({"kind": "screenshot", "type": "sample_screenshot",
                        "url": s["image"], "width": s.get("width"),
                        "height": s.get("height"), "caption": s.get("caption") or ""})
    return out


def extract_covers(cover_groups):
    """The FULL cover set from /games/{id}/platforms/{pid}/covers, one extra request."""
    out = []
    for grp in (cover_groups or []):
        countries = grp.get("countries") or []
        for c in (grp.get("covers") or []):
            if not c.get("image"):
                continue
            kind = COVER_KIND.get((c.get("scan_of") or "").strip().lower(), "other")
            out.append({"kind": kind, "type": c.get("scan_of") or "", "url": c["image"],
                        "width": c.get("width"), "height": c.get("height"),
                        "countries": countries})
    return out


def status():
    used, _oldest = _spend_window()
    return {"configured": bool(api_key()), "enabled": enabled(),
            "hourly_limit": hourly_limit(),
            "used_last_hour": used,
            "burst_reserve": _reserve(),
            "burst_headroom": max(0, hourly_limit() - _reserve() - used),
            "seconds_between_requests": round(
                _burst_floor() if used < hourly_limit() - _reserve() else _interval(), 2),
            "sustained_seconds": round(_interval(), 2),
            "pages_for_full_walk": 3325,
            "hours_for_full_walk": round(3325 * _interval() / 3600.0, 2),
            "product_codes": config.get_bool("mobygames_product_codes", False)}


def main(argv):
    if not argv or argv[0] == "--status":
        s = status()
        print("mobygames: key %s" % ("configured" if s["configured"] else "NOT SET"))
        for k in ("enabled", "hourly_limit", "used_last_hour", "burst_headroom",
                  "seconds_between_requests", "sustained_seconds",
                  "pages_for_full_walk", "hours_for_full_walk", "product_codes"):
            print("  %-26s %s" % (k, s[k]))
        return 0
    if argv[0] == "--platforms":
        for p in platforms():
            print("%5s  %s" % (p.get("platform_id"), p.get("platform_name")))
        return 0
    if argv[0] == "--ids":
        n = int(argv[1]) if len(argv) > 1 else 100
        print(games(offset=0, limit=min(n, PAGE), fmt="id"))
        return 0
    rec = game(argv[0]) if argv[0].isdigit() else (games(title=" ".join(argv))[:1] or
                                                   [None])[0]
    if not rec:
        print("(not found)")
        return 1
    print(rec.get("title"))
    for k, v in sorted(extract_metadata(rec).items()):
        print("    %-20s %s" % (k, str(v)[:70]))
    for m in extract_media(rec):
        print("    media %-12s %sx%s %s" % (m["kind"], m.get("width"), m.get("height"),
                                            (m["url"] or "")[:52]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
