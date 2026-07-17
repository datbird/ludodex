#!/usr/bin/env python3
"""Shared ROM-filename parsing (No-Intro / Redump / GoodTools tags).

Used by build_romdb.py (the Unraid ROM indexer) and crawl.py (the local archive
crawler) so both clean titles and detect region/version/etc. the same way.
"""
import re

# A parenthetical whose comma/'+'-separated tokens are all regions -> region tag.
REGIONS = {
    "USA", "Europe", "Japan", "World", "Asia", "Australia", "Brazil", "Canada",
    "China", "Korea", "France", "Germany", "Italy", "Spain", "Netherlands",
    "Sweden", "Russia", "UK", "United Kingdom", "Taiwan", "Hong Kong",
    "Scandinavia", "Latin America", "Mexico", "Norway", "Denmark", "Finland",
    "Poland", "Portugal", "Greece", "Belgium", "Switzerland", "Austria",
    "Ireland", "New Zealand", "South Africa", "Israel", "India", "Turkey",
    "Czech", "Hungary", "Croatia", "Unknown", "US", "EU", "JP", "Japan, USA",
}
LANGS = {
    "En", "Fr", "De", "Es", "It", "Ja", "Nl", "Pt", "Sv", "No", "Da", "Fi",
    "Pl", "Ru", "Ko", "Zh", "Cs", "Hu", "El", "Tr", "Ca", "Hr", "Sk", "Sl",
    "Ar", "He", "Th", "Id", "Uk", "Ro", "Bg", "Et", "Lv", "Lt", "Eu", "Gl",
}
FLAG_WORDS = {
    "Proto", "Prototype", "Beta", "Alpha", "Demo", "Sample", "Unl", "Pirate",
    "Aftermarket", "Promo", "Kiosk", "Preview", "Debug", "Test", "Hack",
    "Trainer", "Bootleg", "Homebrew", "BIOS", "Enhancement Chip", "PD",
    "Public Domain", "NTSC", "PAL",
}
GOOD_CODES = {
    "U": "USA", "E": "Europe", "J": "Japan", "W": "World", "F": "France",
    "G": "Germany", "I": "Italy", "S": "Spain", "Sw": "Sweden", "Nl": "Netherlands",
    "No": "Norway", "K": "Korea", "C": "China", "Tw": "Taiwan", "A": "Australia",
    "As": "Asia", "B": "Brazil", "Gr": "Greece", "HK": "Hong Kong", "R": "Russia",
    "D": "Netherlands", "Pt": "Portugal", "Fi": "Finland", "Da": "Denmark",
    "Cz": "Czech", "H": "Holland", "Un": "Unknown", "UK": "UK", "Ca": "Canada",
    "Mx": "Mexico", "FC": "French Canada", "FN": "Finland/Norway", "GR": "Greece",
}
_COMBO = re.compile(r"^[UEJWFGISKCABRDH]{2,6}$")

# Case-insensitive decode: ROM sets aren't consistent about capitalization, so a
# lowercase (u)/(usa)/(en) must resolve the same as (U)/(USA)/(En). NOTE: the multi-
# letter _COMBO regex above stays UPPERCASE-only on purpose — case-folding it would
# misread ordinary words whose letters are all region codes ("Sega" -> S,E,G,A).
_REGIONS_CI = {r.lower(): r for r in REGIONS}
_LANGS_CI = {l.lower(): l for l in LANGS}
_GOOD_CI = {k.lower(): v for k, v in GOOD_CODES.items()}

# Generic container folders that aren't the game itself.
GENERIC_DIRS = {"archive", "archives", "archived", "favorites", "favorite",
                "roms", "games", "game", "iso", "isos", "complete", "_storage"}

# Media / support subfolders that hold art, manuals, saves — never a game.
MEDIA_DIRS = {"images", "manuals", "videos", "media", "screenshots", "snaps",
              "box", "boxart", "wheel", "marquee", "covers", "downloaded_media",
              "support", "bezels", "logos", "fanart", "music", "gamelist",
              "gamelists", "cheats", "saves", "states", "screenshot", "titles",
              "3dboxes", "physicalmedia", "miximages"}

# Extensions that indicate an actual ROM/disc image (skip box-art/manuals/etc).
ROM_EXTS = {
    "sfc", "smc", "nes", "fds", "unf", "gba", "gb", "gbc", "n64", "z64", "v64",
    "nds", "dsi", "3ds", "cia", "cci", "nsp", "xci", "iso", "chd", "cue", "bin",
    "img", "mdf", "nrg", "ccd", "wbfs", "rvz", "gcm", "gcz", "wad", "cso", "pbp",
    "vpk", "pkg", "gdi", "cdi", "32x", "md", "gen", "smd", "sms", "gg", "pce",
    "a26", "a52", "a78", "lnx", "ws", "wsc", "ngp", "ngc", "col", "int", "vec",
    "d64", "adf", "ipf", "dsk", "tap", "z80", "rom", "vb", "min", "sv", "j64",
    "jag", "lto", "sg", "sc", "zip", "7z", "rar", "chd", "elf", "dol", "wud",
    "wux", "wua", "nkit", "m3u", "supercard",
}


def toks(s):
    return [t.strip() for t in re.split(r"[,+]", s) if t.strip()]


