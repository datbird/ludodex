#!/usr/bin/env python3
"""ScreenScraper.fr (api2) client — emulation metadata AND media in one scrape.

ScreenScraper is the emulation community's canonical database (what ES-DE,
Skraper, RetroArch and Batocera scrape). It is BOTH a metadata provider and a
media provider: a single jeuInfos.php call returns a game's metadata AND every
media URL for it — so one request per game gives us both, which matters on the
free tier's tight daily quota.

Auth: a software devid/devpassword (request at the screenscraper.fr forum) +
softname, plus the end-user's ssid/sspassword (which sets the tier/quota). The
engine reads the live `ssuser` quota block from every response, adapts its
parallelism to ssuser.maxthreads, paces under maxrequestspermin, and stops before
maxrequestsperday — so it works within whatever tier the user has and is
resumable across days. stdlib-only.
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config
import platmap                    # canonical platform tokens (system-fit checks)
import provider_rate              # shared pacing/timeout rules

API = "https://www.screenscraper.fr/api2/"

# Our platform label -> ScreenScraper systemeid. Authoritative list is
# systemesListe.php (cached at runtime); this covers our catalog's systems and is
# the fallback. See gist dollerbill/86162c5cb249d79ef01a9ad2c691d29d.
SYSTEM_ID = {
    "nes": 3, "snes": 4, "n64": 14, "gamecube": 13, "wii": 16, "wiiu": 18,
    "nintendo switch": 225, "gameboy": 9, "gameboy color": 10, "gba": 12,
    "nds": 15, "3ds": 17, "virtualboy": 11,
    "sega genesis": 1, "sega ms": 2, "gamegear": 21, "sega 32x": 19,
    "sega cd": 20, "sega saturn": 22, "dreamcast": 23,
    "psx": 57, "ps2": 58, "ps3": 59, "psvita": 62, "psp": 61,
    "atari 2600": 26, "atari 5200": 40, "atari 7800": 41, "jaguar": 27,
    "lynx": 28, "atari st": 42,
    "3do": 29, "colecovision": 48, "intellivision": 115, "supervision": 207,
    "neogeo": 142, "neogeopocketcolor": 82, "turbo gfx": 31, "tg16": 31,
    "amiga": 64, "amigacd32": 130, "zx spectrum": 76, "mame": 75, "arcade": 75,
    "apple2": 86, "jaguar cd": 171, "tubo duo": 114, "turbo duo": 114,
    "xbox360": 33, "xbox": 32,
}

# ScreenScraper media `type` -> our canonical media kind (media.py KINDS).
MEDIA_KIND = {
    "box-2D": "cover", "steamgrid": "cover",
    "box-2D-back": "box_back",
    "box-2D-side": "box_spine",
    "box-3D": "box_3d", "box-3D-side": "box_3d", "box-texture": "box_3d",
    "support-2D": "physical_media", "support-2D-back": "physical_media",
    "support-texture": "physical_media",
    "wheel": "logo", "wheel-hd": "logo", "wheel-carbon": "logo",
    "wheel-steel": "logo",
    "ss": "screenshot", "sstitle": "title_screen", "fanart": "background",
    "marquee": "marquee", "screenmarquee": "marquee",
    "screenmarquee-hd": "marquee", "screenmarqueesmall": "marquee",
    "bezel-16-9": "bezel", "bezel-4-3": "bezel",
    "flyer": "flyer", "maps": "map",
    "mixrbv1": "mix", "mixrbv2": "mix",
    "video": "video", "video-normalized": "video", "manuel": "manual",
    "icon": "icon",
    # --- observed live and previously falling through to `other` ---------------------
    # ScreenScraper's vocabulary is larger than its docs and varies by system, so the
    # safety net (unknown -> `other`, logged once) is the right default. But a type that
    # lands in `other` is invisible to every kind-scoped consumer: `box-scan` IS box art
    # and belongs in the cover pool, and `background` is self-evident. Leaving them in
    # `other` is why console entries could hold 77 ScreenScraper assets and still show a
    # cropped landscape as their cover.
    "box-scan": "cover",             # flat scan of the box front
    "background": "background",
    # Sega/console "picto" art — small pictograms used as list/menu icons.
    "pictoliste": "icon", "pictocouleur": "icon", "pictomonochrome": "icon",
    # A photographed physical figure/statue. Genuinely not one of the 23 kinds, so it
    # stays `other` DELIBERATELY — recorded here so it stops being re-investigated.
    "figurine": "other",
}

# raw types we've already warned about (avoid log spam)
_SEEN_UNKNOWN = set()


# The same table keyed by CANONICAL platform token. The catalog stores canonical
# labels ('ps1', 'genesis', 'gb'); SYSTEM_ID above is keyed on ScreenScraper-ish names
# ('psx', 'sega genesis', 'gameboy'), so a raw lookup missed 27 of the 56 non-PC games
# live — and a game whose system cannot be identified skips the per-system search
# entirely, leaving only the cross-system pass, which happily returns another system's
# record. Derived from SYSTEM_ID rather than hand-written, so the two cannot drift.
# Only where the canon is UNAMBIGUOUS. platmap.canon is a display/grouping token, not a
# system identity: it folds 'atari st', 'amiga', 'zx spectrum' and 'apple2' all into
# 'pc'. A canon several systems share cannot identify one, so it maps to nothing and is
# read as "no evidence" — the alternative is systeme_id('pc') answering 42 (Atari ST)
# and every PC game being matched, and refused, as an Atari ST release.
# ('mame'/'arcade' and 'tubo duo'/'turbo duo' share a canon but agree on the id, so they
# stay: the rule is one ID per canon, not one label.)
_CANON_SYSTEM_ID = {}
for _canon in {platmap.canon(_l) for _l in SYSTEM_ID}:
    _ids = {v for k, v in SYSTEM_ID.items() if platmap.canon(k) == _canon}
    if len(_ids) == 1:
        _CANON_SYSTEM_ID[_canon] = next(iter(_ids))


def systeme_id(platform):
    """ScreenScraper systemeid for one of our platform labels, or None.

    None means "ScreenScraper has no system for this", which is true for PC and must
    stay true — it is read as "no evidence", never as "refuse"."""
    p = (platform or "").strip().lower()
    return SYSTEM_ID.get(p) or _CANON_SYSTEM_ID.get(platmap.canon(p))


def jeu_system_id(jeu):
    """The systemeid a candidate declares for ITSELF, or None if it doesn't say."""
    s = (jeu or {}).get("systeme") or {}
    try:
        return int(s.get("id")) if s.get("id") not in (None, "") else None
    except (TypeError, ValueError):
        return None


