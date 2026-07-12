#!/usr/bin/env python3
"""Outbound device sync — push a wanted game (ROM + chosen media + gamelist entry)
from the master ROM repo onto a device's emulation frontend (RetroDECK / ES-DE).

This is the PUSH direction, the inverse of devices.sync_device() (which PULLs a
device's library into the catalog). It turns a `device_wants` queue entry into real
files on the target device, laid out the way ES-DE expects:

    <rom_path>/<esde-system>/<rom-file>
    <media_path>/<esde-system>/<esde-media-folder>/<rom-basename>.<ext>
    <gamelists>/<esde-system>/gamelist.xml   (text metadata; ES-DE matches media by
                                              filename, so media paths aren't stored)

Transport, job/progress and the ROM index all come from devices.py + build_romdb;
this module owns the layout math, the ROM resolver, the gamelist writer and the
per-system format-conversion rules.
"""
import os
import re
import sqlite3
import xml.etree.ElementTree as ET

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)

# --------------------------------------------------------------------------- #
#  Catalog platform  ->  ES-DE system folder name
# --------------------------------------------------------------------------- #
# media.ESDE_SYSTEM_ALIAS maps ES-DE folder -> our platform; this is the reverse,
# choosing the canonical ES-DE folder for each of our platforms. Platforms whose
# label already IS a valid ES-DE folder (snes, nes, psx, n64, gba, nds, ps2, ps3,
# psp, psvita, dreamcast, saturn, wii, wiiu, switch…) fall through unchanged.
CATALOG_TO_ESDE = {
    "sega genesis": "genesis",
    "sega ms": "mastersystem",
    "sega cd": "segacd",
    "sega 32x": "sega32x",
    "sega saturn": "saturn",
    "gameboy": "gb",
    "gameboy color": "gbc",
    "gamecube": "gc",
    "jaguar": "atarijaguar",
    "jaguar cd": "atarijaguarcd",
    "lynx": "atarilynx",
    "atari st": "atarist",
    "atari 2600": "atari2600",
    "atari 5200": "atari5200",
    "atari 7800": "atari7800",
    "3ds": "n3ds",
    "nintendo switch": "switch",
    "zx spectrum": "zxspectrum",
    "turbo gfx": "tg16",
    "tubo duo": "tg16",
    "neogeopocketcolor": "ngpc",
    "neogeo": "neogeo",
    "mame": "arcade",
    "arcade (mame)": "arcade",
}


def esde_system(platform):
    """Our catalog platform label -> the ES-DE system folder name."""
    p = (platform or "").strip().lower()
    return CATALOG_TO_ESDE.get(p, p)


# --------------------------------------------------------------------------- #
#  Chosen-kind  ->  ES-DE downloaded_media subfolder (reverse of media.ESDE_TYPE_KIND)
# --------------------------------------------------------------------------- #
KIND_TO_ESDE_FOLDER = {
    "cover": "covers",
    "background": "fanart",
    "logo": "marquees",
    "screenshot": "screenshots",
    "title_screen": "titlescreens",
    "box_3d": "3dboxes",
    "box_back": "backcovers",
    "physical_media": "physicalmedia",
    "mix": "miximages",
    "video": "videos",
    "manual": "manuals",
}


# --------------------------------------------------------------------------- #
#  Per-system format-conversion rules
# --------------------------------------------------------------------------- #
# fmt: what the emulator wants; tool: how to get there (runs on the target device,
# where RetroDECK bundles chdman/dolphin-tool). "copy" = ship the file as-is.
# Multi-disc titles additionally get an .m3u playlist (pure text, no tool needed).
_CD_SYSTEMS = {"psx", "ps2", "saturn", "segacd", "pcenginecd", "pcfx", "3do",
               "neogeocd", "amigacd32", "megacd", "tg-cd", "turbografxcd"}
_DISC_SRC_EXTS = {"cue", "bin", "iso", "img", "gdi", "toc", "ccd", "mdf", "nrg"}


def convert_plan(esde_sys, ext):
    """Return (target_ext, tool) for a source ROM. tool ∈ {copy, chd, rvz}. CD-based
    systems → .chd via chdman; GameCube/Wii → .rvz via dolphin-tool; everything else
    ships as-is (RetroArch cores read .zip/.7z and raw ROMs directly)."""
    e = (ext or "").lower().lstrip(".")
    if esde_sys in _CD_SYSTEMS and e in _DISC_SRC_EXTS:
        return ("chd", "chd")
    if esde_sys in ("gc", "wii") and e in ("iso", "gcm", "rvz", "wbfs", "ciso"):
        return ("rvz", "rvz") if e != "rvz" else ("rvz", "copy")
    return (e, "copy")


