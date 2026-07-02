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
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config
from titlenorm import norm      # shared dedupe normalizer (honors config prefs)
from playnite import LIST_KINDS, SCALAR_KINDS
from igdb import map_record as igdb_map   # IGDB metadata-provider record mapping

OWN = DIR                                   # store TSVs live next to the scripts
ROM_DB = config.get("roms_index_db")
OUT = config.get("library_db")


def _rom_indexes():
    """Every ROM index feeding the emulation source: the legacy single
    roms_index_db plus one per Connections ROM-folder manager
    (roms-index-mgr<id>.sqlite). Lets multiple ROM folders (local + devices) all
    become emulation without overwriting each other."""
    import glob
    paths = []
    if ROM_DB and os.path.exists(ROM_DB):
        paths.append(ROM_DB)
    for p in sorted(glob.glob(os.path.join(DATA, "roms-index-mgr*.sqlite"))):
        paths.append(p)
    return paths

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
launchbox_keys = set()  # norm_keys present in the LaunchBox library (provenance)


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
    # dedup PROVIDER rows by (source, id, platform) so a Playnite Steam entry
    # enriches the Steam pull instead of duplicating it — but keep every
    # emulation/archive variant (source_id = system/archive name, not unique) AND
    # every console a console title is owned on (same id, different platform:
    # e.g. an Xbox title on both xbox one + xbox series).
    if source in ("emulation", "archive") or \
       (source, str(sid), platform) not in {(s[0], s[2], s[1]) for s in g["sources"]}:
        g["sources"].append(row)
    return key


def add_attrs(key, source, sid, record, origin=None):
    """Attach a full Playnite-style attribute record to a game (for the
    attributes tables + export round-trip). `origin` names the importer that
    supplied the record (e.g. 'playnite'), used to attribute its tags."""
    if not key:
        return
    games_attrs.setdefault(key, {"src": []})["src"].append(
        (source, str(sid), record, origin))


# ---- carry-over: keep sources whose primary input isn't present on THIS host ----
# A consumer server (syncs only PC stores) has no roms-index / crawl-index / Playnite
# export, so a from-scratch rebuild would drop the emulation/archive/Playnite catalog
# produced on the Deck. Re-seed those source rows from the existing library first;
# metadata enrichment (run later from the local caches) re-derives their attributes.
# On the producer (all inputs present) every category is regenerated, so this is a
# no-op there.
_REGEN = set()
for _s in ("steam", "epic", "gog", "itch", "ea", "psn", "xbox"):
    if config.source_enabled(_s) and os.path.exists(OWN + "/%s_games.tsv" % _s):
        _REGEN.add(_s)
# Emulation is ADDITIVE by default: prior emulation games are carried over AND the
# current ROM indexes are merged in (union by norm_key). This prevents a device
# sync from silently dropping emulation games that only exist via carry-over (e.g.
# a Deck-produced library when the Deck isn't yet a synced ROM manager here). A
# true from-scratch purge (producer / removed roms should leave) uses --fresh.
if "--fresh" in sys.argv and config.source_enabled("emulation") and _rom_indexes():
    _REGEN.add("emulation")
if os.path.exists(os.path.join(DATA, "crawl-index.sqlite")):
    _REGEN.add("archive")
_pn_json = config.get("playnite_import_json")
_lb_json = config.get("launchbox_import_json")
_regen_pn = bool(config.source_enabled("playnite") and _pn_json and os.path.exists(_pn_json))
_regen_lb = bool(config.source_enabled("launchbox") and _lb_json and os.path.exists(_lb_json))

if os.path.exists(OUT):
    _prev = sqlite3.connect(OUT)
    try:
        for nk, in_pn, in_lb in _prev.execute(
                "SELECT norm_key, in_playnite, in_launchbox FROM games"):
            if in_pn and not _regen_pn:
                playnite_keys.add(nk)
            if in_lb and not _regen_lb:
                launchbox_keys.add(nk)
        for src, plat, sid, title, detail in _prev.execute(
                "SELECT source, platform, source_id, title_raw, detail FROM sources"):
            if src in _REGEN or not config.source_enabled(src):
                continue                       # regenerated fresh, or turned off
            add(title, src, plat, sid, detail or "")
    except sqlite3.OperationalError:
        pass                                   # no prior library / schema mismatch
    _prev.close()


