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
import re

# --------------------------------------------------------------------------- #
#  canonical media kinds — the vocabulary every provider maps into
# --------------------------------------------------------------------------- #
KINDS = (
    # portrait box / cover family
    "cover",          # box front / portrait capsule / library grid portrait
    "box_back",       # rear box art
    "box_3d",         # 3D box render
    "box_spine",      # box spine / side
    "physical_media",  # cartridge / disc / support label
    # wide / landscape art (kept distinct)
    "background",     # fanart / store page background
    "hero",           # wide library hero banner (Steam library_hero, SGDB hero)
    "header",         # capsule / banner (~460x215; Steam header, SGDB h-grids)
    # marks
    "logo",           # clear logo / wheel art (transparent)
    "icon",           # small square app icon
    # arcade
    "marquee",        # arcade marquee sign
    "bezel",          # screen bezel / overlay / backdrop
    "arcade_cabinet",  # full cabinet photo / render
    "arcade_controls",  # control panel / CPO / controls info
    "pcb",            # circuit board
    # screens / composite
    "screenshot",     # in-game screenshot
    "title_screen",   # title / boot screen
    "mix",            # ES-DE / Recalbox composite "miximage"
    # promo / misc
    "flyer",          # advertisement flyer / promo poster
    "map",            # game world / level map
    "video",          # preview / trailer
    "manual",         # scanned manual (pdf)
    "other",          # catch-all — anything unrecognized (never dropped; logged)
)

# Single-asset kinds (one chosen per game); the rest can have many (screenshots,
# physical_media, flyer, map, video, manual, other).
SCALAR_KINDS = ("cover", "box_back", "box_3d", "box_spine",
                "background", "hero", "header", "logo", "icon",
                "marquee", "bezel", "arcade_cabinet", "arcade_controls", "pcb",
                "title_screen", "mix")

# --------------------------------------------------------------------------- #
#  Shape verification (Algo tier — measurement, not judgment)
#
#  Selection used to rank on provider priority then row id, so nothing ever examined
#  the image: a landscape header could win a `cover` slot purely by being indexed
#  first. These let the deterministic picker REJECT an asset whose orientation
#  contradicts its kind, before any provider precedence applies.
#
#  Only kinds with a genuinely fixed orientation are listed. `logo`, `icon`, `mix`,
#  `physical_media` and friends are deliberately absent — they vary legitimately, and
#  guessing would be worse than not checking.
# --------------------------------------------------------------------------- #
KIND_ORIENT = {
    "cover": "portrait", "box_back": "portrait", "box_spine": "portrait",
    "background": "landscape", "hero": "landscape", "header": "landscape",
    "marquee": "landscape", "bezel": "landscape", "arcade_controls": "landscape",
    "title_screen": "landscape", "screenshot": "landscape",
}

# Fixed provider sizes we can know WITHOUT fetching. Deriving beats measuring: it costs
# no network, so shape is testable on the first pass rather than the pass after.
# Steam's `library_600x900` carries its size in the name (handled by the regex below);
# these are the documented constants for the rest.
_DERIVED_DIMS = {
    "header.jpg": (460, 215),                 # Steam store header — long-stable
    "library_hero.jpg": (1920, 620),          # Steam library hero
    "t_cover_big": (264, 352),                # IGDB documented sizes
    "t_720p": (1280, 720), "t_1080p": (1920, 1080),
    "t_screenshot_huge": (1280, 720), "t_thumb": (90, 128),
}
_DIM_RE = re.compile(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)")


def orient_of(w, h):
    """'portrait' | 'landscape' | 'square', or None when unmeasured."""
    if not w or not h:
        return None
    if h > w:
        return "portrait"
    return "landscape" if w > h else "square"


def derived_dims(ref):
    """(w, h) inferable from a provider URL/filename alone, else (None, None)."""
    if not ref:
        return (None, None)
    m = _DIM_RE.search(ref)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    for token, wh in _DERIVED_DIMS.items():
        if token in ref:
            return wh
    return (None, None)


def shape_ok(kind, w, h):
    """False ONLY when the orientation is known AND contradicts the kind.

    Unknown dimensions are never penalised — an unmeasured asset must not lose to a
    measured one on that basis alone, or selection would silently prefer whichever
    provider happened to be measurable. Square is tolerated everywhere (icons, logos
    and some box art are legitimately square)."""
    want = KIND_ORIENT.get(kind)
    if not want:
        return True
    got = orient_of(w, h)
    if got is None or got == "square":
        return True
    return got == want

