#!/usr/bin/env python3
"""Target profiles — a frontend's conventions as DATA, not as code.

WHY. devicesync.py knows how to push a game to a device end to end: resolve the ROM,
pick the best variant, group a multi-disc set, convert a CD image to .chd, copy the
chosen art, write a gamelist entry. All of that is correct and none of it is reusable,
because every layout decision inside it is a module-level constant named after ES-DE.
A second frontend means a second copy of the whole file, and a third means three copies
drifting apart.

So the layout decisions move here, into dicts. The push logic keeps its shape and takes
a profile argument. ES-DE's profile is a transcription of what the constants already
said, which is the point: phase 2 changes no behaviour, it only moves the seam.

WHAT A PROFILE OWNS
  systems      our platform label            -> the target's folder name
  media        our media kind                -> the target's media folder
  media_layout where media lives relative to the ROM/media roots, and how it is named
  metadata     which writer, and where its file goes
  discs        which systems are disc-based, and which extensions are entry points
  convert      what a source extension must BECOME for a given system
  archives     whether this system's emulator can read a zipped ROM at all

WHAT A PROFILE DOES NOT OWN. Anything that is a fact about the GAME rather than about
the target: which dump of a cartridge is the best one, how discs group, what the
catalog calls a platform. Those stay in devicesync where they belong, because they are
the same answer whatever you are publishing to.

Profiles are plain data so they can eventually be user-edited — there are more frontends
than anyone will hardcode, and someone's Batocera fork will differ by two folder names.
"""

# --------------------------------------------------------------------------- #
#  Shared facts about discs. These describe MEDIA, not a frontend, so they are
#  defaults a profile may override rather than something each one restates.
# --------------------------------------------------------------------------- #
DISC_SRC_EXTS = {"cue", "bin", "iso", "img", "gdi", "toc", "ccd", "mdf", "nrg"}
# One entry point per disc; everything else in the group is a member track.
DISC_ENTRY_EXTS = {"cue", "gdi", "chd", "iso", "pbp", "ccd", "toc", "m3u"}


# --------------------------------------------------------------------------- #
#  ES-DE / RetroDECK
# --------------------------------------------------------------------------- #
# A transcription of the constants that used to live in devicesync.py. Anything that
# reads oddly here read oddly there; phase 2 deliberately preserves behaviour rather
# than improving it, so that a regression is a regression and not a "fix".
ESDE = {
    "id": "esde",
    "name": "ES-DE / RetroDECK",

    # Platforms whose label already IS a valid ES-DE folder (snes, nes, psx, n64, gba,
    # nds, ps2, ps3, psp, psvita, dreamcast, saturn, wii, wiiu, switch…) fall through
    # unchanged, so only the exceptions are listed.
    "systems": {
        "sega genesis": "genesis", "sega ms": "mastersystem", "sega cd": "segacd",
        "sega 32x": "sega32x", "sega saturn": "saturn",
        "gameboy": "gb", "gameboy color": "gbc", "gamecube": "gc",
        "jaguar": "atarijaguar", "jaguar cd": "atarijaguarcd", "lynx": "atarilynx",
        "atari st": "atarist", "atari 2600": "atari2600", "atari 5200": "atari5200",
        "atari 7800": "atari7800",
        "3ds": "n3ds", "nintendo switch": "switch", "zx spectrum": "zxspectrum",
        "turbo gfx": "tg16", "tubo duo": "tg16",
        "neogeopocketcolor": "ngpc", "neogeo": "neogeo",
        "mame": "arcade", "arcade (mame)": "arcade",
    },

    "media": {
        "cover": "covers", "background": "fanart", "logo": "marquees",
        "screenshot": "screenshots", "title_screen": "titlescreens",
        "box_3d": "3dboxes", "box_back": "backcovers",
        "physical_media": "physicalmedia", "mix": "miximages",
        "video": "videos", "manual": "manuals",
    },
    # ES-DE matches media to a ROM by FILENAME, so nothing about the media path is
    # recorded in the gamelist. dest = <media_path>/<system>/<folder>/<base>.<ext>
    "media_layout": {"root": "media", "path": "{system}/{folder}/{base}.{ext}"},

    "metadata": {
        "writer": "gamelist_xml",
        # RetroDECK's gamelists dir sits beside downloaded_media, so it is derived
        # rather than configured: …/ES-DE/downloaded_media → …/ES-DE/gamelists
        "root": "derive_sibling_of_media",
        "derive_from": "downloaded_media",
        "derive_to": "gamelists",
        "path": "{system}/gamelist.xml",
    },

    "discs": {
        "systems": {"psx", "ps2", "saturn", "segacd", "pcenginecd", "pcfx", "3do",
                    "neogeocd", "amigacd32", "megacd", "tg-cd", "turbografxcd"},
        "src_exts": DISC_SRC_EXTS,
        "entry_exts": DISC_ENTRY_EXTS,
        "playlist": "m3u",          # multi-disc sets get one
    },

    # (system-or-'*') -> {source ext -> (target ext, tool)}. Tools run where they are
    # available; RetroDECK bundles chdman and dolphin-tool on the device itself.
    "convert": {
        "_disc_systems": ("chd", "chd"),          # any disc system, any src ext
        "gc": {"iso": ("rvz", "rvz"), "gcm": ("rvz", "rvz"), "wbfs": ("rvz", "rvz"),
               "ciso": ("rvz", "rvz"), "rvz": ("rvz", "copy")},
        "wii": {"iso": ("rvz", "rvz"), "gcm": ("rvz", "rvz"), "wbfs": ("rvz", "rvz"),
                "ciso": ("rvz", "rvz"), "rvz": ("rvz", "copy")},
    },

    # RetroArch cores read .zip/.7z directly, so nothing is unpacked by default.
    "archives": {"default": "keep"},
}