def system_label(systemeid):
    """Our platform label for a ScreenScraper systemeid, or None. The reverse of
    `systeme_id`, so a match can report the system it actually landed on."""
    if not systemeid:
        return None
    for label, sid in SYSTEM_ID.items():
        if sid == systemeid:
            return label
    return None


def system_fits(platform, jeu):
    """False only when BOTH sides state a system and they disagree.

    ScreenScraper keeps one record per system, so a record for another system is a
    different RELEASE of the game — the 2008 PSN port of a 1998 PS1 title, the 2008 Wii
    Virtual Console edition of a 1993 Genesis one — carrying that release's art, dates
    and metadata. Refusing it is the discipline `game_era` already follows: an absent
    statement is not evidence and must never refuse anything, so a PC entry (no system)
    and a candidate that does not declare its system both pass."""
    want = systeme_id(platform)
    got = jeu_system_id(jeu)
    if not want or not got:
        return True
    return want == got


def _auth(creds):
    p = {"devid": creds["devid"], "devpassword": creds["devpassword"],
         "softname": creds.get("softname", "ludodex"), "output": "json"}
    if creds.get("ssid"):
        p["ssid"], p["sspassword"] = creds["ssid"], creds["sspassword"]
    return p


class SSError(Exception):
    def __init__(self, kind, msg=""):
        # badcreds | quota | rate | closed | error
        #
        # `rate` and `quota` were ONE kind and they are not one thing. HTTP 429 means
        # "slower"; HTTP 430 means "that is your day". Collapsing them made every
        # transient throttle look like exhaustion, so the caller had to spend an
        # ssuserInfos request to ask which it was — per throttled RESULT, on an API
        # that had just told us to slow down.
        self.kind = kind
        super().__init__("%s: %s" % (kind, msg))