# --------------------------------------------------------------------------- #
#  ROM resolver — a wanted game's emulation source(s) -> real file(s) in a repo
# --------------------------------------------------------------------------- #
def _rom_index(mgr_id):
    p = os.path.join(DATA, "roms-index-mgr%d.sqlite" % int(mgr_id))
    return p if os.path.exists(p) else None


def resolve_roms(mgr_id, system, game_name):
    """Find the ROM file(s) for one (system, game) in a source manager's ROM index.
    Returns [{fullpath, relpath, filename, ext, disc, region}] — a list because a
    multi-disc game is several files. `game_name` is matched against the index's
    tag-stripped `game` grouping key first, then the raw `name`, case-insensitively."""
    idx = _rom_index(mgr_id)
    if not idx:
        return []
    con = sqlite3.connect(idx)
    con.row_factory = sqlite3.Row
    try:
        cols = "fullpath, relpath, filename, ext, disc, region"
        rows = con.execute(
            "SELECT %s FROM roms WHERE lower(system)=lower(?) AND lower(game)=lower(?) "
            "ORDER BY disc, filename" % cols, (system, game_name)).fetchall()
        if not rows:
            rows = con.execute(
                "SELECT %s FROM roms WHERE lower(system)=lower(?) AND lower(name)=lower(?) "
                "ORDER BY disc, filename" % cols, (system, game_name)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


# --------------------------------------------------------------------------- #
#  ES-DE gamelist.xml — read-modify-write (never clobber foreign entries)
# --------------------------------------------------------------------------- #
# ES-DE resolves MEDIA by filename convention, so gamelist holds only text metadata.
_GL_FIELDS = ("name", "sortname", "desc", "rating", "releasedate",
              "developer", "publisher", "genre", "players")


def _fmt_releasedate(val):
    """A year or ISO date -> ES-DE's YYYYMMDDT000000 stamp (year-only -> Jan 1)."""
    s = str(val or "").strip()
    m = re.search(r"(\d{4})(?:-(\d{2})-(\d{2}))?", s)
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2) or "01", m.group(3) or "01"
    return "%s%s%sT000000" % (y, mo, d)


def gamelist_upsert(xml_text, entries):
    """Upsert `entries` into an ES-DE gamelist.xml document (string in, string out).
    Each entry: {path, name, desc, rating(0-1), release_year|release_date, developer,
    publisher, genre, players, favorite}. Games are keyed by <path> ('./<file>'):
    an existing entry is updated in place; foreign entries are left untouched."""
    try:
        root = ET.fromstring(xml_text) if xml_text and xml_text.strip() else ET.Element("gameList")
    except ET.ParseError:
        root = ET.Element("gameList")
    if root.tag != "gameList":
        root = ET.Element("gameList")
    by_path = {(g.findtext("path") or "").strip(): g for g in root.findall("game")}

    for e in entries:
        path = e.get("path")
        if not path:
            continue
        g = by_path.get(path)
        if g is None:
            g = ET.SubElement(root, "game")
            ET.SubElement(g, "path").text = path
            by_path[path] = g

        def setf(tag, value):
            if value in (None, ""):
                return
            el = g.find(tag)
            if el is None:
                el = ET.SubElement(g, tag)
            el.text = str(value)

        setf("name", e.get("name"))
        setf("desc", e.get("desc"))
        if e.get("rating") not in (None, ""):
            try:
                setf("rating", "%.2f" % max(0.0, min(1.0, float(e["rating"]))))
            except (TypeError, ValueError):
                pass
        rd = _fmt_releasedate(e.get("release_date") or e.get("release_year"))
        setf("releasedate", rd)
        setf("developer", e.get("developer"))
        setf("publisher", e.get("publisher"))
        setf("genre", e.get("genre"))
        setf("players", e.get("players"))
        if e.get("favorite"):
            setf("favorite", "true")

    _indent(root)
    return "<?xml version=\"1.0\"?>\n" + ET.tostring(root, encoding="unicode")


def _indent(elem, level=0):
    """Pretty-print ElementTree in place (stdlib ET has no indent pre-3.9-safe)."""
    pad = "\n" + "  " * level
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = pad + "  "
        for i, child in enumerate(elem):
            _indent(child, level + 1)
            tail_pad = pad + ("  " if i < len(elem) - 1 else "")
            if not (child.tail or "").strip():
                child.tail = tail_pad
    if level and not (elem.tail or "").strip():
        elem.tail = pad
