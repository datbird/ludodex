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
    "Czech", "Hungary", "Croatia", "Unknown", "US", "EU", "JP",
    # NB no comma-joined entry belongs here: toks() splits a parenthetical on commas
    # BEFORE this set is consulted, so "Japan, USA" could never be reached — and did not
    # need to be, because each half resolves on its own.
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

# Uppercase words whose every letter happens to be a GoodTools region code. The comment
# below claims the uppercase-only rule avoids "Sega"; it does not avoid "SEGA", which
# decoded as "Spain, Europe, Germany, Australia". "(CD)" decoded as "China, Netherlands".
# Flag words are NOT listed here — they are decided before the region rules run, which is
# what stopped "(HACK)" becoming "Holland, Australia, China, Korea" and, worse, having its
# Hack flag swallowed by the `continue` that followed.
# Only tokens the regex above ACTUALLY matches are listed; anything with a letter outside
# its character class ("DVD", "REV") could never reach this branch and listing it would
# imply a problem that does not exist.
_NOT_A_COMBO = {"SEGA", "CD", "SD", "HD", "GB", "GBC", "GBA", "GG", "SGB", "AGB",
                "CGB", "SFC", "AI", "ID", "BS", "CIB"}

# Case-insensitive decode: ROM sets aren't consistent about capitalization, so a
# lowercase (u)/(usa)/(en) must resolve the same as (U)/(USA)/(En). NOTE: the multi-
# letter _COMBO regex above stays UPPERCASE-only on purpose — case-folding it would
# misread ordinary words whose letters are all region codes ("Sega" -> S,E,G,A).
_REGIONS_CI = {r.lower(): r for r in REGIONS}
_LANGS_CI = {l.lower(): l for l in LANGS}
_GOOD_CI = {k.lower(): v for k, v in GOOD_CODES.items()}
# Flags decode case-insensitively too. This table was simply missing, so "(Beta)" carried
# a flag and "(beta)" carried none — a silent inconsistency in a module whose comment two
# lines up says the decode is case-insensitive.
_FLAGS_CI = {w.lower(): w for w in FLAG_WORDS}

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


# Only a KNOWN extension is an extension. `rsplit(".", 1)` treated EVERY dot as one, so
# "Dr. Mario 64" parsed to "Dr" — and compute_game_keys runs this over FOLDER names, where
# two games then collapsed into one game called "Dr". Same for "Mr. Driller",
# "S.T.A.L.K.E.R." and "Ep. 1".
_STRIPPABLE_EXTS = ROM_EXTS | {
    "png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "ico",
    "xml", "txt", "dat", "cfg", "ini", "json", "nfo", "pdf", "md", "csv", "html",
    "mp4", "mkv", "avi", "webm", "mov", "mp3", "ogg", "wav", "flac", "opus",
    "srm", "sav", "state", "cht", "log", "db", "sqlite", "torrent", "url", "lnk",
    "exe", "dll", "bat", "sh", "gz", "bz2", "xz", "tar", "sbi", "sub", "toc", "bak",
}

# A DISC IS NOT A GAME. "Disc 1.chd" beside "Disc 2.chd" is one game in three files, and
# reading each as a collection member turned every multi-disc PlayStation title into one
# game PER DISC. Nobody files a bare "Disc 1" loose among other people's games, so a
# folder holding one is a GAME FOLDER, never a collection.
_DISC_PART = re.compile(
    r"^(disc|disk|cd|dvd|side|part|track)\s*[\-_.]?\s*([0-9]{1,2}|[ivx]{1,4}|[ab])$",
    re.I)


def toks(s):
    return [t.strip() for t in re.split(r"[,+]", s) if t.strip()]


def strip_ext(filename):
    """`filename` without a trailing extension THIS MODULE RECOGNISES."""
    head, _dot, tail = (filename or "").rpartition(".")
    return head if head and tail.lower() in _STRIPPABLE_EXTS else (filename or "")


def is_disc_part(name):
    """True when a cleaned name designates a DISC of a game rather than a game."""
    return bool(_DISC_PART.match((name or "").strip()))


