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
import sys
import json
import sqlite3

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config
from titlenorm import norm      # shared dedupe normalizer (honors config prefs)
from playnite import LIST_KINDS, SCALAR_KINDS

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


games = {}   # norm_key -> dict(title, store_title, sources=[])


games_attrs = {}     # norm_key -> {"src": [(source, source_id, record)], }
playnite_keys = set()  # norm_keys present in the Playnite library (provenance)


def add(title, source, platform, sid, detail=""):
    key = norm(title)
    if not key:
        return key
    g = games.get(key)
    if g is None:
        g = {"title": title, "store_title": None, "sources": []}
        games[key] = g
    # prefer a store title as the canonical (cleaner than tagged ROM names)
    if source not in ("emulation", "archive") and not g["store_title"]:
        g["store_title"] = title
    row = (source, platform, str(sid), title, detail)
    # dedup PROVIDER rows by (source, id) so a Playnite Steam entry enriches the
    # Steam pull instead of duplicating it — but keep every emulation/archive
    # variant (their source_id is a system/archive name, not a unique id).
    if source in ("emulation", "archive") or \
       (source, str(sid)) not in {(s[0], s[2]) for s in g["sources"]}:
        g["sources"].append(row)
    return key


def add_attrs(key, source, sid, record):
    """Attach a full Playnite-style attribute record to a game (for the
    attributes tables + export round-trip)."""
    if not key:
        return
    games_attrs.setdefault(key, {"src": []})["src"].append(
        (source, str(sid), record))


# ---- emulation (distinct game per system, ROM files only) ----
if config.source_enabled("emulation") and ROM_DB and os.path.exists(ROM_DB):
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


for _src in ("steam", "epic", "gog", "itch"):
    if config.source_enabled(_src):
        load_tsv(OWN + "/%s_games.tsv" % _src, _src)


# ---- crawled local archives (crawl.py -> process.py -> extracted) ----
CRAWL_DB = os.path.join(DIR, "crawl-index.sqlite")
if os.path.exists(CRAWL_DB):
    enabled = {a["name"] for a in config.archives_list(only_enabled=True)}
    cc = sqlite3.connect(CRAWL_DB)
    try:
        rows = cc.execute(
            "SELECT archive, system, title, region, version, revision, disc, "
            "flags FROM extracted")
        for archive, system, title, region, version, revision, disc, flags in rows:
            if archive in enabled and title:
                detail = " | ".join(x for x in (system, region, version, revision,
                                                disc, flags) if x)
                add(title, "archive", archive, archive, detail)
    except sqlite3.OperationalError:
        pass            # nothing processed yet (run crawl.py + process.py)
    cc.close()


# ---- Playnite library (playnite_bridge.ps1 -Export -> JSON) ----
# Playnite is NOT a source; it's a meta/consolidation layer. Each game maps to
# its UNDERLYING provider (steam/gog/ea/xbox/…), enriching that source's entry.
# "In Playnite" is recorded as provenance only (the in_playnite flag).
PN_JSON = config.get("playnite_import_json")
if config.source_enabled("playnite") and PN_JSON and os.path.exists(PN_JSON):
    try:
        pn = json.load(open(PN_JSON, encoding="utf-8"))
    except (ValueError, OSError):
        pn = []
    for rec in (pn or []):
        title = rec.get("name")
        if not title:
            continue
        provider = rec.get("source") or "manual"
        if provider == "playnite":          # no underlying library (manual entry)
            provider = "manual"
        sid = rec.get("source_id") or ""
        plats = rec.get("platforms") or []
        platform = plats[0] if plats else provider
        yr = rec.get("release_year") or ""
        comp = rec.get("completion_status") or ""
        detail = " | ".join(str(x) for x in (yr, comp) if x)
        key = add(title, provider, platform, sid, detail)
        if key:
            playnite_keys.add(key)
            add_attrs(key, provider, sid, rec)


# ---- write ----
if os.path.exists(OUT):
    os.remove(OUT)
con = sqlite3.connect(OUT)
cur = con.cursor()
cur.executescript("""
CREATE TABLE games (id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
  n_sources INTEGER, n_kinds INTEGER, sources_summary TEXT,
  has_emulation INT, has_steam INT, has_gog INT, has_epic INT, has_itch INT,
  has_archive INT, in_playnite INT);   -- in_playnite = provenance, NOT a source
CREATE TABLE sources (game_id INTEGER, source TEXT, platform TEXT,
  source_id TEXT, title_raw TEXT, detail TEXT);
-- Playnite-parity attributes:
CREATE TABLE source_attrs (game_id INTEGER, source TEXT, source_id TEXT,
  attrs_json TEXT);                       -- lossless per-provider record (export)
CREATE TABLE game_attributes (game_id INTEGER, kind TEXT, value TEXT);  -- queryable
""")