# ---- emulation (distinct game per system, ROM files only) ----
# Read every ROM index (legacy single db + per-manager indexes); add() merges by
# norm_key, so the same game across folders becomes one emulation entry.
if config.source_enabled("emulation"):
    ph = ",".join("?" * len(ROM_EXTS))
    q = ("SELECT system, game, GROUP_CONCAT(DISTINCT region) FROM roms "
         "WHERE ext IN (%s) AND lower(game) NOT IN (%s) AND game<>'' "
         "GROUP BY system, game" % (ph, ",".join("?" * len(MEDIA_GAMES))))
    for _rom_db in _rom_indexes():
        rc = sqlite3.connect(_rom_db)
        try:
            for system, game, regions in rc.execute(q, list(ROM_EXTS)
                                                     + list(MEDIA_GAMES)):
                add(game, "emulation", system, system, (regions or ""))
        except sqlite3.OperationalError:
            pass
        rc.close()


# ---- store TSVs ----
def load_tsv(path, source):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line:
            continue
        parts = line.split("\t")
        sid = parts[0]
        title = parts[1] if len(parts) > 1 else ""
        # optional 3rd column = specific console/platform (psn/xbox emit it);
        # otherwise the platform is just the source label
        platform = parts[2] if len(parts) > 2 and parts[2] else source
        if title:
            add(title, source, platform, sid)


for _src in ("steam", "epic", "gog", "itch", "ea", "psn", "xbox"):
    if config.source_enabled(_src):
        load_tsv(OWN + "/%s_games.tsv" % _src, _src)


# ---- hand-added games (durable manual-games.sqlite; the library '+' add flow) ----
MANUAL_DB = os.path.join(DATA, "manual-games.sqlite")
if os.path.exists(MANUAL_DB):
    mgc = sqlite3.connect(MANUAL_DB)
    try:
        for title, src, plat, detail in mgc.execute(
                "SELECT title, source, platform, detail FROM manual_games"):
            if title:
                add(title, src or "manual", plat or (src or "manual"),
                    "manual:" + norm(title), detail or "")
    except sqlite3.OperationalError:
        pass
    mgc.close()


# ---- crawled local archives (crawl.py -> process.py -> extracted) ----
CRAWL_DB = os.path.join(DATA, "crawl-index.sqlite")
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
            add_attrs(key, provider, sid, rec, "playnite")


# ---- LaunchBox library (launchbox_import.py -> JSON) ----
# Same meta-layer treatment as Playnite: each game maps to its underlying provider;
# "in LaunchBox" is provenance only (the in_launchbox flag).
LB_JSON = config.get("launchbox_import_json")
if config.source_enabled("launchbox") and LB_JSON and os.path.exists(LB_JSON):
    try:
        lbgames = json.load(open(LB_JSON, encoding="utf-8"))
    except (ValueError, OSError):
        lbgames = []
    for rec in (lbgames or []):
        title = rec.get("name")
        if not title:
            continue
        provider = rec.get("source") or "manual"
        sid = rec.get("source_id") or ""
        plats = rec.get("platforms") or []
        platform = plats[0] if plats else provider
        detail = str(rec.get("release_year") or "")
        key = add(title, provider, platform, sid, detail)
        if key:
            launchbox_keys.add(key)
            add_attrs(key, provider, sid, rec, "launchbox")


# ---- write ----
if os.path.exists(OUT):
    os.remove(OUT)