# HOW MANY HTTP ATTEMPTS THIS PROCESS HAS ACTUALLY MADE, retries included.
#
# A caller that budgets "one request per id" against a hard daily quota is wrong by up
# to 3x on a flaky day, because _request retries and the caller never sees it. The
# budget has to be able to ask the transport what it really spent.
_attempts = 0
_attempts_lock = threading.Lock()


def _count_attempt():
    global _attempts
    with _attempts_lock:                 # the walk calls this from a thread pool
        _attempts += 1


def attempts_made():
    """Total HTTP attempts made so far, retries included. Monotonic."""
    with _attempts_lock:
        return _attempts


def _request(endpoint, creds, extra=None, timeout=90, attempts=3):
    """GET an api2 endpoint -> parsed JSON 'response'. Classifies failures.

    RETRIES ON TIMEOUT, and the timeout is generous on purpose. screenscraper.fr is a
    volunteer-run service that routinely answers in 30-40s under load — measured live at
    36.9s for a search that succeeded, and 40.6s for one that did not. At the old
    timeout=40 with no retry, that put every call on a coin flip, and a lost flip was
    SILENT: the caller catches, returns 0, and the game keeps no ScreenScraper art with
    nothing recorded to say it was ever attempted. Slow is not the same as absent.

    BOTH KINDS OF TIMEOUT, which is what this was missing. A read timeout arrives as
    socket.timeout; a CONNECT timeout arrives as URLError(reason=TimeoutError) — and
    _read used to swallow every URLError into SSError('closed'), so the branch below
    never ran for the commonest failure of a slow server. The retry was live code that
    could not fire: ss_scrape slept 60s and skipped the game, ss_mirror counted a closed
    strike and moved its cursor on. _read now re-raises timeout URLErrors untouched so
    the policy lives here, in one place."""
    params = _auth(creds)
    params.update(extra or {})
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "%s (ludodex)" % creds.get("softname", "ludodex")})
    last = None
    for attempt in range(max(1, attempts)):
        _count_attempt()                           # the budget must see the RETRIES too
        try:
            return _read(req, timeout)
        except (socket.timeout, TimeoutError) as e:
            last = e
        except urllib.error.URLError as e:
            if not provider_rate.is_timeout(e):
                raise
            last = e
        if attempt + 1 < attempts:
            # The backoff schedule is shared with thegamesdb (provider_rate.retry_delay);
            # only ever reached after a TIMEOUT, so it stays linear — the call that timed
            # out already spent 90s waiting and doubling on top of that is a stall.
            time.sleep(provider_rate.retry_delay(attempt))
    raise SSError("error", "timed out after %d attempts (%ss each): %s"
                  % (attempts, timeout, str(last)[:90]))