key_to_gid = {}
for key, g in games.items():
    canonical = g["store_title"] or g["title"]
    srcs = g["sources"]
    kinds = {}
    for s in srcs:
        kinds.setdefault(s[0], set())
        if s[0] in ("emulation", "archive"):    # grouped sources keep their platforms
            kinds[s[0]].add(s[1])
    parts = []
    for grp in ("emulation", "archive"):
        if grp in kinds:
            parts.append(grp + ":" + ",".join(sorted(kinds[grp])))
    # any other provider (steam/gog/epic/itch/ea/ubisoft/battlenet/xbox/…), dynamic
    for st in sorted(k for k in kinds if k not in ("emulation", "archive")):
        parts.append(st)
    summary = "; ".join(parts)
    cur.execute(
        "INSERT INTO games(canonical_title,norm_key,n_sources,n_kinds,sources_summary,"
        "has_emulation,has_steam,has_gog,has_epic,has_itch,has_archive,in_playnite) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (canonical, key, len(srcs), len(kinds), summary,
         int("emulation" in kinds), int("steam" in kinds),
         int("gog" in kinds), int("epic" in kinds), int("itch" in kinds),
         int("archive" in kinds), int(key in playnite_keys)))
    gid = cur.lastrowid
    key_to_gid[key] = gid
    cur.executemany(
        "INSERT INTO sources(game_id,source,platform,source_id,title_raw,detail)"
        " VALUES(?,?,?,?,?,?)", [(gid,) + s for s in srcs])

# ---- attribute tables (Playnite parity) ----
for key, data in games_attrs.items():
    gid = key_to_gid.get(key)
    if gid is None:
        continue
    agg = {}                       # kind -> set of values (multi) / single (scalar)
    for source, sid, rec in data["src"]:
        cur.execute("INSERT INTO source_attrs(game_id,source,source_id,attrs_json)"
                    " VALUES(?,?,?,?)",
                    (gid, source, sid, json.dumps(rec, ensure_ascii=False)))
        for k in LIST_KINDS:
            for v in (rec.get(k) or []):
                if v not in (None, ""):
                    agg.setdefault(k, set()).add(str(v))
        for k in SCALAR_KINDS:
            v = rec.get(k)
            if v not in (None, "", False):
                agg.setdefault(k, set()).add(str(v))
    rows = [(gid, k, v) for k, vs in agg.items() for v in sorted(vs)]
    cur.executemany("INSERT INTO game_attributes(game_id,kind,value) VALUES(?,?,?)",
                    rows)

cur.executescript("""
CREATE INDEX ix_norm ON games(norm_key);
CREATE INDEX ix_title ON games(canonical_title);
CREATE INDEX ix_src_game ON sources(game_id);
CREATE INDEX ix_src_plat ON sources(platform);
CREATE INDEX ix_sattr_game ON source_attrs(game_id);
CREATE INDEX ix_gattr_game ON game_attributes(game_id);
CREATE INDEX ix_gattr_kv ON game_attributes(kind, value);
""")
con.commit()

# summary to stderr
tot = cur.execute("SELECT COUNT(*) FROM games").fetchone()[0]
multi = cur.execute("SELECT COUNT(*) FROM games WHERE n_kinds>1").fetchone()[0]
for label, col in (("emulation", "has_emulation"), ("steam", "has_steam"),
                   ("gog", "has_gog"), ("epic", "has_epic"), ("itch", "has_itch"),
                   ("archive", "has_archive")):
    n = cur.execute("SELECT COUNT(*) FROM games WHERE %s=1" % col).fetchone()[0]
    print("# games with %-9s source: %d" % (label, n), file=sys.stderr)
_pn = cur.execute("SELECT COUNT(*) FROM games WHERE in_playnite=1").fetchone()[0]
if _pn:
    print("# games also in Playnite (provenance): %d" % _pn, file=sys.stderr)
print("# total unique games: %d (%d available from >1 source KIND)" % (tot, multi),
      file=sys.stderr)
con.close()