# --------------------------------------------------------------------------- #
#  Plain folder
# --------------------------------------------------------------------------- #
# Deliberately the OPPOSITE shape to ES-DE in every respect that matters: no system
# renaming, no media, no metadata file, no conversion. It exists to prove the seam is
# a seam — if publishing still compiles ES-DE assumptions into the push path, this
# profile is what breaks.
#
# It is also genuinely useful: an SD card, a Steam Deck folder, a share you point an
# emulator at by hand.
FOLDER = {
    "id": "folder",
    "name": "Plain folder",
    "systems": {},                       # our platform label is the folder name
    "media": {},                         # no art
    "media_layout": None,
    "metadata": None,                    # nothing written
    "discs": {"systems": set(), "src_exts": DISC_SRC_EXTS,
              "entry_exts": DISC_ENTRY_EXTS, "playlist": "m3u"},
    "convert": {},                       # ship whatever the source is
    "archives": {"default": "keep"},
}


# --------------------------------------------------------------------------- #
#  EmulationStation gamelist layout (RetroBat / Batocera / rom-librarian)
# --------------------------------------------------------------------------- #
# TRANSCRIBED FROM A LIVE LIBRARY, not from documentation: G:\games\emulation\roms on
# <workstation>, scraped by ScreenScraper. Everything below was read out of a real
# gamelist.xml and the real folders beside it.
#
# The two ways it differs from ES-DE are exactly the ones the profile seam exists for:
#
#   * MEDIA LIVES INSIDE THE SYSTEM FOLDER (roms/<system>/images/…), not in a sibling
#     downloaded_media tree. So does the gamelist.
#   * THE GAMELIST RECORDS MEDIA PATHS EXPLICITLY (<image>./images/Foo-image.png</image>)
#     where ES-DE stores none and matches by filename. A writer that omits them here
#     produces a library with no art and no error.
#
# ...and the filenames carry a per-kind SUFFIX (-image, -marquee, -thumb, -video,
# -manual), which is why a media map of kind->folder is not expressive enough.
#
# The system names are LaunchBox-style (sonyplaystation, nintendosupernes) rather than
# Batocera's (psx, snes). That is what this library uses; a stock RetroBat install would
# want a different map and the same everything else — which is the point of the split.
ESGAMELIST = {
    "id": "esgamelist",
    "name": "EmulationStation gamelist (RetroBat / Batocera style)",

    "systems": {
        "psx": "sonyplaystation", "ps2": "sonyplaystation2", "ps3": "sonyplaystation3",
        "psp": "sonyplaystationportable", "psvita": "sonyplaystationvita",
        "snes": "nintendosupernes", "nes": "nintendones", "n64": "nintendo64",
        "gameboy": "nintendogameboy", "gb": "nintendogameboy",
        "gameboy color": "nintendogameboycolor", "gbc": "nintendogameboycolor",
        "gameboy advance": "nintendogameboyadvance", "gba": "nintendogameboyadvance",
        "nds": "nintendods", "3ds": "nintendo3ds",
        "gamecube": "nintendogamecube", "gc": "nintendogamecube",
        "wii": "nintendowii", "wiiu": "nintendowiiu",
        "nintendo switch": "nintendoswitch", "switch": "nintendoswitch",
        "virtualboy": "nintendovirtualboy",
        "sega genesis": "segagenesis", "genesis": "segagenesis",
        "sega ms": "segamastersystem", "mastersystem": "segamastersystem",
        "sega cd": "segacd", "sega 32x": "sega32x",
        "sega saturn": "segasaturn", "saturn": "segasaturn",
        "dreamcast": "segadreamcast", "gamegear": "segagamegear",
        "sg1000": "segasg1000",
        "turbo gfx": "necturbografx", "tg16": "necturbografx",
        "tg-cd": "necturbografxcd", "supergrafx": "necsupergrafx",
        "neogeo": "snkneogeo", "neogeocd": "snkneogeocd",
        "ngp": "snkneogeopocket", "neogeopocketcolor": "snkneogeopocketcolor",
        "ngpc": "snkneogeopocketcolor",
        "jaguar": "atarijaguar", "jaguar cd": "atarijaguarcd", "lynx": "atarilynx",
        "atari st": "atarist",
        "c64": "commodore64", "zx spectrum": "sinclairzxspectrum",
        "wonderswan": "bandaiwonderswan",
        "wonderswancolor": "bandaiwonderswancolor",
        "intellivision": "mattelintellivision",
        "mame": "mame", "arcade (mame)": "mame", "arcade": "mame",
        "dos": "msdos", "cdi": "philipscdi",
        "xbox": "msxbox", "xbox 360": "msxbox360",
    },

    # kind -> (folder, filename template). The suffix is not decoration: the scraper
    # writes -image/-marquee/-thumb and the gamelist points at those exact names.
    "media": {
        "cover": ("images", "{base}-image.{ext}"),
        "logo": ("images", "{base}-marquee.{ext}"),
        "screenshot": ("images", "{base}-thumb.{ext}"),
        "video": ("videos", "{base}-video.{ext}"),
        "manual": ("manuals", "{base}-manual.{ext}"),
    },
    "media_layout": {"root": "rom", "path": "{system}/{folder}/{name}"},

    "metadata": {
        "writer": "gamelist_xml",
        "root": "rom",                     # roms/<system>/gamelist.xml
        "path": "{system}/gamelist.xml",
        # ...and unlike ES-DE, the paths go IN the file, relative to the system folder.
        "records_media_paths": True,
        "relative_prefix": "./",
    },

    "discs": {
        "systems": {"sonyplaystation", "sonyplaystation2", "segasaturn", "segacd",
                    "necturbografxcd", "philipscdi", "3do", "snkneogeocd",
                    "commodoreamigacd32", "atarijaguarcd", "segadreamcast"},
        "src_exts": DISC_SRC_EXTS,
        "entry_exts": DISC_ENTRY_EXTS,
        "playlist": "m3u",
    },
    "convert": {"_disc_systems": ("chd", "chd")},
    "archives": {"default": "keep"},
}


