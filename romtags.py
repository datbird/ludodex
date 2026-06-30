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

# Generic container folders that aren't the game itself.
GENERIC_DIRS = {"archive", "archives", "favorites", "favorite", "roms",
                "games", "game", "iso", "isos", "complete", "_storage"}

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
        if not region and t and all(x in REGIONS for x in t):
            region = ", ".join(t)
            continue
        if not region and g in GOOD_CODES:
            region = GOOD_CODES[g]
            continue
        if not region and _COMBO.match(g):
            region = ", ".join(GOOD_CODES[c] for c in g if c in GOOD_CODES)
            continue
        if not languages and t and all(x in LANGS for x in t):
            languages = ",".join(t)
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
