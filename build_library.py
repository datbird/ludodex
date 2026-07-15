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
import json
import sqlite3

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config
from titlenorm import norm      # shared dedupe normalizer (honors config prefs)
import merges                   # durable user merges — fold duplicates into one
import splits                   # durable "peel apart" — split a merged-away game out
import console_eras             # hardware timelines — catch era-impossible merges
_MERGE_ALIAS = merges.alias_map()
_PEEL = splits.overrides()      # {(source, source_id): (to_key, to_title)}


def _mkey(title):
    """Dedupe key for a title, with any user 'Fix duplication' merge applied so a
    merged-away entry folds into its canonical one on every rebuild."""
    k = norm(title)
    return _MERGE_ALIAS.get(k, k)
from playnite import LIST_KINDS, SCALAR_KINDS
from igdb import map_record as igdb_map   # IGDB metadata-provider record mapping
from media import norm_system             # canonical console labels (gb->gameboy, …)

# Store ownership TSVs live in the DURABLE data dir (not next to the scripts, which
# is an ephemeral image layer): otherwise store ownership only survives via catalog
# carry-over and a rebuild-from-scratch silently drops every store's games. Fall back
# to the legacy /app location if that's the only place TSVs exist (e.g. update.sh,
# which writes beside the scripts) — migrated to /data on the next in-app sync.
_STORE_SRCS = ("steam", "gog", "epic", "itch", "ea", "psn", "xbox")
OWN = DATA
if not any(os.path.exists(os.path.join(DATA, "%s_games.tsv" % s)) for s in _STORE_SRCS) \
        and any(os.path.exists(os.path.join(DIR, "%s_games.tsv" % s)) for s in _STORE_SRCS):
    OWN = DIR
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

# OS vs. device split: the store PLATFORM is the store/console identity (xbox,
# steam, snes…), kept separate from what a title RUNS ON. The Xbox importer emits
# a device per row (windows / xbox one / xbox series); add() folds those into one
# 'xbox' source (device list durably in `detail`) and they're split into `os`
# (PC operating systems) and `device` (consoles) attributes at write time.
OS_VALUES = {"windows", "win", "linux", "mac", "macos", "osx"}

# ---- per-platform library entries (DESIGN §11) ----
# The library unit is one entry per (game, PLATFORM). The platform is inherent to
# the source: every PC storefront folds to a single 'pc' platform (so Steam + GOG
# of the same game dedupe into one entry), while console networks / emulation keep
# their distinct platform (so a game splits per console). Xbox is the only judgment
# call — user setting `xbox_platform` picks 'xbox' (default) or 'pc'.
PC_STORES = {"steam", "gog", "epic", "itch", "ea"}


def _entry_platform(source, platform):
    """The platform bucket an owned/wanted row lands in — the entry identity axis."""
    if source in PC_STORES:
        return "pc"
    if source == "xbox":
        return "pc" if config.get("xbox_platform") == "pc" else "xbox"
    if source in ("emulation", "archive") and platform:
        # canonicalize console labels so the same console under different ROM-manager
        # names (genesis/sega genesis, gb/gameboy, tg16/turbo gfx) is ONE platform,
        # not several — same normalizer the media index uses, so entry.platform lines
        # up with media.system for siloing.
        return norm_system(platform)
    return platform or source


base_present = set()   # every base norm_key that has an owned/wanted entry (for the
                       # wishlist "already owned?" check, which is title-level)


