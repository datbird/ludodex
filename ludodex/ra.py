"""RetroAchievements API client.

Enrichment provider (NOT an ownership source): given a game, it supplies the
full achievement set plus which ones the configured user has earned.

Auth is username + web API key (Settings > Services > Credentials, resolved via
config). RetroAchievements requires a descriptive User-Agent and rate-limits
callers, so every request goes through a throttle driven by the per-service
limits in Settings > Services > Rate limits (config.rate_limits).
"""

import json
import sys
import time
import urllib.parse
import urllib.request

import config

BASE = "https://retroachievements.org/API/API_%s.php"
UA = "ludodex/1.0 (+https://github.com/datbird/ludodex)"

# ludodex platform label (media.norm_system) -> RA console id.
CONSOLE_ID = {
    "nes": 7, "snes": 3, "n64": 2, "gamecube": 16,
    "gameboy": 4, "gameboy color": 6, "gba": 5, "nds": 18,
    "sega genesis": 1, "sega ms": 11, "gamegear": 15, "sega saturn": 39,
    "sega 32x": 10, "sega cd": 9, "dreamcast": 40, "sg-1000": 33,
    "turbo gfx": 8, "psx": 12, "ps2": 21, "psp": 41,
    "atari 2600": 25, "atari 7800": 51, "lynx": 13, "jaguar": 17,
    "neogeo": 27, "mame": 27, "arcade": 27, "3do": 43,
    "colecovision": 44, "intellivision": 45, "wonderswan": 53,
}


def creds():
    """(username, api_key) or (None, None) if not configured."""
    u = config.get("retroachievements_username")
    k = config.get("retroachievements_api_key")
    return (u, k) if (u and k) else (None, None)


# ---- rate limiting (cooldown + per-minute; per-day soft guard) -------------- #
_last = 0.0
_minute = []
_day_used = 0


def _throttle():
    global _last, _day_used
    lim = config.rate_limits("retroachievements")
    if lim["per_day"] and _day_used >= lim["per_day"]:
        raise RuntimeError("RA per-day request cap (%d) reached this run" % lim["per_day"])
    now = time.monotonic()
    cd = lim["cooldown_ms"] / 1000.0
    if cd and _last:
        wait = _last + cd - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
    if lim["per_min"]:
        cutoff = now - 60
        _minute[:] = [t for t in _minute if t > cutoff]
        if len(_minute) >= lim["per_min"]:
            time.sleep(max(0.0, 60 - (now - _minute[0])))
            now = time.monotonic()
        _minute.append(now)
    _last = now
    _day_used += 1


def _call(endpoint, **params):
    u, k = creds()
    if not u:
        raise RuntimeError("RetroAchievements not configured (username + API key)")
    params.update(z=u, y=k)
    _throttle()
    url = BASE % endpoint + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_console_ids():
    return _call("GetConsoleIDs")


def get_game_list(console_id, only_achievements=True):
    """All games for a console: [{ID, Title, ...}]. f=1 limits to games that
    actually have achievements (keeps the match set relevant + smaller)."""
    return _call("GetGameList", i=console_id, f=1 if only_achievements else 0)


def get_user_progress(game_id, user=None):
    """GetGameInfoAndUserProgress -> full achievement set + this user's earned
    status. Achievements is {id: {Title, Description, Points, BadgeName,
    DateEarned, DateEarnedHardcore, ...}}."""
    u = user or creds()[0]
    return _call("GetGameInfoAndUserProgress", g=game_id, u=u)


if __name__ == "__main__":                       # quick manual smoke test
    u, k = creds()
    print("configured:", bool(u), "user:", u, file=sys.stderr)
    prof = _call("GetUserProfile", u=u)
    print("profile:", prof.get("User"), "pts:", prof.get("TotalPoints"))
