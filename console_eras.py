#!/usr/bin/env python3
"""Console production eras — the hardware timeline used to catch wrongly-merged games.

A game cannot ship on hardware that doesn't exist yet. When a catalog entry has a
store/IGDB identity dated year Y and ALSO an emulation ROM on a console whose
production era can't contain Y, that ROM is a DIFFERENT game that only collapsed in
because the titles normalize the same — e.g. a ~1994 Game Boy "Uno" folded into the
2016 Steam "UNO". `impossible(platform, Y)` flags exactly that so build_library can
peel it back out. Keyed by the catalog's own emulation platform labels
(`sources.platform` for source='emulation').

Y is IGDB's FIRST-release year, so a legitimately re-released old game (Sonic 1991 on
Genesis + Steam) keeps its original year and stays merged; only a genuinely NEW
same-named game (Uno 2016) trips the check.
"""

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


def era(platform):
    return ERAS.get((platform or "").strip().lower())


def _year(v):
    try:
        return int(str(v)[:4])
    except (TypeError, ValueError):
        return None


def impossible(platform, year):
    """True when a game dated `year` could NOT be on this console — i.e. `year` is
    before the console launched or well after its last commercial software. Unknown
    platform or year → False (never split on missing data)."""
    e = era(platform)
    y = _year(year)
    if not e or y is None:
        return False
    lo, hi = e
    return y < lo - LOW_BUFFER or y > hi + HIGH_BUFFER