def add(title, source, platform, sid, detail="", state="have"):
    # "Peel apart": a specific source row the user split off a merged entry goes to
    # its OWN key + title, overriding the natural title-derived key. Applied first so
    # the row lands on the peeled-off game on every rebuild.
    peel = _PEEL.get((source, str(sid)))
    if peel:
        title = peel[1] or title
        key = _MERGE_ALIAS.get(peel[0], peel[0])
    else:
        key = _mkey(title)
    if not key:
        return key
    # Xbox: keep the store identity ('xbox') on the platform and carry the actual
    # device(s) a title runs on (windows / xbox one / xbox series) as a comma-list
    # in `detail` — durable across carry-over rebuilds, later split into os/device
    # attributes. The device arrives as the platform (fresh per-device TSV row) or
    # already in detail (a carried-over, already-normalised source).
    # entry platform = the identity axis; the row's stored platform becomes it too
    # (every row in an entry shares one platform). Xbox still carries its device
    # list in `detail`, split into os/device attributes at write time.
    ep = _entry_platform(source, platform)
    xbox_devs = set()
    if source == "xbox":
        raw = detail if platform in ("", "xbox") else platform
        xbox_devs = {d.strip() for d in str(raw).split(",")
                     if d.strip() and d.strip() != "xbox"}
        detail = ",".join(sorted(xbox_devs))
    ekey = (key, ep)                     # one library entry per (game, platform)
    base_present.add(key)
    g = games.get(ekey)
    if g is None:
        g = {"title": title, "store_title": None, "sources": []}
        games[ekey] = g
    # prefer a store title as the canonical (cleaner than tagged ROM names). Only
    # a *have* store source names the game — a want shouldn't rename an owned one.
    if source not in ("emulation", "archive") and state == "have" and not g["store_title"]:
        g["store_title"] = title
    # dedup source rows by (source, id) WITHIN the entry — the platform is the
    # entry's, so a Playnite Steam entry enriches the Steam pull instead of
    # duplicating it, and a ROM-index re-read won't append an identical row.
    # Distinct platforms are now distinct ENTRIES, so "every platform a title is
    # on" is preserved as separate entries sharing the base norm_key.
    dk = (source, str(sid))
    for i, s in enumerate(g["sources"]):
        if (s[0], s[2]) == dk:
            if xbox_devs:                            # union devices into detail
                had = {d.strip() for d in (s[4] or "").split(",") if d.strip()}
                g["sources"][i] = s = s[:4] + (",".join(sorted(had | xbox_devs)),) + s[5:]
            if state == "have" and s[5] != "have":   # have wins over want
                g["sources"][i] = s[:5] + ("have",)
            return ekey
    g["sources"].append((source, ep, str(sid), title, detail, state))
    return ekey


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

_prev_wanted = {}          # store -> [(norm_key, title, store_id)] carried from the old DB
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
    try:                                       # wishlist-wanted games (may be absent in older DBs)
        for nk, title, store, sid in _prev.execute(
                "SELECT g.norm_key, g.canonical_title, w.store, w.store_id "
                "FROM games g JOIN wanted w ON w.game_id=g.id WHERE g.wanted=1"):
            _prev_wanted.setdefault(store, []).append((nk, title, sid))
    except sqlite3.OperationalError:
        pass
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
        # optional 3rd column = specific console/platform (psn/xbox emit it);
        # otherwise the platform is just the source label. Xbox's device value is
        # normalised into the store identity + os/device attributes inside add().
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


# ---- durable per-format ownership facts (ownership.sqlite): manual physical
#      ownership + per-platform wants that coexist with what you already own ----
try:
    import ownership as _ownership
    for _nk, _t, _src, _plat, _state, _note in _ownership.all_facts(DATA):
        if _t:
            add(_t, _src, _plat or _src, "own:%s:%s" % (_src, _plat), _note, _state)
except Exception:
    pass


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
            playnite_keys.add(key[0])       # in_playnite is title-level provenance
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
            launchbox_keys.add(key[0])      # in_launchbox is title-level provenance
            add_attrs(key, provider, sid, rec, "launchbox")


# ---- wishlists (Discover "Wanted"): games you want but don't own ----
# Loaded LAST, after every owned source, so we can drop anything already owned
# (its norm_key is already in `games`). A wanted game becomes a catalog entry with
# NO owned source (wanted=1); the moment you own it, the match here removes it.
wanted = {}          # norm_key -> {"title": str, "stores": [(store, store_id, title_raw)]}


def load_wishlist(path, store):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line:
            continue
        parts = line.split("\t")
        sid = parts[0]
        title = parts[1] if len(parts) > 1 else ""
        if not title:
            continue
        key = _mkey(title)
        if not key or key in base_present:    # already owned -> not "wanted"
            continue
        w = wanted.setdefault(key, {"title": title, "stores": []})
        if (store, str(sid)) not in {(s[0], s[1]) for s in w["stores"]}:
            w["stores"].append((store, str(sid), title))


