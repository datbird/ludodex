#!/usr/bin/env python3
"""Media vocabulary + provider mappings for ludodex.

Media is indexed by REFERENCE (a path or URL) and keyed by the catalog's stable
``norm_key`` (game_id is reassigned every build_library rebuild; norm_key is not).
This module owns the canonical media-kind vocabulary and the per-provider mapping
from each provider's own type names to that vocabulary — the same pattern the
project already uses for attributes (playnite.py) and external ids (igdb.py).

Providers fall into two classes:
  * local  — a filesystem media set on a registered mount path (ES-DE, Steam grid).
  * remote — fetched over HTTP by id (Steam CDN, IGDB, SteamGridDB).

ES-DE media sets are shared by BOTH major Steam Deck emulation setups: RetroDECK
(self-contained Flatpak, media under <retrodeck>/ES-DE/downloaded_media) and
EmuDeck (native install, ES-DE its default frontend). Same structure, different
root — so one path-configurable provider covers both; register each root as a
media mount (config.py media-mount add <path> esde).
"""

# --------------------------------------------------------------------------- #
#  canonical media kinds — the vocabulary every provider maps into
# --------------------------------------------------------------------------- #
KINDS = (
    "cover",          # box front / portrait capsule / grid portrait
    "background",     # fanart / hero / key art
    "logo",           # clear logo / marquee / wheel art
    "icon",           # small square app icon
    "screenshot",     # in-game screenshot
    "title_screen",   # title / boot screen
    "box_3d",         # 3D box render
    "box_back",       # rear box art
    "physical_media",  # cartridge / disc
    "mix",            # ES-DE composite "miximage"
    "video",          # preview / trailer
    "manual",         # scanned manual (pdf)
)

# Single-asset kinds (one chosen per game); the rest can have many (screenshots).
SCALAR_KINDS = ("cover", "background", "logo", "icon", "title_screen",
                "box_3d", "box_back", "mix")

# --------------------------------------------------------------------------- #
#  ES-DE (EmulationStation Desktop Edition) — used by RetroDECK *and* EmuDeck
# --------------------------------------------------------------------------- #
# media-type subfolder -> canonical kind. (miniimages/marquees etc. as seen on
# disk.) Unmapped folders (e.g. "CLEANUP") are skipped.
ESDE_TYPE_KIND = {
    "covers": "cover",
    "fanart": "background",
    "marquees": "logo",
    "screenshots": "screenshot",
    "titlescreens": "title_screen",
    "3dboxes": "box_3d",
    "backcovers": "box_back",
    "physicalmedia": "physical_media",
    "miximages": "mix",
    "videos": "video",
    "manuals": "manual",
}

# ES-DE system short-name (folder) -> our catalog platform label (as stored in
# sources.platform for source='emulation'). Names that already match our label
# (snes, nes, psx, dreamcast, n64, nds, gba, ps2, ps3, psvita, psp, gc->gamecube…)
# are normalized by norm_system(); this map only holds the genuine differences.
ESDE_SYSTEM_ALIAS = {
    "genesis": "sega genesis",
    "megadrive": "sega genesis",
    "mastersystem": "sega ms",
    "segacd": "sega cd",
    "sega32x": "sega 32x",
    "gb": "gameboy",
    "gbc": "gameboy color",
    "gc": "gamecube",
    "gamegear": "gamegear",
    "atari2600": "atari 2600",
    "atari5200": "atari 5200",
    "atari7800": "atari 7800",
    "atarijaguar": "jaguar",
    "atarilynx": "lynx",
    "atarist": "atari st",
    "n3ds": "3ds",
    "switch": "nintendo switch",
    "zxspectrum": "zx spectrum",
    "tg16": "turbo gfx",
    "ngp": "neogeopocketcolor",
    "saturn": "sega saturn",
}


def norm_system(esde_name):
    """Map an ES-DE system folder name to our catalog platform label."""
    n = esde_name.strip().lower()
    return ESDE_SYSTEM_ALIAS.get(n, n)


# --------------------------------------------------------------------------- #
#  Steam custom-artwork grid (local) — ~/.steam/.../userdata/<id>/config/grid
#  Files are keyed by Steam appid with a suffix that encodes the kind:
#    <appid>p.png      -> cover     (600x900 portrait / library capsule)
#    <appid>.png       -> background-ish landscape grid; treat as background
#    <appid>_hero.png  -> background (hero banner)
#    <appid>_logo.png  -> logo
#    <appid>_icon.png  -> icon
# --------------------------------------------------------------------------- #
def steamgrid_kind(filename):
    """(appid, kind) for a Steam grid file, or (None, None) if unrecognized."""
    import os
    base, ext = os.path.splitext(filename)
    if ext.lower() not in (".png", ".jpg", ".jpeg", ".ico"):
        return None, None
    for suffix, kind in (("_hero", "background"), ("_logo", "logo"),
                         ("_icon", "icon")):
        if base.endswith(suffix):
            appid = base[: -len(suffix)]
            return (appid if appid.isdigit() else None), kind
    if base.endswith("p") and base[:-1].isdigit():     # <appid>p = portrait cover
        return base[:-1], "cover"
    if base.isdigit():                                  # <appid> = landscape grid
        return base, "background"
    return None, None


# --------------------------------------------------------------------------- #
#  provider registry — local (mount-based) + remote (fetched by id)
# --------------------------------------------------------------------------- #
LOCAL_PROVIDERS = ("esde", "steamgrid", "playnite")
REMOTE_PROVIDERS = ("steam", "igdb", "steamgriddb")
MEDIA_PROVIDERS = LOCAL_PROVIDERS + REMOTE_PROVIDERS