def _read(req, timeout):
    """One HTTP attempt; the classification the caller relies on stays here.

    ONE CLASSIFICATION POLICY, SHARED WITH thegamesdb._read:

      * a timeout, however urllib wraps it, goes back up to _request to be RETRIED —
        and that test is now provider_rate.is_timeout, one copy for both modules;
      * a body we cannot parse is an ERROR, never an absence;
      * every attempt, retries included, is charged to the budget.

    The STATUS-CODE table below stays ScreenScraper's own, and deliberately: 429 means
    "too fast" here and 430/431 mean "that is your allowance", while TheGamesDB folds
    403 and 429 into one monthly cap. A shared classifier would have to be handed both
    tables, which is what these two functions already are.
    """
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        kind = _classify(e.code, body)
        if e.code == 404 and kind == "notfound":
            return None                 # game simply not found
        raise SSError(kind, "HTTP %s %s" % (e.code, body[:120]))
    except urllib.error.URLError as e:
        # A timeout goes back UP for _request to retry. Turning it into SSError('closed')
        # here is what made _request's retry loop dead code for connect timeouts.
        if provider_rate.is_timeout(e):
            raise
        raise SSError("closed", str(e))
    # A valid api2 response is JSON with a "response" object — parse it FIRST, so the
    # plaintext-error heuristics below can't false-positive on a good result (every
    # response's ssuser block contains 'quotarefu' and the echoed commandRequested
    # URL contains 'screenscraper' → both "quota" and "scrape" are always present).
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and "response" in obj:
            return obj["response"]
    except ValueError:
        pass
    low = raw.lower()
    if "api closed" in low or "api ferm" in low or "maximum threads" in low:
        raise SSError("closed", raw[:120])
    if "quota" in low and "scrape" in low:
        raise SSError("quota", raw[:120])
    if "erreur de login" in low or ("invalid" in low and "dev" in low):
        raise SSError("badcreds", raw[:120])
    # AN UNREADABLE BODY IS NOT AN ABSENCE. This used to `return None`, which every
    # caller reads as "ScreenScraper does not have this game": ss_scrape wrote
    # status='notfound' and put the (game, system) in the permanently-done set, so ONE
    # HTML maintenance page removed that game from the worklist forever. We do not know
    # what this is, so the only honest answer is "ask again".
    raise SSError("error", "unreadable body (%d bytes): %s"
                  % (len(raw), raw[:90].replace("\n", " ")))


def _classify(code, body):
    low = (body or "").lower()
    if code in (401, 403) or "login" in low or "password" in low:
        return "badcreds"
    # 429 and 430 are DIFFERENT ANSWERS and were one kind. ScreenScraper's api2 uses 429
    # for "too fast, slow down" and 430 for "that is your allowance for today"; 431 is
    # the per-minute variant of the same refusal. A caller told "quota" for a 429 either
    # parks for half an hour with tens of thousands of requests still available, or
    # spends an extra ssuserInfos request to find out which one it really was.
    if code == 429:
        return "rate"
    if code in (430, 431) or "quota" in low:
        return "quota"
    if code in (423,) or "closed" in low or "ferm" in low or "member" in low:
        return "closed"
    if code == 404:
        return "notfound"
    return "error"


def ssuser(resp):
    """Extract the live quota block from any response (or {} )."""
    return (resp or {}).get("ssuser", {}) if isinstance(resp, dict) else {}


def quota_view(ss):
    """Human/struct view of the ssuser quota block."""
    def i(k, d=0):
        try:
            return int(ss.get(k, d))
        except (TypeError, ValueError):
            return d
    return {
        "maxthreads": max(1, i("maxthreads", 1)),
        "requeststoday": i("requeststoday"),
        "maxrequestsperday": i("maxrequestsperday"),
        "requestskotoday": i("requestskotoday"),
        "maxrequestskoperday": i("maxrequestskoperday"),
        "maxrequestspermin": i("maxrequestspermin"),
        "favregion": ss.get("favregion") or "us",
        "level": ss.get("niveau") or ss.get("level") or "?",
    }


def user_info(creds):
    """ssuserInfos.php -> quota_view (call once at startup to read the tier)."""
    resp = _request("ssuserInfos.php", creds)
    return quota_view(ssuser(resp))


def jeu_recherche(creds, recherche, systemeid=None, limit=8):
    """Name search — jeuRecherche.php, the endpoint ES-DE's 'search by name' uses
    (ludodex previously only did ROM-based jeu_infos). Returns a list of full
    candidate jeu records (metadata + media included), best-match first."""
    if not recherche:
        return []
    extra = {"recherche": recherche}
    if systemeid:
        extra["systemeid"] = systemeid
    resp = _request("jeuRecherche.php", creds, extra)
    if not resp:
        return []
    jeux = resp.get("jeux")
    if isinstance(jeux, dict):                 # a single-game response
        jeux = [jeux]
    # a no-result search returns a list with one empty dict — drop those
    return [j for j in (jeux or []) if isinstance(j, dict) and j.get("id")][:limit]