def carry_wishlist(store):
    # re-seed a store's wanted games from the old DB when its fresh TSV is absent,
    # so a rebuild (e.g. after a container recreate wipes the ephemeral TSVs) doesn't
    # silently drop them — the same durability owned sources get via carry-over.
    for nk, title, sid in _prev_wanted.get(store, []):
        nk = _MERGE_ALIAS.get(nk, nk)          # fold a merged-away wanted entry
        if not nk or nk in base_present:        # now owned -> no longer "wanted"
            continue
        w = wanted.setdefault(nk, {"title": title, "stores": []})
        if (store, str(sid)) not in {(s[0], s[1]) for s in w["stores"]}:
            w["stores"].append((store, str(sid), title))


for _ws in ("steam", "gog"):
    if not config.source_enabled(_ws):
        continue
    path = OWN + "/%s_wishlist.tsv" % _ws
    if os.path.exists(path):
        load_wishlist(path, _ws)               # fresh pull this run
    else:
        carry_wishlist(_ws)                    # keep prior wanted alive


# ---- write ----
if os.path.exists(OUT):
    os.remove(OUT)
con = sqlite3.connect(OUT)
cur = con.cursor()
cur.executescript("""
CREATE TABLE games (id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
  platform TEXT, entry_key TEXT,   -- one entry per (norm_key, platform); entry_key = norm_key@platform
  n_sources INTEGER, n_kinds INTEGER, sources_summary TEXT,
  has_emulation INT, has_steam INT, has_gog INT, has_epic INT, has_itch INT,
  has_archive INT, in_playnite INT, in_launchbox INT,
  wanted INT DEFAULT 0);  -- wanted=1: a wishlist-only entry (no owned source)
CREATE TABLE sources (game_id INTEGER, source TEXT, platform TEXT,
  source_id TEXT, title_raw TEXT, detail TEXT, state TEXT DEFAULT 'have');
  -- state: 'have' (owned via this source) | 'want' (per-format wish)
-- store-wishlist provenance for wanted games (which store(s) they're wanted from)
CREATE TABLE wanted (game_id INTEGER, store TEXT, store_id TEXT, title_raw TEXT);
-- Playnite-parity attributes:
CREATE TABLE source_attrs (game_id INTEGER, source TEXT, source_id TEXT,
  attrs_json TEXT);                       -- lossless per-provider record (export)
CREATE TABLE game_attributes (game_id INTEGER, kind TEXT, value TEXT,
  origin TEXT DEFAULT '');  -- origin = comma-joined source(s): steam/igdb/ai/…
CREATE TABLE metadata_links (game_id INTEGER, provider TEXT, provider_id TEXT,
  slug TEXT, url TEXT);                    -- canonical ids from metadata providers
CREATE TABLE game_tags (game_id INTEGER, tag TEXT, origin TEXT);  -- origin: playnite/ludodex/…
""")

key_to_gid = {}                 # (base_key, platform) -> gid   (per-entry attrs)
base_to_gids = {}               # base_key -> [gid,...]         (title-level metadata fan-out)
_wtotal = len(games) + len(wanted)      # for the sync UI's live "N/total games" count
_wrote = 0
for (base, plat), g in games.items():
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
    # a game is owned if ANY source is 'have'; a pure want-only game (e.g. only a
    # manual "want the ROM" fact) is wanted=1 so it lands in the Wanted view.
    owned = any(s[5] == "have" for s in srcs)
    cur.execute(
        "INSERT INTO games(canonical_title,norm_key,platform,entry_key,n_sources,n_kinds,"
        "sources_summary,has_emulation,has_steam,has_gog,has_epic,has_itch,has_archive,"
        "in_playnite,in_launchbox,wanted) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (canonical, base, plat, "%s@%s" % (base, plat), len(srcs), len(kinds), summary,
         int("emulation" in kinds), int("steam" in kinds),
         int("gog" in kinds), int("epic" in kinds), int("itch" in kinds),
         int("archive" in kinds), int(base in playnite_keys),
         int(base in launchbox_keys), 0 if owned else 1))
    gid = cur.lastrowid
    key_to_gid[(base, plat)] = gid
    base_to_gids.setdefault(base, []).append(gid)
    cur.executemany(
        "INSERT INTO sources(game_id,source,platform,source_id,title_raw,detail,state)"
        " VALUES(?,?,?,?,?,?,?)", [(gid,) + s for s in srcs])
    _wrote += 1
    if _wrote % 200 == 0:
        print("PROG\t%d\t%d\t%s\tcatalog" % (_wrote, _wtotal, base), flush=True)