con = sqlite3.connect(OUT)
cur = con.cursor()
cur.executescript("""
CREATE TABLE games (id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
  n_sources INTEGER, n_kinds INTEGER, sources_summary TEXT,
  has_emulation INT, has_steam INT, has_gog INT, has_epic INT, has_itch INT,
  has_archive INT, in_playnite INT, in_launchbox INT);  -- *_in flags = provenance, NOT sources
CREATE TABLE sources (game_id INTEGER, source TEXT, platform TEXT,
  source_id TEXT, title_raw TEXT, detail TEXT);
-- Playnite-parity attributes:
CREATE TABLE source_attrs (game_id INTEGER, source TEXT, source_id TEXT,
  attrs_json TEXT);                       -- lossless per-provider record (export)
CREATE TABLE game_attributes (game_id INTEGER, kind TEXT, value TEXT,
  origin TEXT DEFAULT '');  -- origin = comma-joined source(s): steam/igdb/ai/…
CREATE TABLE metadata_links (game_id INTEGER, provider TEXT, provider_id TEXT,
  slug TEXT, url TEXT);                    -- canonical ids from metadata providers
CREATE TABLE game_tags (game_id INTEGER, tag TEXT, origin TEXT);  -- origin: playnite/ludodex/…
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
        "has_emulation,has_steam,has_gog,has_epic,has_itch,has_archive,in_playnite,"
        "in_launchbox) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (canonical, key, len(srcs), len(kinds), summary,
         int("emulation" in kinds), int("steam" in kinds),
         int("gog" in kinds), int("epic" in kinds), int("itch" in kinds),
         int("archive" in kinds), int(key in playnite_keys),
         int(key in launchbox_keys)))
    gid = cur.lastrowid
    key_to_gid[key] = gid
    cur.executemany(
        "INSERT INTO sources(game_id,source,platform,source_id,title_raw,detail)"
        " VALUES(?,?,?,?,?,?)", [(gid,) + s for s in srcs])

# ---- attribute tables (Playnite parity) ----
# Tags are handled apart from other attribute kinds: we keep each tag's ORIGIN
# (which importer supplied it) in game_tags, while still exposing tags as a normal
# "tags" attribute so search/filter treat them like any other kind.
tag_map = {}                        # game_id -> {tag: set(origins)}
for key, data in games_attrs.items():
    gid = key_to_gid.get(key)
    if gid is None:
        continue
    agg = {}                       # kind -> {value: set(source origins)}
    for source, sid, rec, origin in data["src"]:
        cur.execute("INSERT INTO source_attrs(game_id,source,source_id,attrs_json)"
                    " VALUES(?,?,?,?)",
                    (gid, source, sid, json.dumps(rec, ensure_ascii=False)))
        for k in LIST_KINDS:
            for v in (rec.get(k) or []):
                if v in (None, ""):
                    continue
                if k == "tags":
                    tag_map.setdefault(gid, {}).setdefault(str(v), set()).add(
                        origin or "import")
                else:
                    agg.setdefault(k, {}).setdefault(str(v), set()).add(source)
        for k in SCALAR_KINDS:
            v = rec.get(k)
            if v not in (None, "", False):
                agg.setdefault(k, {}).setdefault(str(v), set()).add(source)
    rows = [(gid, k, v, ",".join(sorted(o))) for k, vmap in agg.items()
            for v, o in sorted(vmap.items())]
    cur.executemany("INSERT INTO game_attributes(game_id,kind,value,origin) "
                    "VALUES(?,?,?,?)", rows)

# ---- user-defined tags (origin 'ludodex', durable in tags.sqlite) ----
TAGS_DB = os.path.join(DATA, "tags.sqlite")
if os.path.exists(TAGS_DB):
    tc = sqlite3.connect(TAGS_DB)
    try:
        for nk, tag in tc.execute("SELECT norm_key, tag FROM user_tags"):
            gid = key_to_gid.get(nk)
            if gid is not None and tag:
                tag_map.setdefault(gid, {}).setdefault(str(tag), set()).add("ludodex")
    except sqlite3.OperationalError:
        pass
    tc.close()

# ---- Steam community tags (origin 'steam', fetched cache from SteamSpy) ----
STEAM_TAGS_DB = os.path.join(DATA, "steam-tags.sqlite")
if config.metadata_enabled("steamspy") and os.path.exists(STEAM_TAGS_DB):
    stc = sqlite3.connect(STEAM_TAGS_DB)
    try:
        for nk, tag in stc.execute("SELECT norm_key, tag FROM steam_tags"):
            gid = key_to_gid.get(nk)
            if gid is not None and tag:
                tag_map.setdefault(gid, {}).setdefault(str(tag), set()).add("steam")
    except sqlite3.OperationalError:
        pass
    stc.close()

# write game_tags (per origin) + expose tags as a normal attribute (deduped value)
_gt_rows, _ga_rows = [], []
for gid, tags in tag_map.items():
    for tag, origins in tags.items():
        for o in sorted(origins):
            _gt_rows.append((gid, tag, o))
        _ga_rows.append((gid, "tags", tag, ",".join(sorted(origins))))
cur.executemany("INSERT INTO game_tags(game_id,tag,origin) VALUES(?,?,?)", _gt_rows)
cur.executemany("INSERT INTO game_attributes(game_id,kind,value,origin) "
                "VALUES(?,?,?,?)", _ga_rows)

# ---- IGDB enrichment (metadata provider, fill-gaps only) ----
# IGDB is NOT a source: it only fills attribute KINDS a game still lacks. If a
# game already has any value for a kind (from a store / Playnite), IGDB leaves
# that kind untouched, so owned-source data is always authoritative.
# kinds each game already has (owned-source/Playnite) — shared by every metadata
# provider so they only fill gaps, in registry order (IGDB then ScreenScraper).
have = {}                           # game_id -> set(kinds already populated)
for gid, kind in cur.execute("SELECT game_id, kind FROM game_attributes"):
    have.setdefault(gid, set()).add(kind)

CACHE_DB = os.path.join(DATA, "metadata-cache.sqlite")
n_link = n_attr = 0
if config.metadata_enabled("igdb") and os.path.exists(CACHE_DB):
    mc = sqlite3.connect(CACHE_DB)
    try:
        rows = mc.execute(
            "SELECT r.norm_key, r.igdb_id, r.slug, m.payload_json "
            "FROM igdb_resolution r JOIN igdb_meta m ON m.igdb_id=r.igdb_id "
            "WHERE r.igdb_id>0").fetchall()
    except sqlite3.OperationalError:
        rows = []                   # cache exists but enrich hasn't populated it
    mc.close()
    for nk, iid, slug, payload in rows:
        gid = key_to_gid.get(nk)
        if gid is None:
            continue
        url = "https://www.igdb.com/games/%s" % slug if slug else None
        cur.execute("INSERT INTO metadata_links(game_id,provider,provider_id,"
                    "slug,url) VALUES(?,?,?,?,?)",
                    (gid, "igdb", str(iid), slug, url))
        n_link += 1
        try:
            rec = json.loads(payload)
        except ValueError:
            continue
        existing = have.setdefault(gid, set())
        new_rows = []
        for kind, val in igdb_map(rec).items():
            if kind in existing:                 # fill-gaps: don't touch it
                continue
            for v in (val if isinstance(val, list) else [val]):
                if v not in (None, ""):
                    new_rows.append((gid, kind, str(v), "igdb"))
            existing.add(kind)
        if new_rows:
            cur.executemany("INSERT INTO game_attributes(game_id,kind,value,origin) "
                            "VALUES(?,?,?,?)", new_rows)
            n_attr += len(new_rows)

# ---- ScreenScraper enrichment (metadata provider, fill-gaps; emulation) ----
# One scrape per game yields metadata + media; here we merge the metadata (media
# is ingested into the media index separately). Fill-gaps after IGDB, so owned
# and IGDB data still win.
SS_CACHE = os.path.join(DATA, "screenscraper-cache.sqlite")
ss_link = ss_attr = 0
if config.metadata_enabled("screenscraper") and os.path.exists(SS_CACHE):
    from screenscraper import extract_metadata as ss_map
    sc = sqlite3.connect(SS_CACHE)
    try:
        ss_rows = sc.execute("SELECT norm_key, ss_id, payload_json FROM ss_game "
                             "WHERE status='ok'").fetchall()
    except sqlite3.OperationalError:
        ss_rows = []
    sc.close()
    linked = set()
    for nk, ss_id, payload in ss_rows:
        gid = key_to_gid.get(nk)
        if gid is None or not payload:
            continue
        if ss_id and (gid, ss_id) not in linked:
            cur.execute("INSERT INTO metadata_links(game_id,provider,provider_id,"
                        "slug,url) VALUES(?,?,?,?,?)",
                        (gid, "screenscraper", str(ss_id), None,
                         "https://www.screenscraper.fr/gameinfos.php?gameid=%s"
                         % ss_id))
            linked.add((gid, ss_id))
            ss_link += 1
        try:
            jeu = json.loads(payload)
        except ValueError:
            continue
        existing = have.setdefault(gid, set())
        new_rows = []
        for kind, val in ss_map(jeu).items():
            if kind == "name" or kind in existing:      # fill-gaps; name isn't an attr
                continue
            for v in (val if isinstance(val, list) else [val]):
                if v not in (None, ""):
                    new_rows.append((gid, kind, str(v), "screenscraper"))
            existing.add(kind)
        if new_rows:
            cur.executemany("INSERT INTO game_attributes(game_id,kind,value,origin) "
                            "VALUES(?,?,?,?)", new_rows)
            ss_attr += len(new_rows)

# ---- AI metadata supplement (accepted findings, fill-gaps, LOWEST precedence) ----
# Only attributes the user accepted in the metadata review, and only for kinds no
# owned/provider source supplied — AI never overrides real data.
ai_attr = 0
try:
    import aimeta
    for nk, attrs in aimeta.accepted_supplements().items():
        gid = key_to_gid.get(nk)
        if gid is None:
            continue
        existing = have.setdefault(gid, set())
        new_rows = []
        for kind, val in attrs.items():
            if kind in existing:
                continue
            for v in (val if isinstance(val, list) else [val]):
                if v not in (None, ""):
                    new_rows.append((gid, kind, str(v), "ai"))
            existing.add(kind)
        if new_rows:
            cur.executemany("INSERT INTO game_attributes(game_id,kind,value,origin) "
                            "VALUES(?,?,?,?)", new_rows)
            ai_attr += len(new_rows)
except Exception as e:                             # never let AI supplements break a build
    print("# AI supplement merge skipped: %s" % e)
if ai_attr:
    print("# AI supplement       attrs: %d (accepted findings, fill-gaps)" % ai_attr)

cur.executescript("""
CREATE INDEX ix_norm ON games(norm_key);
CREATE INDEX ix_title ON games(canonical_title);
CREATE INDEX ix_src_game ON sources(game_id);
CREATE INDEX ix_src_plat ON sources(platform);
CREATE INDEX ix_sattr_game ON source_attrs(game_id);
CREATE INDEX ix_gattr_game ON game_attributes(game_id);
CREATE INDEX ix_gattr_kv ON game_attributes(kind, value);
CREATE INDEX ix_mlink_game ON metadata_links(game_id);
CREATE INDEX ix_gtag_game ON game_tags(game_id);
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
if n_link:
    print("# IGDB: linked %d games, +%d attribute rows (fill-gaps)"
          % (n_link, n_attr), file=sys.stderr)
if ss_link:
    print("# ScreenScraper: linked %d games, +%d attribute rows (fill-gaps)"
          % (ss_link, ss_attr), file=sys.stderr)
print("# total unique games: %d (%d available from >1 source KIND)" % (tot, multi),
      file=sys.stderr)
con.close()
