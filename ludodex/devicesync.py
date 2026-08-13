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
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish_profiles     # noqa: E402  target layouts, as data

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))

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


def esde_system(platform, profile=None):
    """Our catalog platform label -> the target's system folder name.

    Named for ES-DE because that is what every caller has always called it; the profile
    argument is what makes it true of any target. Defaults to ES-DE, so an unqualified
    call behaves exactly as it did."""
    return publish_profiles.system_for(profile or publish_profiles.ESDE, platform)


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


def convert_plan(esde_sys, ext, profile=None):
    """(target_ext, tool) for a source ROM. tool ∈ {copy, chd, rvz, unzip}.

    The rules are the profile's, not this module's: what a file must BECOME is a fact
    about the destination emulator, and it is the first thing that differs between two
    frontends pointed at the same library."""
    return publish_profiles.convert_plan(profile or publish_profiles.ESDE,
                                         esde_sys, ext)


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
        cols = "fullpath, relpath, filename, ext, disc, region, flags, name, game"
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


# --------------------------------------------------------------------------- #
#  Push planning — pick which file(s) to send, and where they land
# --------------------------------------------------------------------------- #
def esde_gamelists_path(media_path, profile=None):
    """The directory this target's per-system metadata files live in, or None when the
    profile writes none."""
    return publish_profiles.metadata_root(profile or publish_profiles.ESDE, media_path)


# CD entry-point extensions (one per disc); the rest are member tracks pulled along.
_DISC_ENTRY = {"cue", "gdi", "chd", "iso", "pbp", "ccd", "toc", "m3u"}
_REGION_RANK = {"usa": 0, "world": 1, "us": 0, "u": 0, "w": 1, "europe": 2, "e": 2,
                "eur": 2, "japan": 3, "j": 3, "jpn": 3}


def _variant_key(h):
    """Sort key to pick the BEST cart dump among duplicates: verified first, then
    preferred region, no bad/hack flags, then shortest filename (cleanest)."""
    flags = (h.get("flags") or "").lower()
    region = (h.get("region") or "").strip().lower()
    verified = 0 if "verified" in flags or "[!]" in (h.get("filename") or "") else 1
    bad = 1 if any(b in flags for b in ("dump:b", "bad", "hack", "pirate", "proto",
                                        "beta", "demo")) else 0
    return (bad, verified, _REGION_RANK.get(region, 5), len(h.get("filename") or ""))


def pick_rom_files(hits, esde_sys):
    """From a game's resolved files, choose what to actually push. Returns a list of
    'discs': [{files:[fullpath…], entry:fullpath, disc:int|None, basename:str}].
    Cart game → one disc, one best-variant file. CD game → one entry per disc (with
    its member tracks), so a multi-disc set becomes N discs (+ an .m3u later)."""
    if not hits:
        return []
    is_cd = esde_sys in _CD_SYSTEMS or any(
        (h.get("ext") or "").lower() in _DISC_ENTRY for h in hits)
    if not is_cd:
        best = sorted(hits, key=_variant_key)[0]
        base = os.path.splitext(best["filename"])[0]
        return [{"files": [best["fullpath"]], "entry": best["fullpath"],
                 "disc": None, "basename": base}]
    # CD: group by disc; the entry point is the .cue/.gdi/.chd, tracks ride along.
    discs = {}
    for h in hits:
        d = h.get("disc") or 0
        discs.setdefault(d, []).append(h)
    out = []
    for d in sorted(discs):
        grp = discs[d]
        entries = [h for h in grp if (h.get("ext") or "").lower() in _DISC_ENTRY]
        entry = sorted(entries, key=_variant_key)[0] if entries else sorted(grp, key=_variant_key)[0]
        base = os.path.splitext(entry["filename"])[0]
        out.append({"files": [h["fullpath"] for h in grp], "entry": entry["fullpath"],
                    "disc": (d or None), "basename": base})
    return out


# --------------------------------------------------------------------------- #
#  Chosen media → local repo files (to push into ES-DE downloaded_media)
# --------------------------------------------------------------------------- #
def chosen_media_files(media_index_db, repo_dir, norm_key, profile=None):
    """{target_folder: (local_repo_path, ext)} for a game's chosen, materialized media —
    only kinds THIS TARGET has a folder for, only assets already pulled into the repo.

    A profile with no media map returns nothing, which is correct: a plain folder target
    has nowhere to put a cover, and inventing a folder for it would be worse than
    skipping it."""
    if not os.path.exists(media_index_db):
        return {}
    con = sqlite3.connect(media_index_db)
    con.row_factory = sqlite3.Row
    out = {}
    try:
        for r in con.execute(
                "SELECT kind, sha1, ext FROM media WHERE norm_key=? AND chosen=1 "
                "AND sha1 IS NOT NULL AND sha1!=''", (norm_key,)):
            folder = publish_profiles.media_folder(
                profile or publish_profiles.ESDE, r["kind"])
            if not folder:
                continue
            ext = (r["ext"] or "jpg").split("?")[0].lstrip(".")
            p = os.path.join(repo_dir, "%s.%s" % (r["sha1"], ext))
            if os.path.exists(p):
                out[folder] = (p, ext)
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()
    return out


# --------------------------------------------------------------------------- #
#  Ingest diff — files on a device that the master archive doesn't have
# --------------------------------------------------------------------------- #
def diff_ingest(device_mgr_id, archive_mgr_id, limit=None):
    """Games present in a device's ROM index but NOT in the archive's, matched by
    (system, game) case-insensitively. Returns [{system, game, files:[…], n_files}]
    — candidates to pull back into the master archive."""
    dev, arch = _rom_index(device_mgr_id), _rom_index(archive_mgr_id)
    if not dev or not arch:
        return []
    ac = sqlite3.connect(arch)
    have = set()
    try:
        for s, g in ac.execute("SELECT DISTINCT lower(system), lower(game) FROM roms"):
            have.add((s, g))
    finally:
        ac.close()
    dc = sqlite3.connect(dev)
    dc.row_factory = sqlite3.Row
    games, order = {}, []
    try:
        for r in dc.execute("SELECT system, game, relpath, filename, ext, disc "
                            "FROM roms ORDER BY system, game, disc, filename"):
            key = (r["system"].lower(), (r["game"] or "").lower())
            if key in have:
                continue
            if key not in games:
                games[key] = {"system": r["system"], "game": r["game"], "files": []}
                order.append(key)
            games[key]["files"].append({"relpath": r["relpath"], "filename": r["filename"],
                                        "ext": r["ext"], "disc": r["disc"]})
            if limit and len(order) >= limit and key == order[-1]:
                pass
    finally:
        dc.close()
    out = [{"system": games[k]["system"], "game": games[k]["game"],
            "files": games[k]["files"], "n_files": len(games[k]["files"])} for k in order]
    return out[:limit] if limit else out


def device_free_bytes(dev, path):
    """Free bytes at `path` on a device (via `df`), or None. dev is a device dict;
    None/local uses the local df."""
    import subprocess
    cmd = "df -PB1 %s 2>/dev/null | awk 'NR==2{print $4}'" % _shq(path)
    try:
        if not dev or dev.get("transport") == "local":
            out = subprocess.run(["bash", "-lc", cmd], capture_output=True,
                                 text=True, timeout=20).stdout
        else:
            import devices
            out = devices._ssh(dev, cmd, timeout=20)
        return int((out or "").strip() or 0) or None
    except Exception:
        return None


def _shq(s):
    return "'" + str(s).replace("'", "'\\''") + "'"
