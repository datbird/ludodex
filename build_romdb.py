#!/usr/bin/env python3
"""Build a SQLite index of the emulation ROM archive from a `find -printf`
metadata dump (size<TAB>mtime<TAB>relpath-under-roms). Parses No-Intro / Redump
/ TOSEC style filename tags into queryable columns.

Usage: build_romdb.py <romscan.tsv> <out.sqlite> <roms_root_abs>
"""
import os
import re
import sys
import sqlite3
import time

TSV, DB, ROOT = sys.argv[1], sys.argv[2], sys.argv[3]

# Known region tokens (a parenthetical whose comma/'+'-separated tokens are all
# regions -> the region tag). Order-independent.
REGIONS = {
    "USA", "Europe", "Japan", "World", "Asia", "Australia", "Brazil", "Canada",
    "China", "Korea", "France", "Germany", "Italy", "Spain", "Netherlands",
    "Sweden", "Russia", "UK", "United Kingdom", "Taiwan", "Hong Kong",
    "Scandinavia", "Latin America", "Mexico", "Norway", "Denmark", "Finland",
    "Poland", "Portugal", "Greece", "Belgium", "Switzerland", "Austria",
    "Ireland", "New Zealand", "South Africa", "Israel", "India", "Turkey",
    "Czech", "Hungary", "Croatia", "Unknown", "US", "EU", "JP", "Japan, USA",
}
# Language codes seen in (En,Fr,De)-style groups.
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

# GoodTools region codes: exact short tokens, e.g. snes/nes "(U)", "(Sw)".
GOOD_CODES = {
    "U": "USA", "E": "Europe", "J": "Japan", "W": "World", "F": "France",
    "G": "Germany", "I": "Italy", "S": "Spain", "Sw": "Sweden", "Nl": "Netherlands",
    "No": "Norway", "K": "Korea", "C": "China", "Tw": "Taiwan", "A": "Australia",
    "As": "Asia", "B": "Brazil", "Gr": "Greece", "HK": "Hong Kong", "R": "Russia",
    "D": "Netherlands", "Pt": "Portugal", "Fi": "Finland", "Da": "Denmark",
    "Cz": "Czech", "H": "Holland", "Un": "Unknown", "UK": "UK", "Ca": "Canada",
    "Mx": "Mexico", "FC": "French Canada", "FN": "Finland/Norway", "GR": "Greece",
}
# Letters allowed in concatenated combos like "UE", "JU", "JUE".
_COMBO = re.compile(r"^[UEJWFGISKCABRDH]{2,6}$")

# Tokenize "A, B + C" -> ["A","B","C"]
def toks(s):
    return [t.strip() for t in re.split(r"[,+]", s) if t.strip()]

# Generic container folders that aren't the game itself.
GENERIC_DIRS = {"archive", "archives", "favorites", "favorite", "roms",
                "games", "game", "iso", "isos", "complete", "_storage"}


def parse_name(filename):
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    # all (...) and [...] groups, in order
    groups = re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", base)
    parens = [g[0] for g in groups if g[0] != ""]
    bracks = [g[1] for g in groups if g[1] != ""]
    # title = text before first tag group
    m = re.match(r"^(.*?)\s*[\(\[]", base)
    title = (m.group(1) if m else base).strip()

    region = ""; languages = ""; version = ""; revision = ""; disc = ""
    flags = []
    for g in parens:
        t = toks(g)
        # --- region: No-Intro full names, then GoodTools codes/combos ---
        if not region and t and all(x in REGIONS for x in t):
            region = ", ".join(t); continue
        if not region and g in GOOD_CODES:
            region = GOOD_CODES[g]; continue
        if not region and _COMBO.match(g):
            region = ", ".join(GOOD_CODES[c] for c in g if c in GOOD_CODES); continue
        if not languages and t and all(x in LANGS for x in t):
            languages = ",".join(t); continue
        if not languages and re.fullmatch(r"M\d+", g):   # GoodTools multi-language
            languages = g; continue
        if not version and re.match(r"^(v[\d]|PRG\d|Version)", g, re.I):
            version = g.strip(); continue
        if not revision and re.match(r"^Rev(\s|$|\.|[A-Z0-9])", g, re.I):
            revision = g.strip(); continue
        if not disc and re.match(r"^(Disc|Disk|Side)\b", g, re.I):
            disc = g.strip(); continue
        # leftover paren groups: pick out flag words
        for w in t:
            head = w.split()[0] if w.split() else w
            if head in FLAG_WORDS or w in FLAG_WORDS:
                flags.append(w)
    # bracket groups are usually GoodTools dump-status; keep notable ones as flags
    for g in bracks:
        if g == "!":
            flags.append("verified")
        elif re.fullmatch(r"[a-z][a-z0-9]*", g):  # b/h/p/t/a/o/f/c dumps + variants
            flags.append("dump:" + g)
        else:
            head = g.split()[0] if g.split() else g
            if head in FLAG_WORDS or g in FLAG_WORDS:
                flags.append(g)
    raw = " ".join("(%s)" % g for g in parens) + \
          (" " + " ".join("[%s]" % g for g in bracks) if bracks else "")
    return title, region, languages, version, revision, disc, \
        ",".join(dict.fromkeys(flags)), raw.strip()