# ---- wanted (wishlist-only) games: catalog rows with no owned source, wanted=1 ----
for key, w in wanted.items():
    stores = sorted({s[0] for s in w["stores"]})
    plat = "pc"                              # store wishlists (steam/gog) are PC
    cur.execute(
        "INSERT INTO games(canonical_title,norm_key,platform,entry_key,n_sources,n_kinds,"
        "sources_summary,has_emulation,has_steam,has_gog,has_epic,has_itch,has_archive,"
        "in_playnite,in_launchbox,wanted) VALUES(?,?,?,?,0,0,?,0,0,0,0,0,0,0,0,1)",
        (w["title"], key, plat, "%s@%s" % (key, plat),
         "wishlist:" + ",".join(stores)))
    gid = cur.lastrowid
    key_to_gid[(key, plat)] = gid
    base_to_gids.setdefault(key, []).append(gid)
    cur.executemany("INSERT INTO wanted(game_id,store,store_id,title_raw) "
                    "VALUES(?,?,?,?)", [(gid,) + s for s in w["stores"]])
    _wrote += 1
    if _wrote % 200 == 0:
        print("PROG\t%d\t%d\t%s\tcatalog" % (_wrote, _wtotal, key), flush=True)

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

# ---- os / device attributes (split out of the Xbox store platform column) ----
# Read back the just-written Xbox sources (detail = comma-joined devices) and
# classify each value as an OS (windows/mac/linux) or a device (xbox one/series).
_od_rows = []
for _gid, _detail in cur.execute(
        "SELECT game_id, detail FROM sources WHERE source='xbox' "
        "AND detail IS NOT NULL AND detail!=''").fetchall():
    for _dv in (_detail or "").split(","):
        _dv = _dv.strip()
        if not _dv:
            continue
        _kind = "os" if _dv.lower() in OS_VALUES else "device"
        _od_rows.append((_gid, _kind, _dv, "xbox"))
cur.executemany("INSERT INTO game_attributes(game_id,kind,value,origin) "
                "VALUES(?,?,?,?)", _od_rows)

# ---- user-defined tags (origin 'ludodex', durable in tags.sqlite) ----
TAGS_DB = os.path.join(DATA, "tags.sqlite")
if os.path.exists(TAGS_DB):
    tc = sqlite3.connect(TAGS_DB)
    try:
        for nk, tag in tc.execute("SELECT norm_key, tag FROM user_tags"):
            if not tag:
                continue
            for gid in base_to_gids.get(nk, ()):   # applies to every platform entry
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
            if not tag:
                continue
            for gid in base_to_gids.get(nk, ()):   # applies to every platform entry
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

# ---- IGDB + ScreenScraper enrichment (metadata providers) ----
# Providers are NOT sources: they only fill attribute KINDS a game lacks from an
# owned source (owned data stays authoritative). BETWEEN providers, list-valued
# kinds (genres, themes, developers, …) are UNIONED per value — each value keeps
# EVERY provider that supplied it (origin = comma-joined), so a genre from IGDB and
# a genre only ScreenScraper has both survive, and a shared genre is credited to
# both. Scalar kinds (release date, description, …) take the first provider (IGDB);
# the AI adjudication overlay re-points a scalar where providers disagree.
have = {}                           # game_id -> set(kinds already populated (owned))
for gid, kind in cur.execute("SELECT game_id, kind FROM game_attributes"):
    have.setdefault(gid, set()).add(kind)

p_multi = {}    # gid -> kind -> {value: set(origins)}   list-valued (unioned)
p_scalar = {}   # gid -> kind -> [value, set(origins)]   single-valued (first wins)