PROFILES = {p["id"]: p for p in (ESDE, FOLDER, ESGAMELIST)}
DEFAULT_PROFILE = "esde"


def get(profile_id):
    """A profile by id. An unknown id is an ERROR, not a silent fallback to ES-DE —
    publishing to the wrong layout is worse than refusing to publish."""
    p = PROFILES.get((profile_id or "").strip().lower())
    if p is None:
        raise KeyError("unknown publish profile %r (have: %s)"
                       % (profile_id, ", ".join(sorted(PROFILES))))
    return p


def system_for(profile, platform):
    """Our catalog platform label -> the target's folder name."""
    p = (platform or "").strip().lower()
    return (profile.get("systems") or {}).get(p, p)


def media_folder(profile, kind):
    """The target's folder for a media kind, or None when it has no home there.

    None means "this target does not carry this kind", which is a real answer — the
    caller skips the asset rather than inventing a folder for it."""
    v = (profile.get("media") or {}).get(kind)
    return v[0] if isinstance(v, tuple) else v


def media_name(profile, kind, base, ext):
    """The FILENAME a target expects for one asset.

    ES-DE matches media to a ROM by name alone, so the file is simply <base>.<ext>. An
    EmulationStation gamelist library writes <base>-image.png, <base>-marquee.png and so
    on, and its gamelist points at those exact names — so this cannot be assumed."""
    v = (profile.get("media") or {}).get(kind)
    if isinstance(v, tuple):
        return v[1].format(base=base, ext=ext)
    return "%s.%s" % (base, ext)


