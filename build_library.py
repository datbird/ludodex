#!/usr/bin/env python3
"""Build the unified, deduped game library from all sources:
  - emulation ROMs  (roms-index.sqlite)
  - Steam / Epic / GOG ownership TSVs
Dedupes by a normalized title so one game lists every source it's available from.

Output: game-library.sqlite
  games(id, canonical_title, norm_key, n_sources, sources_summary,
        has_emulation, has_steam, has_gog, has_epic)
  sources(game_id, source, platform, source_id, title_raw, detail)
"""
import os
import re
import sys
import sqlite3

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config

OWN = DIR                                   # store TSVs live next to the scripts
ROM_DB = config.get("roms_index_db")
OUT = config.get("library_db")

# Extensions that indicate an actual ROM/disc image (to skip box-art/manuals/etc).
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
MEDIA_GAMES = {"images", "manuals", "videos", "media", "screenshots", "snaps",
               "box", "wheel", "marquee", "covers", "", "downloaded_media"}

ROMAN = {"ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8",
         "ix": "9", "xi": "11", "xii": "12", "xiii": "13", "xiv": "14"}
EDITION = re.compile(
    r"\b(game of the year edition|goty|definitive edition|remaster(ed)?|"
    r"complete edition|deluxe edition|gold edition|enhanced edition|"
    r"ultimate edition|collector'?s edition|digital deluxe|standard edition|"
    r"legacy edition|premium edition|special edition)\b")


def norm(t):
    s = (t or "").lower()
    # strip a trailing ROM/disc/playlist extension left on faux-.m3u dir names
    s = re.sub(r"\.(m3u|iso|chd|cue|bin|img|mdf|nrg|ccd|rvz|wbfs|nkit|gcm|gcz|"
               r"cso|pbp|gdi|cdi|rom|nds|3ds|zip|7z|rar)$", "", s)
    s = re.sub(r"[™®©]", "", s)        # ™ ® ©
    s = re.sub(r"\([^)]*\)", " ", s)                   # (...) tags
    s = re.sub(r"\[[^\]]*\]", " ", s)                   # [...] tags
    s = s.replace("&", " and ").replace("+", " plus ")
    s = EDITION.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    toks = [ROMAN.get(w, w) for w in s.split()]
    while toks and toks[0] in ("the", "a", "an"):
        toks = toks[1:]
    return " ".join(toks).strip()


games = {}   # norm_key -> dict(title, store_title, sources=[])


def add(title, source, platform, sid, detail=""):
    key = norm(title)
    if not key:
        return
    g = games.get(key)
    if g is None:
        g = {"title": title, "store_title": None, "sources": []}
        games[key] = g
    # prefer a store title as the canonical (cleaner than tagged ROM names)
    if source != "emulation" and not g["store_title"]:
        g["store_title"] = title
    g["sources"].append((source, platform, str(sid), title, detail))


# ---- emulation (distinct game per system, ROM files only) ----
rc = sqlite3.connect(ROM_DB)
ph = ",".join("?" * len(ROM_EXTS))
q = ("SELECT system, game, GROUP_CONCAT(DISTINCT region) FROM roms "
     "WHERE ext IN (%s) AND lower(game) NOT IN (%s) AND game<>'' "
     "GROUP BY system, game" % (ph, ",".join("?" * len(MEDIA_GAMES))))
for system, game, regions in rc.execute(q, list(ROM_EXTS) + list(MEDIA_GAMES)):
    add(game, "emulation", system, system, (regions or ""))
rc.close()


# ---- store TSVs ----
def load_tsv(path, source):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line:
            continue
        sid, _, title = line.partition("\t")
        if title:
            add(title, source, source, sid)


load_tsv(OWN + "/steam_games.tsv", "steam")
load_tsv(OWN + "/epic_games.tsv", "epic")
load_tsv(OWN + "/gog_games.tsv", "gog")


# ---- write ----
if os.path.exists(OUT):
    os.remove(OUT)
con = sqlite3.connect(OUT)
cur = con.cursor()
cur.executescript("""
CREATE TABLE games (id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
  n_sources INTEGER, sources_summary TEXT,
  has_emulation INT, has_steam INT, has_gog INT, has_epic INT);
CREATE TABLE sources (game_id INTEGER, source TEXT, platform TEXT,
  source_id TEXT, title_raw TEXT, detail TEXT);
""")

for key, g in games.items():
    canonical = g["store_title"] or g["title"]
    srcs = g["sources"]
    kinds = {}
    for s in srcs:
        kinds.setdefault(s[0], set())
        if s[0] == "emulation":
            kinds[s[0]].add(s[1])
    parts = []
    if "emulation" in kinds:
        parts.append("emulation:" + ",".join(sorted(kinds["emulation"])))
    for st in ("steam", "gog", "epic"):
        if st in kinds:
            parts.append(st)
    summary = "; ".join(parts)
    cur.execute(
        "INSERT INTO games(canonical_title,norm_key,n_sources,sources_summary,"
        "has_emulation,has_steam,has_gog,has_epic) VALUES(?,?,?,?,?,?,?,?)",
        (canonical, key, len(srcs), summary,
         int("emulation" in kinds), int("steam" in kinds),
         int("gog" in kinds), int("epic" in kinds)))
    gid = cur.lastrowid
    cur.executemany(
        "INSERT INTO sources(game_id,source,platform,source_id,title_raw,detail)"
        " VALUES(?,?,?,?,?,?)", [(gid,) + s for s in srcs])

cur.executescript("""
CREATE INDEX ix_norm ON games(norm_key);
CREATE INDEX ix_title ON games(canonical_title);
CREATE INDEX ix_src_game ON sources(game_id);
CREATE INDEX ix_src_plat ON sources(platform);
""")
con.commit()

# summary to stderr
import sys
tot = cur.execute("SELECT COUNT(*) FROM games").fetchone()[0]
multi = cur.execute("SELECT COUNT(*) FROM games WHERE n_sources>1").fetchone()[0]
for label, col in (("emulation", "has_emulation"), ("steam", "has_steam"),
                   ("gog", "has_gog"), ("epic", "has_epic")):
    n = cur.execute("SELECT COUNT(*) FROM games WHERE %s=1" % col).fetchone()[0]
    print("# games with %-9s source: %d" % (label, n), file=sys.stderr)
print("# total unique games: %d (%d available from >1 source)" % (tot, multi),
      file=sys.stderr)
con.close()
