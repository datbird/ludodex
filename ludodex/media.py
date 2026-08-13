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
import hashlib
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
    # `icon` is the one kind whose shape is a SQUARE, and saying so needs a third value
    # — before this, square was only ever TOLERATED (see shape_ok) and never required,
    # so an icon had no shape test at all. Live, that let 14 games serve a non-square
    # asset as their icon: eleven 32x64 Genesis cartridge end-labels, Treasure's
    # PUBLISHER wordmark at 600x259, and a 600x140 strip.
    "icon": "square",
}
# How far from 1:1 still counts as square. 0.8..1.25 separates every real icon in the
# library (256x256, 128x128, …) from every offender (0.50, 2.00, 2.32, 4.29).
SQUARE_TOLERANCE = 1.25

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


# Mean edge energy below which a band counts as DEAD (blurred padding, no detail).
# Absolute on purpose — see the reasoning in `looks_padded`.
DEAD_BAND_ENERGY = 5.0


def band_energy(path, bands=9):
    """Mean edge energy per horizontal band, top to bottom — or None if unreadable.

    The single measurement both `looks_padded` (is there a dead RUN?) and
    `detail_density` (how much detail overall?) are derived from, so the two can never
    disagree about what they looked at.

    PORTRAIT ONLY, and both of its consumers are limited by that on purpose — see the
    warnings in their docstrings. Returning None here is what enforces it."""
    try:
        from PIL import Image, ImageFilter
    except Exception:                       # noqa: BLE001  Pillow absent
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("L")
            w, h = im.size
            if h <= w or h < bands * 4:     # only meaningful for portrait canvases
                return None
            edges = im.filter(ImageFilter.FIND_EDGES)
            step = h // bands
            out = []
            for i in range(bands):
                box = (0, i * step, w, (i + 1) * step if i < bands - 1 else h)
                px = list(edges.crop(box).getdata())
                out.append(sum(px) / max(1, len(px)))
        return out or None
    except Exception:                       # noqa: BLE001  unreadable / not an image
        return None