def parse_name(filename, strip_extension=True):
    """-> (title, region, languages, version, revision, disc, flags, raw_tags).

    `strip_extension=False` for a FOLDER name, which has no extension to strip: a dot in
    a directory name is part of the name, and removing everything after it renames the
    game."""
    base = strip_ext(filename) if strip_extension else (filename or "")
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
        # A FLAG IS DECIDED FIRST. "(HACK)" is [H,A,C,K] to the _COMBO regex — "Holland,
        # Australia, China, Korea" — and the `continue` on that branch then swallowed the
        # Hack flag outright. A parenthetical this module already names as a flag word is
        # never a region combo, whatever its letters spell.
        if g.strip().lower() in _FLAGS_CI:
            flags.append(_FLAGS_CI[g.strip().lower()])
            continue
        if not region and t and all(x.lower() in _REGIONS_CI for x in t):
            region = ", ".join(_REGIONS_CI[x.lower()] for x in t)
            continue
        if not region and g.lower() in _GOOD_CI:
            region = _GOOD_CI[g.lower()]
            continue
        if not region and _COMBO.match(g) and g.upper() not in _NOT_A_COMBO:
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
            hit = _FLAGS_CI.get(w.lower()) or _FLAGS_CI.get(head.lower())
            if hit:
                flags.append(hit)
    for g in bracks:
        if g == "!":
            flags.append("verified")
        elif re.fullmatch(r"[a-z][a-z0-9]*", g):
            flags.append("dump:" + g)
        else:
            head = g.split()[0] if g.split() else g
            hit = _FLAGS_CI.get(g.lower()) or _FLAGS_CI.get(head.lower())
            if hit:
                flags.append(hit)
    raw = " ".join("(%s)" % g for g in parens) + \
          (" " + " ".join("[%s]" % g for g in bracks) if bracks else "")
    return (title, region, languages, version, revision, disc,
            ",".join(dict.fromkeys(flags)), raw.strip())


_REGION_DIRS = {r.lower() for r in REGIONS}


def _clean_title(fn, is_dir=False):
    """A game's display title from a filename or folder name (tags stripped).

    A FOLDER HAS NO EXTENSION. This is called on folder names as well as filenames, and
    stripping a "trailing extension" off a directory renames the game — "Dr. Mario 64"
    became "Dr", which then collapsed with every other "Dr." folder in the system."""
    t = parse_name(fn, strip_extension=not is_dir)[0].strip()
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
    multidisc = set()    # (system, top.lower()) folders holding the parts of ONE game
    resolved = {}        # id -> (system, top|None, kind, title, sub|None)
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
            resolved[rid] = (system, None, "loose", _clean_title(fn), None) \
                if ext in ROM_EXTS else None
            continue
        top = rest[0]
        if top.lower() in MEDIA_DIRS:                 # stray media, not a game
            resolved[rid] = None
            continue
        if len(rest) == 1 and ext in ROM_EXTS:        # direct ROM child of `top`
            t = _clean_title(fn)
            if is_disc_part(t):
                # NOT a collection member: this folder holds the PARTS of one game. Read
                # as members, `Disc 1`/`Disc 2`/`Disc 3` each became a game of its own and
                # every multi-disc title in the library counted three or four times.
                multidisc.add((system, top.lower()))
                resolved[rid] = (system, top, "folder", _clean_title(top, True), None)
                continue
            direct.setdefault((system, top.lower()), set()).add(t.lower())
            resolved[rid] = (system, top, "member", t, None)
        else:                                         # deeper/non-ROM → folder game
            # `rest[1]` is the game folder when `top` turns out to be a COLLECTION. Without
            # it a game folder nested inside a collection resolved to the COLLECTION's
            # name, which pass 2 then blanked as "a non-member file" — so the game was not
            # counted at all, anywhere.
            resolved[rid] = (system, top, "folder", _clean_title(top, True),
                             rest[1] if len(rest) > 1 else None)
    # Pass 2 — emit the game string. A folder that has direct ROM children is a
    # collection: its members are the games and its other files aren't distinct
    # games (game=''). A folder with no direct ROM children is one game.
    out = {}
    for rid, v in resolved.items():
        if v is None:
            out[rid] = ""
            continue
        system, top, kind, title, sub = v
        if top is not None and (system, top.lower()) in multidisc:
            # every file under a multi-disc folder is one game: the folder's
            out[rid] = _clean_title(top, True)
        elif kind in ("loose", "member"):
            out[rid] = title
        elif (system, top.lower()) in direct:         # `top` is a COLLECTION
            # A file DEEPER inside it belongs to the game folder one level down; only a
            # file sitting directly in the collection is a non-game.
            out[rid] = _clean_title(sub, True) if sub else ""
        else:                                          # self-contained game folder
            out[rid] = title
    return out