con = sqlite3.connect(DB)
cur = con.cursor()
cur.executescript("""
PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
DROP TABLE IF EXISTS roms;
CREATE TABLE roms (
  id INTEGER PRIMARY KEY,
  system TEXT, subdir TEXT, game TEXT, filename TEXT, ext TEXT,
  name TEXT, region TEXT, languages TEXT, version TEXT, revision TEXT,
  disc TEXT, flags TEXT, tags TEXT,
  relpath TEXT, fullpath TEXT, size_bytes INTEGER, mtime REAL
);
DROP TABLE IF EXISTS systems;
DROP TABLE IF EXISTS meta;
CREATE TABLE meta (key TEXT, value TEXT);
""")

root = ROOT.rstrip("/")
batch = []
n = 0; total_bytes = 0
with open(TSV, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        line = line.rstrip("\n")
        if not line:
            continue
        try:
            size_s, mtime_s, rel = line.split("\t", 2)
        except ValueError:
            continue
        size = int(size_s) if size_s.isdigit() else 0
        try:
            mtime = float(mtime_s)
        except ValueError:
            mtime = 0.0
        parts = rel.split("/")
        system = parts[0] if parts else ""
        filename = parts[-1]
        mid = parts[1:-1]               # subdir components between system and file
        subdir = "/".join(mid)
        ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
        name, region, languages, version, revision, disc, flags, tags = parse_name(filename)
        # game folder = first subdir component that isn't a generic container
        game = ""
        for c in mid:
            if c.lower() not in GENERIC_DIRS:
                game = c; break
        # fall back to the game-folder name for any tag the filename lacked
        if game:
            gn, gr, gl, gv, grev, gd, gf, gt = parse_name(game)
            region = region or gr
            languages = languages or gl
            version = version or gv
            revision = revision or grev
            disc = disc or gd
            flags = flags or gf
        else:
            game = name
        batch.append((system, subdir, game, filename, ext, name, region, languages,
                      version, revision, disc, flags, tags, rel,
                      root + "/" + rel, size, mtime))
        n += 1; total_bytes += size
        if len(batch) >= 5000:
            cur.executemany(
                "INSERT INTO roms(system,subdir,game,filename,ext,name,region,languages,"
                "version,revision,disc,flags,tags,relpath,fullpath,size_bytes,mtime)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
            batch = []
if batch:
    cur.executemany(
        "INSERT INTO roms(system,subdir,game,filename,ext,name,region,languages,"
        "version,revision,disc,flags,tags,relpath,fullpath,size_bytes,mtime)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)

cur.executescript("""
CREATE INDEX ix_system   ON roms(system);
CREATE INDEX ix_ext      ON roms(ext);
CREATE INDEX ix_name     ON roms(name);
CREATE INDEX ix_game     ON roms(system, game);
CREATE INDEX ix_region   ON roms(region);
CREATE TABLE systems AS
  SELECT system,
         COUNT(*)                         AS files,
         COUNT(DISTINCT game)             AS games,
         SUM(size_bytes)                  AS bytes
  FROM roms GROUP BY system ORDER BY system;
""")
cur.execute("INSERT INTO meta(key,value) VALUES('root',?)", (root,))
cur.execute("INSERT INTO meta(key,value) VALUES('files',?)", (str(n),))
cur.execute("INSERT INTO meta(key,value) VALUES('total_bytes',?)", (str(total_bytes),))
cur.execute("INSERT INTO meta(key,value) VALUES('built_epoch',?)", (str(int(time.time())),))
con.commit()
cur.execute("VACUUM")
con.commit()
con.close()
print("rows=%d total_bytes=%d db=%s" % (n, total_bytes, DB))