def detail_density(path):
    """How much detail an image carries THROUGHOUT — the median band energy — or None.

    Used only to break a tie the `filler` flag could not: when every candidate in a
    bucket is flagged, that term is constant and has decided nothing, and falling
    through to raw pixel count is what let a 600x900 blurred paste beat a 300x450
    authored cover. Median, not mean, so one high-contrast band (a title wordmark)
    cannot carry it — the mistake the peak-relative filler threshold made.

    Live separation: Insurgency paste 2.6 vs authored 4.5/3.0; Arx Fatalis paste 1.6 vs
    authored 6.8/7.1.

    NOT SCALE-INVARIANT, so it is NOT a general tiebreak. This is edge energy PER PIXEL,
    and downscaling concentrates it: resampled to half and quarter size, 8 of 8 live
    covers scored HIGHER the smaller they got, monotonically (e.g. 1.88 -> 2.23 -> 3.12).
    Used between two clean candidates it therefore prefers the thumbnail — the exact
    defect `res_band` exists to prevent. Dry-run, widening select()'s `_blind` condition
    from "every candidate is a paste" to "the term is constant" moved 244 cover picks,
    every one of them from a 300x450 to a 264x352 IGDB thumbnail on this number alone.
    It is only safe where the gap it is reading (blurred paste vs authored art) dwarfs
    the scaling bias, which is why its caller requires ALL candidates to be pastes."""
    e = band_energy(path)
    if not e:
        return None
    s = sorted(e)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def looks_padded(path, bands=9):
    """True when an image is a LETTERBOXED PASTE — real content in a central band,
    blurred/flat padding above and below.

    Steam auto-generates a `portrait.png` for games that never got library art by
    dropping the ~460x215 header onto a 600x900 canvas and blur-filling the rest. The
    result passes every geometric test — it is genuinely 600x900 portrait — while being
    visually a landscape image. Measured orientation and EFFECTIVE orientation disagree,
    and that gap is the whole defect.

    Deterministic, no model: slice into horizontal bands and compare per-band edge
    energy. Authored cover art carries detail top to bottom; a padded paste has near-zero
    high-frequency energy in the outer bands and all of it concentrated in the middle.

    Conservative by construction — it only reports True when the contrast is stark, and
    callers must treat it as a DEMOTION signal, never a deletion: an image that is all a
    game has must still be servable.

    PORTRAIT ONLY, and deliberately so — `band_energy` returns None for a landscape
    canvas and that is what enforces it. The obvious mirror (vertical bands, to catch a
    pillarboxed landscape asset) was built and measured, and it does not work: it flags
    395 live assets of which 294 are Steam's 1920x620 library heroes, whose dark left and
    right edges are AUTHORED — deliberate space for the UI's overlay text — not padding.
    A contiguous dead RUN is exactly what that design produces."""
    energy = band_energy(path, bands)
    if not energy:
        return False
    try:
        peak = max(energy)
        if peak < 8.0:                      # no real content anywhere — not our case
            return False
        # A CONTIGUOUS RUN of dead bands is the tell, not low energy on its own.
        # Authored art can have one quiet edge (a plain border, a dark sky); measured
        # samples show a real cover bottoming out for at most ~3 of 9 bands, while a
        # padded paste goes flat for 4+ consecutive bands.
        #
        # The line is ABSOLUTE, not a fraction of `peak`. It used to be
        # `max(4.0, peak * 0.25)`, and `peak` is an outlier: one band holding a
        # high-contrast title wordmark drags the line above the rest of the artwork, so
        # dark box art reads as padding end to end. Live 2026-08-07, Arx Fatalis
        # returned filler=1 for ALL THREE candidates — the real paste and both authored
        # covers — which makes the term constant, so ranking fell through to resolution
        # and Steam's 600x900 letterboxed paste beat a 300x450 authored cover on size.
        # The paste was caught only by accident, being blurred enough to hit the floor.
        #
        # Padding is BLURRED, so its absolute high-frequency energy is near zero. That
        # is a property of the padding, not of how contrasty the rest of the image is,
        # so the line must not move when the artwork gets brighter. Measured over all
        # 5,245 live cover files, every value in 4.5..7.0 keeps all known pastes and
        # clears all known authored covers; 5.0 sits mid-plateau rather than on an edge.
        # Below ~4.5 the lighter-blurred pastes (Shadowrun Dragonfall, Super Lucky's
        # Tale — padding at 2.2..4.1) start being missed.
        dead = DEAD_BAND_ENERGY
        run = best = 0
        for e in energy:
            run = run + 1 if e < dead else 0
            best = max(best, run)
        return best >= (bands // 2)
    except Exception:                       # noqa: BLE001  unreadable / not an image
        return False


# --------------------------------------------------------------------------- #
#  THEMED-PACK ("template") detection.
#
#  Community art packs ship one decorated frame and drop each game's name inside it:
#  a bezel, a neon grid, a shared gradient. Every member is a real image of the right
#  shape and the right kind, so every geometric test passes — and it is not that
#  game's art. Live: 43 games shared ONE plate, spanning Civilization, Halo, Contra,
#  Metro 2033 and Comix Zone.
#
#  This is not a judgement about taste and carries no threshold about "how decorated"
#  an image may be. It is a statement of fact about the corpus: art that is pixel-wise
#  identical across many DIFFERENT games is not any one of their art. Nothing here
#  names a provider, a colour or a kind, so the next pack is caught the same way.
#
#  WHY THE FRAME'S PIXELS AND NOT ITS SILHOUETTE: the first cut hashed the alpha
#  channel, which is cheaper and finds the pack — and convicts whole kinds with it.
#  Every `box_3d` render shares a box outline and every `bezel` shares a bezel
#  outline, so shape alone flagged 64 perfectly good 3D boxes as a template. A themed
#  pack shares the frame's COLOURS; a 3D box carries its own art out to the edge.
#  Hashing what the frame looks like separates them: the box_3d clusters vanish and
#  the packs stay.
# --------------------------------------------------------------------------- #
FRAME_GRID = 32          # signature resolution
FRAME_INSET = 5          # cells from each edge that make up the frame band
_FRAME_MIN_CELLS = 4     # distinct quantised colours below which a frame is "flat"


def frame_sig(path):
    """A stable hash of an image's FRAME BAND, or None when it hasn't got one.

    Returns None — never a sentinel string — for anything that must not participate:
    Pillow absent, unreadable file, or a frame so flat it carries no design (a plain
    transparent margin, one solid colour). Those would collide with each other by the
    thousand and manufacture enormous fake "packs", so the absence of a frame has to
    be the absence of a signature, not a signature meaning absence.

    Composited onto black so transparent corners hash deterministically, and quantised
    to 3 bits per channel so re-encoding or a resize can't split a pack."""
    try:
        from PIL import Image
    except Exception:                       # noqa: BLE001  Pillow absent
        return None
    try:
        with Image.open(path) as im:
            rgba = im.convert("RGBA").resize((FRAME_GRID, FRAME_GRID), Image.BILINEAR)
        flat = Image.new("RGB", (FRAME_GRID, FRAME_GRID), (0, 0, 0))
        flat.paste(rgba, mask=rgba.split()[-1])
        px = list(flat.getdata())
        cells = []
        for y in range(FRAME_GRID):
            for x in range(FRAME_GRID):
                if (FRAME_INSET <= x < FRAME_GRID - FRAME_INSET
                        and FRAME_INSET <= y < FRAME_GRID - FRAME_INSET):
                    continue                # the middle is the game's own name/art
                r, g, b = px[y * FRAME_GRID + x]
                cells.append((r >> 5, g >> 5, b >> 5))
        if len(set(cells)) < _FRAME_MIN_CELLS:
            return None                     # no design in the border: not a frame
        return hashlib.sha1(str(cells).encode()).hexdigest()[:16]
    except Exception:                       # noqa: BLE001  unreadable / not an image
        return None


#  THE SECOND SIGNATURE, and why one was not enough.
#
#  `frame_sig` hashes the border's PIXELS, which is what stopped it convicting every
#  box_3d render. It therefore only clusters plates whose border is identical. This pack
#  ships per-game gradients on its "world" variants: Comix Zone's `wheel-carbon (wor)`
#  hashed to a frame shared by exactly ONE game and kept the slot, while its own `(jp)`
#  siblings hashed into clusters of 11 and 12 and were correctly demoted. Same plate, same
#  oval, different colours, invisible to a colour hash.
#
#  The SILHOUETTE catches those — it was the first thing tried and was abandoned because
#  a shared outline convicts a whole kind (every 3D box is box-shaped). That is only true
#  for kinds whose shape belongs to the KIND. A clear-logo's shape belongs to the GAME:
#  it is the wordmark cut out of transparency, so two different games sharing one exactly
#  means neither is a wordmark. So the rejected signal is correct after all, on exactly
#  the kinds where the objection does not apply.
#
#  Measured live at 32/48/64px: the plate clusters hold at 59 games identically at every
#  resolution, so this is not a downsampling artefact.
SILHOUETTE_KINDS = ("logo",)
SILHOUETTE_GRID = 64


def silhouette_sig(path):
    """Hash of an image's binarised ALPHA outline, or None when it hasn't got one.

    None for a degenerate silhouette — a plain opaque rectangle or a near-empty one —
    because those collide by the thousand and would manufacture enormous fake packs.
    Only meaningful for SILHOUETTE_KINDS; see the reasoning above."""
    try:
        from PIL import Image
    except Exception:                       # noqa: BLE001  Pillow absent
        return None
    try:
        with Image.open(path) as im:
            a = im.convert("RGBA").split()[-1].resize(
                (SILHOUETTE_GRID, SILHOUETTE_GRID), Image.BILINEAR)
        bits = "".join("1" if v > 128 else "0" for v in a.getdata())
        cov = bits.count("1") / float(SILHOUETTE_GRID * SILHOUETTE_GRID)
        if not (0.05 < cov < 0.95):
            return None                     # no outline to speak of
        return hashlib.sha1(bits.encode()).hexdigest()[:16]
    except Exception:                       # noqa: BLE001  unreadable / not an image
        return None


# How many DISTINCT games must share one frame before it is called a template. Two is
# routinely legitimate — a game and its director's cut, Quake II's two mission packs —
# so the floor is three. Measured on the live library: at 3 the rule fires on themed
# logo/icon/marquee packs and on a publisher's shared background (four unrelated
# 11 bit studios games), and on nothing else.
TEMPLATE_MIN_GAMES = 3


# Measured-resolution BAND. Deliberately a band, not raw pixels, and deliberately
# 3-valued: LARGE / UNKNOWN / SMALL.
#
# The ranking policy is: the image wins, then the provider. But raw pixels cannot sit
# above provider directly, because an UNMEASURED asset has no pixel count and would lose
# to every measured one — which silently re-privileges whichever provider happens to be
# measurable (the same trap shape_ok's docstring warns about). Banding puts unknown in
# the middle: a demonstrably LARGE image beats an unknown one, an unknown one beats a
# demonstrably SMALL one, and provider only breaks ties inside a band.
RES_LARGE, RES_UNKNOWN, RES_SMALL = 0, 1, 2
_RES_MIN_PX = 250_000          # ~500x500; a 264x352 thumbnail is not a cover

# The surface each kind is DISPLAYED on. "Big enough" is meaningless in the abstract:
# 460x215 is a perfectly good header and a hopeless full-screen background, and one
# global line cannot say both. Measured across the live library, that single line was
# CONSTANT for 8 of 13 scalar kinds — 100% LARGE for background/hero/bezel/mix/box_back,
# 100% SMALL for header, 97% SMALL for logo — and a term that is constant across every
# candidate has decided nothing, so the choice fell through to provider order. That is
# the same defect already fixed once for `filler`; it was still live here.
#
# Only kinds with a DEFENSIBLE canonical size are listed. The rest keep the global
# default rather than have a number invented for them — a wrong line is worse than a
# blunt one, because it would silently disqualify good art. Steam's header and
# library_hero sizes are the documented ones already relied on in _DERIVED_DIMS above.
KIND_TARGET_PX = {
    "cover":       600 * 900,      # Steam library capsule / SGDB grid standard
    "header":      460 * 215,      # Steam header.jpg (documented)
    "hero":       1920 * 620,      # Steam library_hero.jpg (documented)
    "logo":        640 * 360,      # Steam logo.png (documented)
    "background": 1920 * 1080,     # fills a screen
    "bezel":      1920 * 1080,     # overlays a screen
    # `icon` was held back until it had a SHAPE rule. A resolution band only means
    # "better" once shape is constrained: dry-run without one, an icon line promoted a
    # 600x300 strip into an icon slot on size alone. KIND_ORIENT now requires square,
    # so bigger is finally a safe thing to prefer here.
    "icon":        256 * 256,      # common app-icon maximum
}
# LARGE means "can fill at least half the pixels of the surface it lands on" — below
# that the asset is being upscaled by more than ~1.4x in each axis.
#
# The fraction is the only number here that was chosen rather than looked up, and it is
# checkable: half of a 600x900 cover is 270,000, within 8% of the 250,000 line that was
# hand-picked for covers and is known to work. The formula reproduces the one case with
# a track record instead of re-fitting it, which is the reason to trust it elsewhere.
_RES_TARGET_FRACTION = 0.5


def res_min_px(kind=None):
    """The LARGE/SMALL line for a kind — its own, or the global default."""
    target = KIND_TARGET_PX.get(kind or "")
    return int(target * _RES_TARGET_FRACTION) if target else _RES_MIN_PX


def res_band(w, h, kind=None):
    """LARGE / UNKNOWN / SMALL for the ranking sort. Unknown is never penalised.

    `kind` is optional so an existing caller keeps the global line rather than silently
    changing meaning; selection passes it, because that is where the comparison is
    between candidates for one slot with one surface to fill."""
    if not w or not h:
        return RES_UNKNOWN
    try:
        return RES_LARGE if int(w) * int(h) >= res_min_px(kind) else RES_SMALL
    except (TypeError, ValueError):
        return RES_UNKNOWN


def shape_ok(kind, w, h):
    """False ONLY when the orientation is known AND contradicts the kind.

    Unknown dimensions are never penalised — an unmeasured asset must not lose to a
    measured one on that basis alone, or selection would silently prefer whichever
    provider happened to be measurable. Square is tolerated everywhere (logos and some
    box art are legitimately square) — but a kind whose shape IS square must actually
    be square, which is a requirement the portrait/landscape pair could not express."""
    want = KIND_ORIENT.get(kind)
    if not want:
        return True
    if want == "square":
        if not w or not h:
            return True                     # unmeasured is never penalised
        try:
            r = int(w) / int(h)
        except (TypeError, ValueError, ZeroDivisionError):
            return True
        return 1.0 / SQUARE_TOLERANCE <= r <= SQUARE_TOLERANCE
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