def _accum(gid, kind, val, origin):
    if isinstance(val, list):
        d = p_multi.setdefault(gid, {}).setdefault(kind, {})
        for v in val:
            if v not in (None, ""):
                d.setdefault(str(v), set()).add(origin)
    elif val not in (None, ""):
        cur_s = p_scalar.setdefault(gid, {}).get(kind)
        if cur_s is None:
            p_scalar[gid][kind] = [str(val), {origin}]
        elif cur_s[0] == str(val):           # same value from another provider
            cur_s[1].add(origin)


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
        gids = base_to_gids.get(nk)          # metadata is title-level → every platform entry
        if not gids:
            continue
        try:
            rec = json.loads(payload)
        except ValueError:
            continue
        url = "https://www.igdb.com/games/%s" % slug if slug else None
        name = (rec.get("name") or "").strip()
        mapped = igdb_map(rec)
        for gid in gids:
            cur.execute("INSERT INTO metadata_links(game_id,provider,provider_id,"
                        "slug,url) VALUES(?,?,?,?,?)",
                        (gid, "igdb", str(iid), slug, url))
            n_link += 1
            # rename-on-match: adopt the provider's official title for ROM/archive-only
            # games (their title is just the filename, e.g. "0001 - F-Zero"). Store-owned
            # games already have clean titles, so leave those alone. norm_key is unchanged
            # (stays the dedupe key), and the ROM filename stays in the source's title_raw.
            if name:
                cur.execute(
                    "UPDATE games SET canonical_title=? WHERE id=? AND NOT EXISTS("
                    "SELECT 1 FROM sources WHERE game_id=? AND source NOT IN "
                    "('emulation','archive'))", (name, gid, gid))
            for kind, val in mapped.items():
                _accum(gid, kind, val, "igdb")

# ScreenScraper (emulation metadata; one scrape yields metadata + media, media is
# indexed separately). Unioned with IGDB per the merge above.
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
        gids = base_to_gids.get(nk)          # metadata is title-level → every platform entry
        if not gids or not payload:
            continue
        try:
            jeu = json.loads(payload)
        except ValueError:
            continue
        mapped = ss_map(jeu)
        for gid in gids:
            if ss_id and (gid, ss_id) not in linked:
                cur.execute("INSERT INTO metadata_links(game_id,provider,provider_id,"
                            "slug,url) VALUES(?,?,?,?,?)",
                            (gid, "screenscraper", str(ss_id), None,
                             "https://www.screenscraper.fr/gameinfos.php?gameid=%s"
                             % ss_id))
                linked.add((gid, ss_id))
                ss_link += 1
            for kind, val in mapped.items():
                if kind != "name":                  # 'name' isn't an attribute
                    _accum(gid, kind, val, "screenscraper")

# insert unioned provider attributes — skip any kind an owned source already filled
_prov_rows = []
for gid in set(p_multi) | set(p_scalar):
    filled = have.setdefault(gid, set())
    for kind, vmap in p_multi.get(gid, {}).items():
        if kind in filled:
            continue
        for v, origins in vmap.items():
            _prov_rows.append((gid, kind, v, ",".join(sorted(origins))))
        filled.add(kind)
    for kind, (v, origins) in p_scalar.get(gid, {}).items():
        if kind in filled:
            continue
        _prov_rows.append((gid, kind, v, ",".join(sorted(origins))))
        filled.add(kind)
if _prov_rows:
    cur.executemany("INSERT INTO game_attributes(game_id,kind,value,origin) "
                    "VALUES(?,?,?,?)", _prov_rows)
n_attr = len(_prov_rows)

# ---- AI metadata supplement (accepted findings, fill-gaps, LOWEST precedence) ----
# Only attributes the user accepted in the metadata review, and only for kinds no
# owned/provider source supplied — AI never overrides real data.
ai_attr = 0
try:
    import aimeta
    for nk, attrs in aimeta.accepted_supplements().items():
        for gid in base_to_gids.get(nk, ()):   # title-level → every platform entry
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

