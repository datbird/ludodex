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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config

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
}

# raw types we've already warned about (avoid log spam)
_SEEN_UNKNOWN = set()


def systeme_id(platform):
    return SYSTEM_ID.get((platform or "").strip().lower())


def _auth(creds):
    p = {"devid": creds["devid"], "devpassword": creds["devpassword"],
         "softname": creds.get("softname", "ludodex"), "output": "json"}
    if creds.get("ssid"):
        p["ssid"], p["sspassword"] = creds["ssid"], creds["sspassword"]
    return p


class SSError(Exception):
    def __init__(self, kind, msg=""):
        self.kind = kind                # badcreds | quota | closed | error
        super().__init__("%s: %s" % (kind, msg))


def _request(endpoint, creds, extra=None, timeout=40):
    """GET an api2 endpoint -> parsed JSON 'response'. Classifies failures."""
    params = _auth(creds)
    params.update(extra or {})
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "%s (ludodex)" % creds.get("softname", "ludodex")})
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
        raise SSError("closed", str(e))
    low = raw.lower()
    if "api closed" in low or "api ferm" in low or "maximum threads" in low:
        raise SSError("closed", raw[:120])
    if "quota" in low and "scrape" in low:
        raise SSError("quota", raw[:120])
    if "erreur de login" in low or "invalid" in low and "dev" in low:
        raise SSError("badcreds", raw[:120])
    try:
        return json.loads(raw).get("response", {})
    except ValueError:
        # non-JSON body that wasn't caught above -> treat as not found / noise
        return None


def _classify(code, body):
    low = (body or "").lower()
    if code in (401, 403) or "login" in low or "password" in low:
        return "badcreds"
    if code in (429, 430, 431) or "quota" in low:
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


def jeu_infos(creds, systemeid=None, romnom=None, romtaille=None,
              crc=None, md5=None, sha1=None):
    """One game lookup. Returns (jeu dict | None, quota_view). Raises SSError on
    auth/quota/closed conditions so the caller can stop or back off."""
    extra = {}
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
