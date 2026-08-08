#!/usr/bin/env python3
"""Console production eras — the hardware timeline used to catch wrongly-merged games.

A game dated year Y that is NEWER than a console's whole production era cannot be a ROM
on that console. When a catalog entry has an IGDB identity dated Y and ALSO an emulation
ROM on a console whose era ended well before Y, that ROM is a DIFFERENT game that only
collapsed in because the titles normalize the same — e.g. a ~1994 Game Boy "Uno" folded
into the 2016 Steam "UNO", or the Apple II "Alice in Wonderland" (1985) sharing a
norm_key with the 2010 film game. `impossible(platform, Y)` flags exactly that so the
matcher rejects it and build_library peels it back out. Keyed by the catalog's own
emulation platform labels (`sources.platform` for source='emulation').

The check is deliberately ONE-SIDED (a game too NEW for the console). The opposite —
a game OLDER than the console — is NOT flagged: modern consoles legitimately host
re-releases of old games (PS1 Classics on PS3, Virtual Console, NSO), and IGDB reports
the ORIGINAL first-release year, so a 1998 game re-released on PS3 would look
"impossible" for the PS3 era if we gated the low side. Gating only the high side keeps
those legitimate re-releases while still catching genuinely newer same-named games.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import platmap                    # canonical platform tokens

# platform label -> (first commercial year, last mainstream year). 9999 = still
# current / open-ended (never bound the upper edge — arcade, modern consoles).
ERAS = {
    # Nintendo
    "nes": (1983, 1995), "snes": (1990, 2003), "n64": (1996, 2002),
    "gamecube": (2001, 2007), "wii": (2006, 2020), "wiiu": (2012, 2019),
    "nintendo switch": (2017, 9999),
    "gameboy": (1989, 2001), "gameboy color": (1998, 2003), "gba": (2001, 2008),
    "nds": (2004, 2015), "3ds": (2011, 2022), "virtualboy": (1995, 1996),
    # Sega
    "sega ms": (1985, 1996), "sega genesis": (1988, 1998), "sega cd": (1991, 1996),
    "sega 32x": (1994, 1996), "sega saturn": (1994, 2000), "dreamcast": (1998, 2002),
    "gamegear": (1990, 1997),
    # Sony
    "psx": (1994, 2006), "ps2": (2000, 2013), "ps3": (2006, 2020),
    "ps4": (2013, 9999), "ps5": (2020, 9999), "psp": (2004, 2014), "psvita": (2011, 2021),
    # Microsoft
    "xbox": (2001, 2009), "xbox360": (2005, 2019), "xbox one": (2013, 9999),
    # Atari / SNK / NEC / others
    "atari 2600": (1977, 1992), "atari 5200": (1982, 1984), "atari 7800": (1986, 1992),
    "lynx": (1989, 1995), "jaguar": (1993, 1996), "jaguar cd": (1994, 1996),
    "neogeo": (1990, 2004), "neogeopocketcolor": (1998, 2001),
    "turbo gfx": (1987, 1999), "tubo duo": (1987, 1999), "3do": (1993, 1996),
    "amiga": (1985, 1996), "amigacd32": (1993, 1994), "apple2": (1977, 1993),
    "colecovision": (1982, 1984), "intellivision": (1979, 1990),
    "zx spectrum": (1982, 1992), "supervision": (1992, 1996),
    # open-ended: never a game's home platform gets bounded on the upper edge
    "mame": (1975, 9999),
}

# regional stagger on the low end; late/tail releases of the SAME game on the high end.
LOW_BUFFER = 2
HIGH_BUFFER = 4

# Retro handhelds: in the 8/16-bit era, a portable version of a console game was
# almost always a SEPARATE, downgraded, differently-built game. So a title that also
# lives on a home console / PC is a different game on one of these → peel it off.
HANDHELD_RETRO = {
    "gameboy", "gameboy color", "gba", "gamegear", "lynx", "neogeopocketcolor",
    "neogeo pocket", "ngp", "ngpc", "supervision", "wonderswan", "wonderswan color",
    "pokemon mini", "gizmondo", "ngage",
}
# Modern handhelds are often the SAME game as (or the lead platform for) a console
# release — too ambiguous to auto-split. Left to the AI-assist tier.
HANDHELD_MODERN = {"nds", "3ds", "psp", "psvita", "psp minis"}


def is_retro_handheld(platform):
    return (platform or "").strip().lower() in HANDHELD_RETRO


def is_handheld(platform):
    p = (platform or "").strip().lower()
    return p in HANDHELD_RETRO or p in HANDHELD_MODERN


# The same table keyed by CANONICAL platform token, because the catalog does not speak
# this table's dialect: `sources.platform` for emulation says 'sega genesis', but
# `games.platform` — which is what a store-owned console entry carries — says 'genesis',
# and ERAS.get('genesis') was None. A gate that silently answers "no era known" refuses
# nothing, so every such entry went unguarded; live, that is how a genesis entry took a
# 1995 Game Gear record's identity. Derived from ERAS so the two cannot drift, and only
# where the canon is UNAMBIGUOUS — platmap.canon folds several home computers into 'pc',
# and one canon covering several eras cannot name one.
_CANON_ERAS = {}
for _c in {platmap.canon(_l) for _l in ERAS}:
    _vals = {v for k, v in ERAS.items() if platmap.canon(k) == _c}
    if len(_vals) == 1:
        _CANON_ERAS[_c] = next(iter(_vals))


def era(platform):
    """(first, last) production years for a platform label, or None if unknown.

    None means "no era known", which the gate reads as "refuse nothing" — so a label
    this table cannot resolve is not a safe default, it is a hole. Accepts both this
    table's labels and the catalog's canonical ones."""
    p = (platform or "").strip().lower()
    return ERAS.get(p) or _CANON_ERAS.get(platmap.canon(p))


def _year(v):
    try:
        return int(str(v)[:4])
    except (TypeError, ValueError):
        return None


def impossible(platform, year):
    """True when a game dated `year` is too NEW to be on this console — i.e. `year` is
    well after the console's last commercial software. One-sided by design: a game
    OLDER than the console is NOT flagged (modern consoles re-release old games; IGDB
    reports the original year). Unknown platform or year → False (never split on missing
    data). LOW_BUFFER is retained for callers that want the launch year but no longer
    gates this check."""
    e = era(platform)
    y = _year(year)
    if not e or y is None:
        return False
    _lo, hi = e
    return y > hi + HIGH_BUFFER