def is_disc_system(profile, system):
    return system in ((profile.get("discs") or {}).get("systems") or set())


def entry_exts(profile):
    return (profile.get("discs") or {}).get("entry_exts") or DISC_ENTRY_EXTS


def playlist_ext(profile):
    return (profile.get("discs") or {}).get("playlist")


def convert_plan(profile, system, ext):
    """(target_ext, tool) for one source file. tool ∈ {copy, chd, rvz, unzip}.

    Order matters and is the same order the ES-DE constants implied: disc systems win
    over per-system rules, because a .iso on the PS2 is a disc image whatever else the
    profile says about .iso elsewhere."""
    e = (ext or "").lower().lstrip(".")
    disc = profile.get("discs") or {}
    conv = profile.get("convert") or {}

    if is_disc_system(profile, system) and e in (disc.get("src_exts") or set()):
        target = conv.get("_disc_systems")
        if target:
            return target

    rules = conv.get(system)
    if rules and e in rules:
        return rules[e]

    # An emulator that cannot read an archive has to have it unpacked, whatever the
    # system's other rules say.
    if e in ("zip", "7z") and archive_policy(profile, system) == "unzip":
        return ("", "unzip")            # target ext unknown until we look inside

    return (e, "copy")


def archive_policy(profile, system):
    """'keep' (ship the archive as-is) or 'unzip' (this emulator cannot read one)."""
    a = profile.get("archives") or {}
    return a.get(system, a.get("default", "keep"))


def metadata_root(profile, media_path, rom_path=None):
    """The DIRECTORY this target's metadata files live under, or None if it writes none.

    Separate from metadata_path because asking for the root by passing an empty system
    is a trap: "{system}/gamelist.xml" becomes "/gamelist.xml", and os.path.join with a
    leading slash discards everything before it — silently returning "/" instead of the
    real root."""
    import os
    meta = profile.get("metadata")
    if not meta:
        return None
    root = meta.get("root")
    if root == "derive_sibling_of_media":
        mp = (media_path or "").rstrip("/")
        frm, to = meta.get("derive_from", ""), meta.get("derive_to", "")
        if frm and mp.endswith("/" + frm):
            return os.path.join(mp[:-(len(frm) + 1)], to)
        return os.path.join(os.path.dirname(mp), to)
    if root == "rom":
        return rom_path
    return media_path


def metadata_path(profile, media_path, rom_path, system):
    """Absolute path of the metadata file for one system, or None if this profile
    writes none."""
    import os
    meta = profile.get("metadata")
    if not meta:
        return None
    base = metadata_root(profile, media_path, rom_path)
    if not base:
        return None
    return os.path.join(base, meta.get("path", "").format(system=system))


def media_dest(profile, media_path, rom_path, system, kind, base, ext):
    """Absolute destination for one media asset, or None when this target has no home
    for that kind.

    Takes the KIND, not the folder. Deriving the kind back from the folder looks
    equivalent and is not: cover and logo both live in images/ here, so a reverse
    lookup returns whichever was declared first and silently writes the marquee to
    <base>-image.png. The kind is the thing that determines the filename, so the kind
    is what gets passed."""
    import os
    lay = profile.get("media_layout")
    folder = media_folder(profile, kind)
    if not lay or not folder:
        return None
    root = media_path if lay.get("root") == "media" else rom_path
    tpl = lay.get("path", "")
    if "{name}" in tpl:
        return os.path.join(root, tpl.format(
            system=system, folder=folder, name=media_name(profile, kind, base, ext)))
    return os.path.join(root, tpl.format(
        system=system, folder=folder, base=base, ext=ext))