def jeu_name(jeu, region="us"):
    """Region-preferred display name of a jeu record."""
    return _pick((jeu or {}).get("noms"), region=region) or ""


def jeu_year(jeu, region="us"):
    """Release year (YYYY string) of a jeu record, or None."""
    d = _pick((jeu or {}).get("dates"), region=region)
    return d[:4] if d and len(d) >= 4 and d[:4].isdigit() else None


def jeu_infos(creds, systemeid=None, romnom=None, romtaille=None,
              crc=None, md5=None, sha1=None, gameid=None):
    """One game lookup. Returns (jeu dict | None, quota_view). Raises SSError on
    auth/quota/closed conditions so the caller can stop or back off."""
    extra = {}
    if gameid:
        extra["gameid"] = gameid
    if sha1:
        extra["sha1"] = sha1
    if md5:
        extra["md5"] = md5
    if crc:
        extra["crc"] = crc
    if systemeid:
        extra["systemeid"] = systemeid
    if romnom:
        extra["romnom"] = romnom
    if romtaille:
        extra["romtaille"] = romtaille
    resp = _request("jeuInfos.php", creds, extra)
    if not resp:
        return None, {}
    return resp.get("jeu"), quota_view(ssuser(resp))


# --- metadata + media extraction from a jeu record -------------------------- #
def _pick(items, key_region="region", region="us", text="text"):
    """Pick a region-preferred entry's text from a [{region,text}] list."""
    if not items:
        return None
    by = {(x.get(key_region) or "").lower(): x.get(text) for x in items
          if isinstance(x, dict)}
    for r in (region, "wor", "us", "eu", "ss", "jp"):
        if by.get(r):
            return by[r]
    return next((v for v in by.values() if v), None)


def extract_metadata(jeu, region="us"):
    """jeu -> {kind: value} attributes (genres/devs/publisher/players/rating/year)."""
    if not jeu:
        return {}
    out = {}
    name = _pick(jeu.get("noms"), region=region)
    if name:
        out["name"] = name
    syn = jeu.get("synopsis")
    if syn:
        s = _pick(syn, key_region="langue", region="en")
        if s:
            out["description"] = s
    genres = []
    for g in jeu.get("genres") or []:
        gn = _pick(g.get("noms"), key_region="langue", region="en")
        if gn:
            genres.append(gn)
    if genres:
        out["genres"] = genres
    if (jeu.get("developpeur") or {}).get("text"):
        out["developers"] = [jeu["developpeur"]["text"]]
    if (jeu.get("editeur") or {}).get("text"):
        out["publishers"] = [jeu["editeur"]["text"]]
    if (jeu.get("joueurs") or {}).get("text"):
        out["players"] = jeu["joueurs"]["text"]
    note = (jeu.get("note") or {}).get("text")
    if note:
        try:
            out["community_score"] = round(float(note) / 20.0 * 100)  # 0-20 -> 0-100
        except ValueError:
            pass
    date = _pick(jeu.get("dates"), region=region)
    if date and len(date) >= 4 and date[:4].isdigit():
        out["release_year"] = date[:4]
    return out


def extract_media(jeu):
    """jeu -> [{kind, type, url, region, format}]. Unknown types are classified
    as 'other' (never dropped) and the raw type is logged once."""
    out = []
    for m in (jeu or {}).get("medias") or []:
        raw = m.get("type")
        if not m.get("url"):
            continue
        kind = MEDIA_KIND.get(raw)
        if kind is None:
            kind = "other"
            if raw not in _SEEN_UNKNOWN:
                _SEEN_UNKNOWN.add(raw)
                print("screenscraper: unmapped media type %r -> 'other'" % raw,
                      file=sys.stderr)
        out.append({"kind": kind, "type": raw, "url": m["url"],
                    "region": m.get("region"), "format": m.get("format"),
                    "crc": m.get("crc")})
    return out


def media_url_with_auth(url, creds):
    """Append auth params so a ScreenScraper media URL is directly downloadable."""
    sep = "&" if "?" in url else "?"
    return url + sep + urllib.parse.urlencode(_auth(creds))
