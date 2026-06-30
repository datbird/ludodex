#!/usr/bin/env python3
"""LaunchBox interchange — field/media/platform maps + filename rules.

LaunchBox (Windows frontend by Unbroken Software) stores its library as plain XML
(`LaunchBox/Data/Platforms/<Platform>.xml`) and media as files under
`LaunchBox/Images/<Platform>/<MediaType>/[<Region>/]<SanitizedTitle>[-NN].<ext>`
— so, unlike Playnite (LiteDB, needs an in-app bridge), ludodex reads/writes it
DIRECTLY on disk. This module holds the shared knowledge both directions use:
the media-type<->canonical-kind map, the platform-name map, the exact Title
filename sanitization, and stable per-game GUIDs.

Like Playnite, LaunchBox is a frontend META-LAYER, not a source: games map to
their underlying provider; "in LaunchBox" is provenance only.
"""
import re
import uuid

# Stable GUID namespace so a game's LaunchBox <ID> is deterministic across
# re-exports (idempotent: same norm_key -> same GUID, no dupes).
NS = uuid.UUID("6c0f4b2e-9a1d-4e7c-8b3a-ludodex0lbox".replace("ludodex0lbox",
                                                              "0b3a8b3a0b3a"))


def game_guid(norm_key):
    """Deterministic lowercase hyphenated GUID for a game (LaunchBox <ID>)."""
    return str(uuid.uuid5(NS, norm_key or ""))


# --- filename sanitization (LaunchBox's exact rule) ------------------------- #
# Windows-illegal chars \ / : * ? " < > | AND the apostrophe ' are replaced with
# underscore; everything else (spaces, () [] & . - accents) is kept as-is.
_BAD = re.compile(r"""[\\/:*?"<>|']""")


def sanitize_title(title):
    return _BAD.sub("_", title or "").rstrip()


def media_filename(title, ext, index=0):
    base = sanitize_title(title)
    ext = ext.lstrip(".").lower()
    if index:
        return "%s-%02d.%s" % (base, index, ext)
    return "%s.%s" % (base, ext)


# --- media type <-> canonical kind ------------------------------------------ #
# canonical kind -> the LaunchBox Images/ media-type folder we WRITE chosen art to.
KIND_TO_LB = {
    "cover": "Box - Front",
    "background": "Fanart - Background",
    "logo": "Clear Logo",
    "screenshot": "Screenshot - Gameplay",
    "title_screen": "Screenshot - Game Title",
    "box_3d": "Box - 3D",
    "box_back": "Box - Back",
    "physical_media": "Cart - Front",
    "marquee": "Arcade - Marquee",
    # video/manual go to Videos/ and Manuals/, not Images/ (handled separately)
}

# LaunchBox Images/ folder -> canonical kind, for IMPORT (covers fan-art + arcade
# variants). Folders not listed are skipped.
LB_TO_KIND = {
    "Box - Front": "cover", "Box - Front - Reconstructed": "cover",
    "Fanart - Box - Front": "cover",
    "Box - Back": "box_back", "Box - 3D": "box_3d",
    "Cart - Front": "physical_media", "Cart - Back": "physical_media",
    "Cart - 3D": "box_3d", "Disc": "physical_media",
    "Clear Logo": "logo",
    "Fanart - Background": "background",
    "Screenshot - Gameplay": "screenshot",
    "Screenshot - Game Title": "title_screen",
    "Screenshot - Game Select": "screenshot",
    "Screenshot - Game Over": "screenshot",
    "Banner": "banner", "Steam Banner": "banner",
    "Arcade - Marquee": "marquee",
}

# Video / manual roots (sibling of Images/), matched by sanitized Title too.
VIDEO_DIR = "Videos"
MANUAL_DIR = "Manuals"


# --- platform name map: our label <-> LaunchBox display name ---------------- #
# LaunchBox uses full canonical names (and the file is named for the platform).
TO_LB_PLATFORM = {
    "nes": "Nintendo Entertainment System",
    "snes": "Super Nintendo Entertainment System",
    "n64": "Nintendo 64", "gamecube": "Nintendo GameCube", "wii": "Nintendo Wii",
    "wiiu": "Nintendo Wii U", "nintendo switch": "Nintendo Switch",
    "gameboy": "Nintendo Game Boy", "gameboy color": "Nintendo Game Boy Color",
    "gba": "Nintendo Game Boy Advance", "nds": "Nintendo DS", "3ds": "Nintendo 3DS",
    "sega genesis": "Sega Genesis", "sega ms": "Sega Master System",
    "gamegear": "Sega Game Gear", "sega 32x": "Sega 32X", "sega cd": "Sega CD",
    "sega saturn": "Sega Saturn", "dreamcast": "Sega Dreamcast",
    "psx": "Sony Playstation", "ps2": "Sony Playstation 2",
    "ps3": "Sony Playstation 3", "psp": "Sony PSP", "psvita": "Sony Playstation Vita",
    "atari 2600": "Atari 2600", "atari 5200": "Atari 5200",
    "atari 7800": "Atari 7800", "jaguar": "Atari Jaguar", "lynx": "Atari Lynx",
    "3do": "3DO Interactive Multiplayer", "colecovision": "ColecoVision",
    "intellivision": "Mattel Intellivision", "neogeo": "SNK Neo Geo AES",
    "neogeopocketcolor": "SNK Neo Geo Pocket Color", "turbo gfx": "NEC TurboGrafx-16",
    "amiga": "Commodore Amiga", "zx spectrum": "Sinclair ZX Spectrum",
    "mame": "Arcade", "arcade": "Arcade",
}
FROM_LB_PLATFORM = {v: k for k, v in TO_LB_PLATFORM.items()}


def to_lb_platform(label):
    return TO_LB_PLATFORM.get((label or "").strip().lower(), label)


def from_lb_platform(name):
    return FROM_LB_PLATFORM.get(name, (name or "").strip().lower())


# --- catalog attribute kind -> LaunchBox <Game> child tag ------------------- #
# Multi-value tags join with "; "; scalars written directly. (release_year is
# folded into ReleaseDate when no full date.)
ATTR_TO_TAG = {
    "genres": ("Genre", True), "developers": ("Developer", True),
    "publishers": ("Publisher", True), "series": ("Series", True),
    "game_modes": ("PlayMode", True),
    "description": ("Notes", False), "release_date": ("ReleaseDate", False),
    "regions": ("Region", True),
}
