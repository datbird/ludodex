#!/usr/bin/env python3
"""ZXInfo (api.zxinfo.dk) — the ZX Spectrum archive. No key, no quota.

Narrow and deep, which is exactly the shape ludodex is missing. Measured on this
deployment's ScreenScraper catalog: 13,859 hashed ZX Spectrum games, of which the free
TheGamesDB map covers ZERO — and TheGamesDB itself is thin on 8-bit computers generally.
The whole Spectrum corner of a big emulation library is effectively unserved by the
providers already wired in.

ZXInfo is the World of Spectrum successor and it knows things a general database does
not: the exact machine variant (48K vs 128K vs +2), the loading screen as a distinct
asset, the original shop price, whether the title is legally available, which compilations
it appeared in, and authorship down to individual people with their roles and countries.

NOTE ON THE ENDPOINT: Skyscraper's documentation points at an older path that now 404s.
The live API is `/v3` — verified 2026-08-17 against `/v3/games/0002259` (Head over Heels)
and `/v3/search`. Recorded here because a dead endpoint in someone else's docs is exactly
the kind of thing that gets re-diagnosed from scratch a year later.

No key and no stated rate limit, so requests are paced under the same per-service SETTING
every other provider reads (`config.rate_limits("zxinfo")`) rather than being fired as
fast as they will go. The setting is shared; the CODE is not — `_pace()` below is
character-for-character arcadedb's, one of seven such private throttles, and they belong
in one shared limiter. See the note in arcadedb.py.
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
import provider_rate                # noqa: E402 — shared pacing rules

API = "https://api.zxinfo.dk/v3"
FILES = "https://zxinfo.dk"        # screen/artwork paths are returned host-relative
DEFAULT_COOLDOWN_MS = 500

# ZXInfo screen `type` -> our canonical media kind. Its "Loading screen" is a real,
# distinct artefact of the platform and has no equivalent anywhere else in the stack —
# mapping it to `title_screen` is the closest honest fit, not a perfect one.
SCREEN_KIND = {
    "loading screen": "title_screen",
    "running screen": "screenshot",
    "in-game screen": "screenshot",
    "title screen": "title_screen",
}


class ZXInfoError(Exception):
    pass


def enabled():
    return config.get_bool("metadata_zxinfo_enabled", True)


def _cooldown_ms():
    try:
        return int(config.rate_limits("zxinfo").get("cooldown_ms")
                   or DEFAULT_COOLDOWN_MS)
    except Exception:                               # noqa: BLE001
        return DEFAULT_COOLDOWN_MS


_last = [0.0]


def _pace():
    # The gap arithmetic lives in provider_rate, not here. This function and
    # arcadedb's were byte-identical, and igdb's differed only in where the gap
    # came from; three copies of five lines is three places for one of them to
    # drift into not pacing at all.
    provider_rate.min_gap(_last, _cooldown_ms() / 1000.0)


def _get(path, params=None, timeout=30):
    _pace()
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": "ludodex",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None                             # a miss is a result
        raise ZXInfoError("HTTP %s" % e.code)
    except Exception as e:                          # noqa: BLE001
        raise ZXInfoError(str(e)[:120])


def search(title, size=8):
    """-> [{id, title, year, machine}] best-first. Empty list on a miss."""
    d = _get("/search", {"query": title, "size": size, "mode": "compact"})
    hits = (((d or {}).get("hits") or {}).get("hits")) or []
    out = []
    for h in hits:
        s = h.get("_source") or {}
        out.append({"id": h.get("_id"), "title": s.get("title"),
                    "year": s.get("yearOfRelease"), "machine": s.get("machineType"),
                    "genre": s.get("genreType")})
    return out


def game(entry_id):
    """The full record for a ZXInfo entry id, or None."""
    d = _get("/games/%s" % entry_id)
    if not d:
        return None
    return d.get("_source") or d


def extract_metadata(rec):
    """-> {ludodex attribute kind: value}. Absent fields omitted, never blanked."""
    if not rec:
        return {}
    out = {}
    y = rec.get("yearOfRelease")
    if y:
        out["release_year"] = str(y)
    g = (rec.get("genreType") or rec.get("genre") or "").strip()
    if g:
        out["genres"] = [g]
    mt = (rec.get("machineType") or "").strip()
    if mt:
        # The exact machine variant is a platform fact, not a genre — a 128K-only title
        # will not run on a 48K, and that is the sort of thing this archive exists for.
        out["device"] = mt
    lang = (rec.get("language") or "").strip()
    if lang:
        out["language"] = lang
    devs, pubs = [], []
    for a in (rec.get("authors") or []):
        nm = (a.get("name") or "").strip()
        if nm and nm not in devs:
            devs.append(nm)
    for p in (rec.get("publishers") or []):
        nm = (p.get("name") or "").strip()
        if nm and nm not in pubs:
            pubs.append(nm)
    if devs:
        out["developers"] = devs
    if pubs:
        out["publishers"] = pubs
    try:
        mx = int(rec.get("maxPlayers") or 0)
    except (TypeError, ValueError):
        mx = 0
    if mx == 1:
        out["game_modes"] = ["Single player"]
    elif mx > 1:
        out["game_modes"] = ["Multiplayer"]
    return out


def extract_media(rec):
    """-> [{kind, type, url}] from the archive's screen list."""
    out = []
    for s in ((rec or {}).get("screens") or []):
        u = (s.get("url") or "").strip()
        if not u:
            continue
        kind = SCREEN_KIND.get((s.get("type") or "").strip().lower(), "screenshot")
        out.append({"kind": kind, "type": s.get("type") or "",
                    "url": u if u.startswith("http") else FILES + u})
    return out


def main(argv):
    if not argv:
        print("usage: zxinfo.py <title> | --id <entry-id>")
        return 2
    if argv[0] == "--id" and len(argv) > 1:
        rec = game(argv[1])
        if not rec:
            print("(not found)")
            return 1
        print(rec.get("title"))
        for k, v in sorted(extract_metadata(rec).items()):
            print("    %-14s %s" % (k, str(v)[:70]))
        for m in extract_media(rec):
            print("    media %-14s %s" % (m["kind"], m["url"][:64]))
        return 0
    for h in search(" ".join(argv)):
        print("%-10s %-40s %-6s %s" % (h["id"], (h["title"] or "")[:40],
                                       h["year"] or "", h["machine"] or ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