def parse_name(filename):
    """-> (title, region, languages, version, revision, disc, flags, raw_tags)."""
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    groups = re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", base)
    parens = [g[0] for g in groups if g[0] != ""]
    bracks = [g[1] for g in groups if g[1] != ""]
    m = re.match(r"^(.*?)\s*[\(\[]", base)
    title = (m.group(1) if m else base).strip()

    region = ""
    languages = ""
    version = ""
    revision = ""
    disc = ""
    flags = []
    for g in parens:
        t = toks(g)
        if not region and t and all(x.lower() in _REGIONS_CI for x in t):
            region = ", ".join(_REGIONS_CI[x.lower()] for x in t)
            continue
        if not region and g.lower() in _GOOD_CI:
            region = _GOOD_CI[g.lower()]
            continue
        if not region and _COMBO.match(g):
            region = ", ".join(_GOOD_CI[c.lower()] for c in g if c.lower() in _GOOD_CI)
            continue
        if not languages and t and all(x.lower() in _LANGS_CI for x in t):
            languages = ",".join(_LANGS_CI[x.lower()] for x in t)
            continue
        if not languages and re.fullmatch(r"M\d+", g):
            languages = g
            continue
        if not version and re.match(r"^(v[\d]|PRG\d|Version)", g, re.I):
            version = g.strip()
            continue
        if not revision and re.match(r"^Rev(\s|$|\.|[A-Z0-9])", g, re.I):
            revision = g.strip()
            continue
        if not disc and re.match(r"^(Disc|Disk|Side)\b", g, re.I):
            disc = g.strip()
            continue
        for w in t:
            head = w.split()[0] if w.split() else w
            if head in FLAG_WORDS or w in FLAG_WORDS:
                flags.append(w)
    for g in bracks:
        if g == "!":
            flags.append("verified")
        elif re.fullmatch(r"[a-z][a-z0-9]*", g):
            flags.append("dump:" + g)
        else:
            head = g.split()[0] if g.split() else g
            if head in FLAG_WORDS or g in FLAG_WORDS:
                flags.append(g)
    raw = " ".join("(%s)" % g for g in parens) + \
          (" " + " ".join("[%s]" % g for g in bracks) if bracks else "")
    return (title, region, languages, version, revision, disc,
            ",".join(dict.fromkeys(flags)), raw.strip())


_REGION_DIRS = {r.lower() for r in REGIONS}


def _clean_title(fn):
    """A game's display title from a filename or folder name (tags stripped)."""
    t = parse_name(fn)[0].strip()
    return t or fn


def compute_game_keys(rows):
    """Assign every ROM-index row a *game* string, grouping files into games the
    way a human would — the hard part of counting a ROM archive accurately.

    A ROM tree mixes three layouts, often within one system:
      • loose files        system/Archive/Game (USA).zip          → 1 game / file
      • collection folders  system/All Releases/Game A.gba, B.gba  → 1 game / file
      • game folders        system/Favorite/Game/…/eboot.bin       → 1 game / folder
    Plus wrapper dirs (Archive/Favorite), region subfolders (Japan/USA), and media
    subfolders (images/manuals) that must never be mistaken for games.

    `rows` is an iterable of (id, system, relpath, ext). Returns {id: game}, where
    game='' marks a non-game file (media/junk) to be excluded. Region/version
    variants of one title collapse to a single game (distinct *titles*, not files).
    """
    rows = list(rows)
    # Pass 1 — resolve each file to (system, top-folder, kind, title) after
    # stripping leading wrapper/region/media dirs; tally direct ROM children per
    # folder so we can tell a collection (many distinct-titled ROMs) from a
    # self-contained game folder (data files under one title).
    direct = {}          # (system, top.lower()) -> set(titles)  [collection evidence]
    resolved = {}        # id -> (system, top|None, kind, title)
    for rid, system, relpath, ext in rows:
        parts = relpath.split("/")
        mid, fn = parts[1:-1], parts[-1]
        ext = (ext or "").lower()
        i = 0
        while i < len(mid):
            dl = mid[i].lower()
            if dl in GENERIC_DIRS or dl in MEDIA_DIRS or dl in _REGION_DIRS:
                i += 1
                continue
            break
        rest = mid[i:]
        if not rest:                                  # loose file under a wrapper
            resolved[rid] = (system, None, "loose", _clean_title(fn)) \
                if ext in ROM_EXTS else None
            continue
        top = rest[0]
        if top.lower() in MEDIA_DIRS:                 # stray media, not a game
            resolved[rid] = None
            continue
        if len(rest) == 1 and ext in ROM_EXTS:        # direct ROM child of `top`
            t = _clean_title(fn)
            direct.setdefault((system, top.lower()), set()).add(t.lower())
            resolved[rid] = (system, top, "member", t)
        else:                                         # deeper/non-ROM → folder game
            resolved[rid] = (system, top, "folder", _clean_title(top))
    # Pass 2 — emit the game string. A folder that has direct ROM children is a
    # collection: its members are the games and its other files aren't distinct
    # games (game=''). A folder with no direct ROM children is one game.
    out = {}
    for rid, v in resolved.items():
        if v is None:
            out[rid] = ""
            continue
        system, top, kind, title = v
        if kind in ("loose", "member"):
            out[rid] = title
        elif (system, top.lower()) in direct:         # non-member file in a collection
            out[rid] = ""
        else:                                          # self-contained game folder
            out[rid] = title
    return out
