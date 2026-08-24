#!/usr/bin/env python3
"""ArcadeDB (adb.arcadeitalia.net) — arcade metadata AND media, no key, no quota.

Arcade is the one category where ludodex's strong providers are weakest. Steam has no
rows for a MAME set by definition. IGDB catalogues arcade titles as games but knows
nothing about the CABINET. ScreenScraper covers arcade, but its arcade media is the same
community pool as everything else. ArcadeDB is built on the official MAME files and does
nothing else — which is why Batocera lists it as a first-class scrape source alongside
ScreenScraper and IGDB.

WHAT MAKES IT UNUSUALLY EASY: it is keyed on the MAME SET NAME. `pacman`, `sf2ce`,
`mslug3` — the ROM filename IS the identifier, so there is no name matching, no year
tie-break, no acceptance gate to run. Either the set exists or it does not, and a lookup
that misses is a miss rather than a plausible-looking wrong answer. That makes it the
cheapest correct provider in the whole stack.

It also supplies things nothing else here does: input controls, button counts, screen
orientation (vertical cabinets are a real filter for anyone building a cab), the MAME
`history.dat` text, and a short-play VIDEO — free, unmetered, from their own host.

No key, no documented rate limit. That is not licence to hammer it: this is a
volunteer-run Italian arcade preservation site, so requests are paced under the same
per-service SETTING every other provider reads (`config.rate_limits("arcadedb")`), and
its default is deliberately slow.

The setting is shared; the CODE is not. `_pace()` below is one of seven byte-identical
private throttles across these provider modules, and zxinfo's is character-for-character
the same function. They belong in one shared limiter — which would be a new module, and
new modules are outside what this pass may add — so the duplication is named here rather
than described as sharing something it does not.
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
import config                       # noqa: E402

# https, not http: the http endpoint 301-redirects and urllib will not follow a redirect
# that changes scheme for some requests. Verified live 2026-08-17.
API = "https://adb.arcadeitalia.net/service_scraper.php"
DEFAULT_COOLDOWN_MS = 700          # polite by default; a volunteer host, no stated limit

# ArcadeDB media key -> our canonical media kind (media.KINDS). Its URLs are direct
# downloads from their own host and cost nothing, so taking all of them is free.
MEDIA_KIND = {
    "url_image_ingame": "screenshot",
    "url_image_title": "title_screen",
    "url_image_marquee": "marquee",
    "url_image_cabinet": "arcade_cabinet",
    "url_image_cpo": "arcade_controls",
    "url_image_control_panel": "arcade_controls",
    "url_image_flyer": "flyer",
    "url_image_pcb": "pcb",
    "url_video_shortplay": "video",
    "url_video_shortplay_hd": "video",
}


class ArcadeDBError(Exception):
    pass


def enabled():
    return config.get_bool("metadata_arcadedb_enabled", True)


def _cooldown_ms():
    try:
        return int(config.rate_limits("arcadedb").get("cooldown_ms")
                   or DEFAULT_COOLDOWN_MS)
    except Exception:                               # noqa: BLE001
        return DEFAULT_COOLDOWN_MS


_last = [0.0]


def _pace():
    gap = _cooldown_ms() / 1000.0
    wait = gap - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()


def query(set_name, timeout=30):
    """Look a MAME set up by name -> the raw record, or None.

    A miss is a RESULT. `pacman` exists, `pacman-that-never-was` does not, and the second
    one is a fact worth caching rather than an error to retry."""
    if not (set_name or "").strip():
        return None
    _pace()
    url = API + "?" + urllib.parse.urlencode(
        {"ajax": "query_mame", "game_name": set_name.strip().lower()})
    req = urllib.request.Request(url, headers={"User-Agent": "ludodex",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise ArcadeDBError("HTTP %s" % e.code)
    except Exception as e:                          # noqa: BLE001
        raise ArcadeDBError(str(e)[:120])
    rows = payload.get("result") or []
    if not rows:
        return None
    row = rows[0]
    # It answers with an empty shell for an unknown set rather than an empty list, so a
    # record with no title is a MISS — reporting it as a hit would attach a blank
    # identity to a real game.
    return row if (row.get("title") or row.get("game_name")) else None


def extract_metadata(row):
    """-> {ludodex attribute kind: value}. Absent fields are omitted, never blanked."""
    if not row:
        return {}
    out = {}
    for src, kind in (("year", "release_year"), ("manufacturer", "developers"),
                      ("genre", "genres")):
        v = (str(row.get(src) or "")).strip()
        if not v:
            continue
        out[kind] = [v] if kind in ("developers", "genres") else v
    hist = (row.get("history") or "").strip()
    if hist:
        out["description"] = hist
    try:
        players = int(row.get("players") or 0)
    except (TypeError, ValueError):
        players = 0
    if players == 1:
        out["game_modes"] = ["Single player"]
    elif players > 1:
        out["game_modes"] = ["Multiplayer"]
    lang = (row.get("languages") or "").strip()
    if lang:
        out["language"] = lang
    return out


def extract_media(row):
    """-> [{kind, type, url}]. Unknown media keys are ignored rather than guessed at."""
    out = []
    for key, kind in MEDIA_KIND.items():
        url = (row or {}).get(key)
        if url and str(url).startswith("http"):
            out.append({"kind": kind, "type": key, "url": url})
    return out


def cabinet_facts(row):
    """The things ONLY an arcade database knows: orientation, controls, buttons.

    Kept apart from the attribute map on purpose — these are not library facets, they are
    what someone building a cabinet or filtering for vertical shooters actually wants."""
    if not row:
        return {}
    keep = ("screen_orientation", "input_controls", "input_buttons", "buttons_colors",
            "nplayers", "rate", "cloneof", "emulator_name")
    return {k: row[k] for k in keep if row.get(k) not in (None, "")}


def main(argv):
    if not argv:
        print("usage: arcadedb.py <mame-set-name> [...]")
        return 2
    for name in argv:
        try:
            row = query(name)
        except ArcadeDBError as e:
            print("%-12s ERROR %s" % (name, e))
            continue
        if not row:
            print("%-12s (not found)" % name)
            continue
        print("%-12s %s" % (name, row.get("title")))
        for k, v in sorted(extract_metadata(row).items()):
            print("    %-14s %s" % (k, str(v)[:70]))
        for m in extract_media(row):
            print("    media %-16s %s" % (m["kind"], m["url"][:60]))
        for k, v in sorted(cabinet_facts(row).items()):
            print("    cab   %-14s %s" % (k, str(v)[:50]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