# --------------------------------------------------------------------------- #
#  ES-DE (EmulationStation Desktop Edition) — used by RetroDECK *and* EmuDeck
# --------------------------------------------------------------------------- #
# media-type subfolder -> canonical kind. (miniimages/marquees etc. as seen on
# disk.) Unmapped folders (e.g. "CLEANUP") are skipped.
ESDE_TYPE_KIND = {
    "covers": "cover",
    "fanart": "background",
    "marquees": "logo",          # ES-DE marquee = wheel/clear-logo art
    "screenshots": "screenshot",
    "titlescreens": "title_screen",
    "3dboxes": "box_3d",
    "backcovers": "box_back",
    "physicalmedia": "physical_media",
    "miximages": "mix",
    "videos": "video",
    "manuals": "manual",
    "custom": "other",           # user-supplied extra image
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
#    <appid>.png       -> header     (landscape grid / capsule)
#    <appid>_hero.png  -> hero       (wide library hero banner)
#    <appid>_logo.png  -> logo
#    <appid>_icon.png  -> icon
# --------------------------------------------------------------------------- #
def steamgrid_kind(filename):
    """(appid, kind) for a Steam grid file, or (None, None) if unrecognized."""
    import os
    base, ext = os.path.splitext(filename)
    if ext.lower() not in (".png", ".jpg", ".jpeg", ".ico"):
        return None, None
    for suffix, kind in (("_hero", "hero"), ("_logo", "logo"),
                         ("_icon", "icon")):
        if base.endswith(suffix):
            appid = base[: -len(suffix)]
            return (appid if appid.isdigit() else None), kind
    if base.endswith("p") and base[:-1].isdigit():     # <appid>p = portrait cover
        return base[:-1], "cover"
    if base.isdigit():                                  # <appid> = landscape grid
        return base, "header"
    return None, None


# --------------------------------------------------------------------------- #
#  provider registry — local (mount-based) + remote (fetched by id)
# --------------------------------------------------------------------------- #
LOCAL_PROVIDERS = ("esde", "steamgrid", "playnite", "launchbox")
REMOTE_PROVIDERS = ("steam", "igdb", "steamgriddb")
MEDIA_PROVIDERS = LOCAL_PROVIDERS + REMOTE_PROVIDERS

# Per-kind provider priority for choosing the ONE best asset (first available
# wins). Curated/owned local art (your Steam custom grid, your ES-DE scrapes)
# beats official store art, which beats IGDB, which beats SteamGridDB community
# art. ES-DE has no real backgrounds, so it sinks for that kind.
PRIORITY = {
    "cover":        ["steamgrid", "esde", "gamelist", "screenscraper", "steam", "igdb", "steamgriddb", "playnite", "launchbox"],
    "background":   ["steamgrid", "steam", "screenscraper", "igdb", "steamgriddb", "esde", "gamelist", "playnite", "launchbox"],
    "hero":         ["steamgrid", "steam", "steamgriddb", "igdb", "screenscraper", "launchbox"],
    "header":       ["steamgrid", "steam", "steamgriddb", "launchbox", "screenscraper"],
    "logo":         ["steamgrid", "esde", "gamelist", "screenscraper", "steam", "igdb", "steamgriddb", "launchbox"],
    "icon":         ["steamgrid", "steamgriddb", "igdb", "screenscraper", "playnite"],
    "screenshot":   ["gamelist", "esde", "screenscraper", "launchbox"],
    "title_screen": ["esde", "screenscraper", "launchbox"],
    "box_3d":       ["esde", "screenscraper", "steamgriddb", "launchbox"],
    "box_back":     ["esde", "screenscraper", "launchbox"],
    "box_spine":    ["screenscraper", "launchbox"],
    "physical_media": ["esde", "screenscraper", "launchbox"],
    "marquee":      ["esde", "screenscraper", "launchbox"],
    "bezel":        ["screenscraper"],
    "arcade_cabinet":  ["launchbox", "screenscraper"],
    "arcade_controls": ["launchbox", "screenscraper"],
    "pcb":          ["launchbox", "screenscraper"],
    "flyer":        ["screenscraper", "launchbox"],
    "map":          ["screenscraper"],
    "mix":          ["esde", "screenscraper"],
}
DEFAULT_PRIORITY = ["steamgrid", "esde", "gamelist", "screenscraper", "steam", "igdb",
                    "steamgriddb", "playnite", "launchbox"]


def priority(kind):
    return PRIORITY.get(kind, DEFAULT_PRIORITY)