# Indexes the split + (year) passes below depend on — the full set is (re)created
# after them, but these per-game lookups would full-scan sources/game_attributes
# (573k+ rows) without them, so build them first.
cur.execute("CREATE INDEX IF NOT EXISTS ix_src_game ON sources(game_id)")
cur.execute("CREATE INDEX IF NOT EXISTS ix_gattr_game ON game_attributes(game_id)")

# ---- platform-era split: SUPERSEDED by the per-platform entry model (DESIGN §11).
# A modern store game and an era-impossible ROM now land in DIFFERENT entries (pc vs
# the console) by construction, so there's nothing to peel within an entry. What
# remains — two genuinely different games that share a norm_key (a ~1994 Game Boy
# "Uno" vs the 2016 Steam "UNO") still share a base_key and thus title-level metadata
# — is handled by reassigning the retro entry a distinct base_key (follow-up task);
# tracked so the Uno/Bubsy separation is preserved under the new model.

# ---- (year) disambiguation: when 2+ games share a title but are DIFFERENT release
# years (remakes / re-releases — Uno 2006 vs 2016, Tomb Raider 1996 vs 2013), append
# "(year)" to each so they read distinctly, the way stores do. Same-title/same-year
# is left untouched (those are true duplicates, folded by dedupe elsewhere). Runs on
# the post-enrichment build, so store games (year only known from IGDB) get labeled;
# idempotent — an existing trailing "(YYYY)" is stripped before re-deriving.
_YR_SUFFIX = re.compile(r"\s*\(\d{4}\)\s*$")
_title_year = {}                    # gid -> release year (release_year, else release_date)
for _gid, _val in cur.execute(
        "SELECT game_id, value FROM game_attributes "
        "WHERE kind IN ('release_year', 'release_date')"):
    _m = re.search(r"\d{4}", str(_val or ""))
    if _m:
        _title_year.setdefault(_gid, int(_m.group()))
_by_title = {}                      # base title (casefold) -> [(gid, base_title)]
for _gid, _title in cur.execute("SELECT id, canonical_title FROM games"):
    _base = _YR_SUFFIX.sub("", _title or "").strip()
    if _base:
        _by_title.setdefault(_base.casefold(), []).append((_gid, _base))
_relabel = []
for _members in _by_title.values():
    if len(_members) < 2:
        continue
    _years = {_title_year.get(g) for g, _ in _members if _title_year.get(g)}
    if len(_years) < 2:             # all one year, or unknown — not a remake split
        continue
    for _gid, _base in _members:
        _y = _title_year.get(_gid)
        if _y:
            _relabel.append(("%s (%d)" % (_base, _y), _gid))
if _relabel:
    cur.executemany("UPDATE games SET canonical_title=? WHERE id=?", _relabel)
    print("# (year) disambiguation: relabeled %d remake title(s)" % len(_relabel))

cur.executescript("""
CREATE INDEX IF NOT EXISTS ix_norm ON games(norm_key);
CREATE INDEX IF NOT EXISTS ix_title ON games(canonical_title);
CREATE INDEX IF NOT EXISTS ix_src_game ON sources(game_id);
CREATE INDEX IF NOT EXISTS ix_src_plat ON sources(platform);
CREATE INDEX IF NOT EXISTS ix_sattr_game ON source_attrs(game_id);
CREATE INDEX IF NOT EXISTS ix_gattr_game ON game_attributes(game_id);
CREATE INDEX IF NOT EXISTS ix_gattr_kv ON game_attributes(kind, value);
CREATE INDEX IF NOT EXISTS ix_mlink_game ON metadata_links(game_id);
CREATE INDEX IF NOT EXISTS ix_gtag_game ON game_tags(game_id);
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
    print("# IGDB: linked %d games" % n_link, file=sys.stderr)
if ss_link:
    print("# ScreenScraper: linked %d games" % ss_link, file=sys.stderr)
if n_attr:
    print("# provider attributes: +%d rows (IGDB+SS unioned per value, origins kept)"
          % n_attr, file=sys.stderr)
print("# total unique games: %d (%d available from >1 source KIND)" % (tot, multi),
      file=sys.stderr)
con.close()
