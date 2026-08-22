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
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
sys.path.insert(0, DIR)
import config
from titlenorm import norm      # shared dedupe normalizer (honors config prefs)
import merges                   # durable user merges — fold duplicates into one
import compilations             # collections/compilations — materialize owned members (§13)
import catalog_patch            # shared member-platform resolver (patched==rebuilt contract)
import splits                   # durable "peel apart" — split a merged-away game out
import console_eras             # hardware timelines — catch era-impossible merges
import homebrew                 # ROM release-type classifier (homebrew/hack/proto/…)
import overrides                # durable per-attribute user corrections
import platmap                  # platform ontology + filename-carried hardware tags
import ingesthints              # AI ingest hints (lite/heavy import) — advisory
_MERGE_ALIAS = merges.alias_map()
_PEEL = splits.overrides()      # {(source, source_id): (to_key, to_title)}
# {(system, game): (title, platform, year)} — what an AI ingest pass read off the
# file paths. Applied to folder-based ROMs only, and only where the algorithmic
# parse produced the (system, game) it was recorded against, so a re-scan that
# changes the parse quietly drops the stale hint instead of misapplying it.
_INGEST_HINT = ingesthints.overrides()


def _mkey(title, platform=None):
    """Dedupe key for a title, with any user 'Fix duplication' merge applied so a
    merged-away entry folds into its canonical one on every rebuild. Pass the entry
    platform so a ROM whose title bakes in the console name ("Doom 32X") keys the same
    as the plain title ("Doom") on that platform (titlenorm strips the hardware tag)."""
    k = norm(title, platform)
    return _MERGE_ALIAS.get(k, k)
from playnite import LIST_KINDS, SCALAR_KINDS
from igdb import map_record as igdb_map   # IGDB metadata-provider record mapping
from media import norm_system             # canonical console labels (gb->gameboy, …)

# Store ownership TSVs live in the DURABLE data dir (not next to the scripts, which
# is an ephemeral image layer): otherwise store ownership only survives via catalog
# carry-over and a rebuild-from-scratch silently drops every store's games. Fall back
# to the legacy /app location if that's the only place TSVs exist (e.g. update.sh,
# which writes beside the scripts) — migrated to /data on the next in-app sync.
# THE list of store sources. Two loops below used to hardcode their own copy of
# it, so adding nintendo wrote a TSV that nothing ever read. Order is
# load-significant, so it matches what those loops always used.
_STORE_SRCS = ("steam", "epic", "gog", "itch", "ea", "psn", "xbox", "nintendo")
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


def _emu_ep(source, platform, title):
    """Entry platform for a folder-based (emulation/archive) ROM, honoring
    filename > folder: when the TITLE carries an explicit hardware tag ("Doom 32X")
    that contradicts the folder/system label ("sega genesis"), the filename wins and
    the ROM is attributed to the tag's platform. Large collections misplace ROMs into
    the wrong folder far more often than they mis-name the file, so the in-title
    hardware tag is trusted 100% (detection is conservative — see platmap.TITLE_PLATFORM)."""
    ep = _entry_platform(source, platform)
    lbl = platmap.platform_from_title(title)
    if lbl and platmap.canon(lbl) != platmap.canon(ep):
        ep = _entry_platform(source, lbl)
    return ep


base_present = set()   # every base norm_key that has an owned/wanted entry (for the
                       # wishlist "already owned?" check, which is title-level)


def add(title, source, platform, sid, detail="", state="have"):
    # "Peel apart": a specific source row the user split off a merged entry goes to
    # its OWN key + title, overriding the natural title-derived key. Applied first so
    # the row lands on the peeled-off game on every rebuild.
    # entry platform is the identity axis; compute it first so the dedupe key can be
    # platform-aware (strip a baked-in console tag like "Doom 32X" -> "doom"). For
    # folder-based ROMs, an in-title hardware tag also re-platforms a misplaced file
    # (filename > folder) so the 32X "Doom 32X (E)" in a Genesis folder lands on 32X.
    ep = _emu_ep(source, platform, title) if source in ("emulation", "archive") \
        else _entry_platform(source, platform)
    peel = _PEEL.get((source, str(sid)))
    if peel:
        title = peel[1] or title
        key = _MERGE_ALIAS.get(peel[0], peel[0])
    else:
        key = _mkey(title, ep)
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
    # (`ep` computed above so the dedupe key could be platform-aware.)
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
for _s in _STORE_SRCS:
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
                # An AI ingest hint rewrites the title and/or the platform this ROM
                # is attributed to. The source id stays the folder's system so the
                # source row still points at where the file actually lives — only
                # the identity axes move. (The hint's year is left for the metadata
                # pass to use as a disambiguator; it must not enter the norm_key.)
                hint = _INGEST_HINT.get((system, game))
                title, plat = game, system
                if hint:
                    title = hint[0] or game
                    plat = hint[1] or system
                add(title, "emulation", plat, system, (regions or ""))
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


for _src in _STORE_SRCS:
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


# ---- Playnite library (scripts/playnite_bridge.ps1 -Export -> JSON) ----
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


# ---- era / handheld separation (DESIGN §11.6) ----
# Two DIFFERENT games that normalize to the same title (a ~1994 Game Boy "Uno" vs the
# 2016 Steam "UNO"; a retro-handheld port vs the home version) are already SEPARATE
# per-platform entries, but they still share a base norm_key — so title-level metadata
# (IGDB) would bleed across them and they'd cross-ref as "also owned on". Give the
# retro / era-impossible EMULATION entry a distinct CROSS-REF base_key (a private
# suffix) so it neither shares metadata nor cross-refs. norm_key stays the real title
# key (media is indexed by it, siloed by system), so only the grouping key changes.
def _igdb_ids():
    """norm_key -> IGDB id for resolved games (DESIGN §11.9 game_key assignment).
    Kept separate from _igdb_years so the identity map is a plain resolution read,
    no payload parse."""
    ids = {}
    _cache = os.path.join(DATA, "metadata-cache.sqlite")
    if not os.path.exists(_cache):
        return ids
    _c = sqlite3.connect(_cache)
    try:
        for _nk, _iid in _c.execute(
                "SELECT norm_key, igdb_id FROM igdb_resolution WHERE igdb_id>0"):
            ids[_nk] = _iid
    except sqlite3.OperationalError:
        pass
    finally:
        _c.close()
    return ids


def _igdb_years():
    """base norm_key -> earliest IGDB release year (for the era check)."""
    ry = {}
    _cache = os.path.join(DATA, "metadata-cache.sqlite")
    if not os.path.exists(_cache):
        return ry
    _c = sqlite3.connect(_cache)
    try:
        rows = _c.execute("SELECT r.norm_key, m.payload_json FROM igdb_resolution r "
                          "JOIN igdb_meta m ON m.igdb_id=r.igdb_id WHERE r.igdb_id>0")
        for _nk, _payload in rows:
            try:
                _rec = igdb_map(json.loads(_payload))
            except Exception:
                continue
            _y = _rec.get("release_year") or str(_rec.get("release_date") or "")[:4]
            try:
                _y = int(str(_y)[:4])
            except (TypeError, ValueError):
                continue
            if _y and (_nk not in ry or _y < ry[_nk]):
                ry[_nk] = _y
    except sqlite3.OperationalError:
        pass
    finally:
        _c.close()
    return ry


def _igdb_addon_parents():
    """{igdb_id: (kind, parent_igdb_id)} for records that are ADD-ON CONTENT.

    IGDB `game_type` 1 = dlc_addon, 2 = expansion. Both need the base game to run, so an
    owned one is content for a game rather than a game you own.

    TYPE 4 (standalone_expansion) IS DELIBERATELY EXCLUDED, and the live library is why.
    A standalone expansion runs WITHOUT the base game, and 22 of datbird's 37 add-on-ish
    entries are that type. Both Quake II Mission Packs are owned and Quake II is NOT, so
    filing them under a parent would take them out of the grid and put them nowhere.

    READ FROM THE MIRROR, NOT THE CACHE, and that distinction is the whole function.
    `metadata-cache.sqlite`'s igdb_meta payloads are built from `igdb.GAME_FIELDS`, which
    does not request `parent_game` — so every cached record has the type and no parent,
    and a first attempt against it found zero add-ons in a library holding fifteen. The
    mirror (`igdb-catalog.sqlite`) stores `parent_game` as a column and already carries it
    for 54,687 records. Local, complete, no refetch.
    """
    out = {}
    _mir = os.path.join(DATA, "igdb-catalog.sqlite")
    if not os.path.exists(_mir):
        return out
    _c = sqlite3.connect("file:%s?mode=ro" % _mir, uri=True)
    try:
        for _iid, _t, _p in _c.execute(
                "SELECT id, game_type, parent_game FROM games "
                "WHERE game_type IN (1,2) AND parent_game IS NOT NULL AND parent_game>0"):
            out[int(_iid)] = ("dlc" if _t == 1 else "expansion", int(_p))
    except sqlite3.OperationalError:
        pass                                   # mirror predates the column
    finally:
        _c.close()
    return out


def _igdb_bundle_ids():
    """IGDB ids whose record is a COMPILATION, not a game (`game_type` 3=bundle,
    13=pack).

    A bundle's identity must never define an individually-owned app. `_id_groups`
    collapses every entry sharing an `igdb:` game_key — correct for regional variants
    of one game (Mario & Luigi RPG 3), catastrophic for a bundle, whose id is shared by
    the DIFFERENT games inside it: owning "Ys I & II Chronicles+" resolved both Steam
    appids to igdb 21032 and merged Ys I and Ys II into a single entry, deleting one of
    them. DESIGN §13 says a compilation CREDITS its members; it must never consume them.

    Deterministic (Algo tier): IGDB states this outright and it costs one extra field on
    a query we already make. Ids absent from the cache (fetched before `game_type` was
    requested) are simply not flagged — forward-only, per the design decision."""
    out = set()
    _cache = os.path.join(DATA, "metadata-cache.sqlite")
    if not os.path.exists(_cache):
        return out
    _c = sqlite3.connect(_cache)
    try:
        # only RESOLVED ids can ever be refused — restrict the payload parse to them
        # instead of json-decoding the whole igdb_meta table every build
        try:
            _rows = _c.execute(
                "SELECT igdb_id, payload_json FROM igdb_meta WHERE igdb_id IN ("
                "SELECT igdb_id FROM igdb_resolution WHERE igdb_id>0 "
                "UNION SELECT igdb_id FROM entry_resolution WHERE igdb_id>0)").fetchall()
        except sqlite3.OperationalError:      # entry_resolution may not exist yet
            _rows = _c.execute("SELECT igdb_id, payload_json FROM igdb_meta").fetchall()
        for _iid, _payload in _rows:
            try:
                if (json.loads(_payload) or {}).get("game_type") in (3, 13):
                    out.add(_iid)
            except Exception:       # noqa: BLE001  malformed cache row
                continue
    except sqlite3.OperationalError:
        pass
    finally:
        _c.close()
    return out


_years = _igdb_years()
_ids = _igdb_ids()               # norm_key -> igdb_id (DESIGN §11.9 game_key)
# Refuse a compilation's identity BEFORE it can group anything. Dropping the id here
# sends the entry to `title:<norm_key>` in _game_key(), so each owned app keeps its own
# identity and _id_groups has nothing to collapse. Two plain entries + a warning, per
# the design's "Algo never guesses" rule.
# ADD-ON CONTENT: {igdb_id: (kind, parent_igdb_id)}. Inverted to owned norm_keys below,
# once every entry's identity is known.
_addon_src = _igdb_addon_parents()
_bundle_ids = _igdb_bundle_ids()
_identity_refused = []           # [(norm_key, reason, detail)] -> identity_review table
if _bundle_ids:
    _refused = sorted(nk for nk, iid in _ids.items() if iid in _bundle_ids)
    for _nk in _refused:
        _identity_refused.append(
            (_nk, "compilation_identity",
             "igdb:%s is a bundle/pack (game_type); refused as a single game's identity"
             % _ids[_nk]))
        del _ids[_nk]
    if _refused:
        print("build_library: refused %d compilation identity/ies (igdb game_type "
              "bundle/pack) — entries keep their own identity: %s"
              % (len(_refused), ", ".join(_refused[:12])
                 + (" …" if len(_refused) > 12 else "")), file=sys.stderr)

# Signal 3 (spec 2026-07-24): MANY-TO-ONE. Two or more distinct owned source_ids from
# ONE store, across DIFFERENT titles, resolving to a single IGDB identity is by
# definition a duplicate listing or a bundle — refuse the merge and flag, no guessing.
# This is the deterministic backstop for a bundle whose IGDB record is mis-typed
# (game_type absent or 0), where signal 2 is blind: the Ys shape still can't merge.
# STORE sources only, and only same-store: two owned ROM dumps of regional variants
# ("Contra" + "Probotector") legitimately share one identity and must keep merging,
# as do cross-store copies of one game.
_m2o = {}                       # igdb_id -> {store: {(norm_key, source_id), ...}}
for (_nk, _plat), _g in games.items():
    _iid = _ids.get(_nk)
    if not _iid:
        continue
    for _s in _g["sources"]:
        if _s[0] in ("emulation", "archive") or not _s[2]:
            continue
        _m2o.setdefault(_iid, {}).setdefault(_s[0], set()).add((_nk, _s[2]))
_m2o_refused = set()
for _iid, _per_store in _m2o.items():
    for _store, _pairs in _per_store.items():
        _nks = {p[0] for p in _pairs}
        _sids = {p[1] for p in _pairs}
        if len(_nks) >= 2 and len(_sids) >= 2:
            _m2o_refused |= _nks
for _nk in sorted(_m2o_refused):
    if _nk not in _ids:
        continue                # already refused by signal 2
    _identity_refused.append(
        (_nk, "many_to_one",
         "igdb:%s is claimed by 2+ distinct owned store apps with different titles; "
         "refused as a single game's identity" % _ids[_nk]))
    del _ids[_nk]
if _m2o_refused:
    print("build_library: refused %d many-to-one identity/ies (2+ owned store apps, "
          "one igdb record): %s" % (len(_m2o_refused),
          ", ".join(sorted(_m2o_refused)[:12])
          + (" …" if len(_m2o_refused) > 12 else "")), file=sys.stderr)


# Which OWNED titles are add-on content, and for which OWNED title. Built AFTER the
# refusals above, so a bundle or many-to-one identity that lost its id cannot be a parent
# and cannot be filed under one. `_ids` is norm_key -> igdb_id; invert it to find the
# parent's key, and keep only pairs where BOTH sides are owned. An add-on whose base game
# is not owned keeps parent_key NULL and stays in the grid, because hiding something you
# own under something you do not is strictly worse.
_by_igdb = {}
for _nk, _iid in _ids.items():
    _by_igdb.setdefault(_iid, _nk)
_ADDON = {}                      # norm_key -> (kind, parent_norm_key or None)
for _nk, _iid in _ids.items():
    _hit = _addon_src.get(_iid)
    if not _hit:
        continue
    _kind, _pid = _hit
    _pnk = _by_igdb.get(_pid)
    _ADDON[_nk] = (_kind, _pnk if _pnk and _pnk != _nk else None)
if _ADDON:
    _linked = sum(1 for _k, _p in _ADDON.values() if _p)
    print("build_library: %d add-on entrie(s), %d filed under an owned game"
          % (len(_ADDON), _linked), file=sys.stderr)


def _entry_igdb_ids():
    """(norm_key, platform) -> IGDB id for PER-ENTRY resolution overrides. When one title
    is two different games on two platforms (the 1986 Portal ROMs vs Valve's 2007 Portal),
    the ROM entries carry their own id here — overriding both the title-level resolution
    (which the store entry keeps) and the era-collision forfeit."""
    out = {}
    _cache = os.path.join(DATA, "metadata-cache.sqlite")
    if not os.path.exists(_cache):
        return out
    _c = sqlite3.connect(_cache)
    try:
        for _nk, _p, _iid in _c.execute(
                "SELECT norm_key, platform, igdb_id FROM entry_resolution WHERE igdb_id>0"):
            out[(_nk, _p)] = _iid
    except sqlite3.OperationalError:
        pass
    finally:
        _c.close()
    return out


def _entry_detached_set():
    """{(norm_key, platform)} explicitly detached from the title's game — a homebrew/demake
    sharing the name (the Atari 2600 "Doom") that must NOT inherit the title's identity."""
    out = set()
    _cache = os.path.join(DATA, "metadata-cache.sqlite")
    if not os.path.exists(_cache):
        return out
    _c = sqlite3.connect(_cache)
    try:
        for _nk, _p in _c.execute("SELECT norm_key, platform FROM entry_resolution "
                                  "WHERE matched_by='detached'"):
            out.add((_nk, _p))
    except sqlite3.OperationalError:
        pass
    finally:
        _c.close()
    return out


_entry_ids = _entry_igdb_ids()   # (norm_key, platform) -> igdb_id (per-entry override)
# A per-entry override pointing at a bundle id bypasses the refusal above (wand
# adjudication paths can set entry_resolution) — the invariant holds for EVERY route
# an identity can arrive by, so filter these too.
if _bundle_ids:
    _eb = [(k, v) for k, v in _entry_ids.items() if v in _bundle_ids]
    for (_nk, _plat), _iid in _eb:
        _identity_refused.append(
            (_nk, "compilation_identity",
             "entry override igdb:%s (%s) is a bundle/pack; refused" % (_iid, _plat)))
        del _entry_ids[(_nk, _plat)]
_entry_detached = _entry_detached_set()   # entries forced to their own (title) identity


def _game_key(nk, platform, bkey):
    """Resolved-identity key for an entry (DESIGN §11.9). A PER-ENTRY resolution override
    wins first (a same-title split — the entry IS its own game). Else: an ERA-collision
    entry (\x1f marker in its base_key) NEVER adopts the shared identity — the neutral art
    belongs to the different-era game — so it gets its own title identity. A non-separated
    identified entry OR a stray retro-handheld port (\x1e marker) ADOPTS the title's
    resolved igdb id (the port IS that game). Everything else (unidentified) falls to the
    title bucket. Suffix-free `title:<nk>` mirrors media_fetch.game_key so entry and media
    identities meet on a plain string match at serve time (see the format note there)."""
    if (nk, platform) in _entry_detached:     # homebrew/different game sharing the name
        return "title:%s" % nk                # → its own identity, never the title's game
    eid = _entry_ids.get((nk, platform))
    if eid:
        return "igdb:%s" % eid
    if "\x1f" in (bkey or "") or nk not in _ids:
        return "title:%s" % nk
    return "igdb:%s" % _ids[nk]


_by_base = {}
for (_b, _p), _g in games.items():
    _is_store = any(s[0] not in ("emulation", "archive") for s in _g["sources"])
    _by_base.setdefault(_b, []).append((_p, _is_store))
_sep = []                       # (old_ekey, new_base)
for _b, _ents in _by_base.items():
    if len(_ents) < 2:
        continue                # a single-platform game is never "two games"
    _y = _years.get(_b)
    # a "home" sibling = a store/PC entry or an emulation entry on non-handheld hardware
    _has_home = any(_st or not console_eras.is_handheld(_p) for _p, _st in _ents)
    # Siblings that could plausibly BE the `_y`-dated game: any store/PC entry (era-
    # unbounded) OR any console whose production era CAN contain `_y`. When such an
    # anchor exists, a sibling whose console CAN'T contain `_y` is a DIFFERENT same-named
    # game and gets peeled off. This catches all-emulation cross-era collisions — Apple
    # II "Alice in Wonderland" (1985) sharing a norm_key with the NDS 2010 movie game the
    # title resolved to — without splitting legit same-era ports: Gods on Genesis+SNES+
    # Amiga are all era-compatible with 1991, so `_compat` holds all three and none is
    # "impossible while a sibling is compatible". The era-aware IGDB matcher makes `_y`
    # trustworthy (it never binds a year impossible for the WHOLE console set), so this
    # no longer needs a store sibling to anchor the modern year. (DESIGN §11.6.)
    _compat = [_p for _p, _st in _ents
               if _st or (_y and not console_eras.impossible(_p, _y))]
    for _p, _st in _ents:
        if _st:
            continue            # store entries are authoritative, never separated
        _impossible = bool(_y and console_eras.impossible(_p, _y)
                           and any(_cp != _p for _cp in _compat))
        _stray = _has_home and console_eras.is_retro_handheld(_p)
        # The stray-handheld heuristic (a retro-handheld port sitting next to a home
        # version) only splits when a DIFFERENT *home* sibling exists, so it doesn't peel
        # a game off its own handheld self. The era-impossible branch needs no such guard:
        # `_compat` already proves a DIFFERENT platform can be the `_y` game, so this
        # entry's console genuinely can't be — separate it even when the compatible
        # sibling is itself a handheld (Apple II "Alice in Wonderland" 1985 vs. the NDS
        # 2010 movie game: NDS is the only compatible sibling, yet Apple II must split).
        _other_home = any((_op != _p) and (_os or not console_eras.is_handheld(_op))
                          for _op, _os in _ents)
        if _impossible or (_stray and _other_home):
            # Two separation flavors, distinguished by marker so the media layer can tell
            # them apart. ERA-impossible (\x1f) means the shared-title neighbor is a
            # DIFFERENT-era game, so the platform-neutral cover belongs to that other game
            # and this entry must forfeit it (Alice/Apple II vs. the 2010 NDS movie game).
            # STRAY retro-handheld (\x1e) is the SAME game in a downgraded port — the
            # name-based neutral cover is still correct art, so it keeps neutral media; the
            # split only exists to give the port its own metadata/cross-ref group, not to
            # blank its cover (Smash T.V. on Game Gear next to the NES version).
            _mark = "\x1f" if _impossible else "\x1e"
            _sep.append(((_b, _p), _b + _mark + _p))
sep_base = {ekey: newbase for ekey, newbase in _sep}   # (norm_key, plat) -> cross-ref base_key
if _sep:
    print("# era/handheld separation: split %d era-mismatched entr(y/ies) off their "
          "shared cross-ref key" % len(_sep), file=sys.stderr)


# ---- merge regional duplicates (DESIGN §11.7) ----
# Two ROMs of the SAME game on the SAME platform (the US "Bowser's Inside Story" and
# the Japanese "Mario & Luigi RPG 3") normalize to DIFFERENT titles -> different
# entries, yet IGDB resolved BOTH to one id. Collapse them into ONE entry, unioning
# their ROM sources, so the card shows a single game with its multiple regional
# versions listed (not duplicate covers). Grouped by the resolved-identity game_key +
# platform: an era-separated collision has a `title:…` game_key so it never merges, and
# nothing is dropped (every source is carried onto the surviving entry).
def _entry_game_key(_ek):
    _b, _p = _ek
    return _game_key(_b, _p, sep_base.get(_ek, _b))

_id_groups = {}                 # (game_key, platform) -> [ekey,...], resolved games only
for _ek in list(games):
    _gk = _entry_game_key(_ek)
    if _gk.startswith("igdb:"):
        _id_groups.setdefault((_gk, _ek[1]), []).append(_ek)

# resolved title (normalized) per igdb id -> pick the representative ROM as the one
# whose norm_key matches the canonical game, so its region's art + identity survive.
_igdb_name_norm = {}
if any(len(v) > 1 for v in _id_groups.values()):
    _mcache = os.path.join(DATA, "metadata-cache.sqlite")
    if os.path.exists(_mcache):
        _mc = sqlite3.connect(_mcache)
        try:
            for _iid, _payload in _mc.execute("SELECT igdb_id, payload_json FROM igdb_meta"):
                try:
                    _nm = (json.loads(_payload) or {}).get("name")
                except Exception:
                    _nm = None
                if _nm:
                    _igdb_name_norm[_iid] = _mkey(_nm)
        except sqlite3.OperationalError:
            pass
        finally:
            _mc.close()

_merged_n = 0
for (_gk, _plat), _eks in _id_groups.items():
    if len(_eks) < 2:
        continue
    _canon_nk = _igdb_name_norm.get(int(_gk.split(":", 1)[1]))
    # representative (lower is better): the canonical-title ROM, else the most-sourced
    # entry, else the lexically-smallest norm_key (deterministic across rebuilds).
    _rep = min(_eks, key=lambda _ek: (0 if _ek[0] == _canon_nk else 1,
                                      -len(games[_ek]["sources"]), _ek[0]))
    _rg = games[_rep]
    # dedup by (source, source_id, title_raw): emulation rows share source_id=SYSTEM
    # (e.g. 'nds'), so the ROM name is what distinguishes the US ROM from the Japanese
    # one — keep BOTH as sources so the merged card lists every regional version (and
    # the ROM-file lookup finds each). Store rows (real unique source_id) still dedup.
    _have = {(s[0], s[2], s[3]) for s in _rg["sources"]}
    for _ek in _eks:
        if _ek == _rep:
            continue
        _og = games.pop(_ek)
        for _s in _og["sources"]:
            _sig = (_s[0], _s[2], _s[3])
            if _sig not in _have:
                _rg["sources"].append(_s)
                _have.add(_sig)
        if not _rg.get("store_title") and _og.get("store_title"):
            _rg["store_title"] = _og["store_title"]
        if _ek[0] in games_attrs:               # fold attribute records onto the rep
            games_attrs.setdefault(_rep[0], {"src": []})["src"].extend(
                games_attrs.pop(_ek[0]).get("src", []))
        _merged_n += 1
if _merged_n:
    print("# regional-dup merge: folded %d duplicate entr(y/ies) into their canonical "
          "game (same IGDB id + platform)" % _merged_n, file=sys.stderr)


# ---- release type + unofficial-match block (DESIGN §11.8) ----
# Classify each ROM entry from its RAW filename tags (the parsed title strips them):
# Homebrew / Hack / Prototype / Demo / Unlicensed, or commercial. Two uses:
#  (1) the editable `release_type` attribute (written after the entries exist), which
#      Spotlight excludes from its normal themes;
#  (2) BLOCK a purely-unofficial title from inheriting a commercial IGDB identity — a
#      (pd) "hello world" is NOT the visual novel "Hello, world.". A Homebrew/Hack/
#      Unlicensed entry with NO commercial/proto/demo/store sibling forfeits the match:
#      it keeps its own filename + ROM art (game_key title:<nk>, no rename, no metadata).
#      Prototype/Demo of an official game still match — they ARE that game (early build).
rt_map = {}
lang_map = {}                    # (norm_key, platform) -> translation language
ver_map = {}                     # (norm_key, platform) -> translation/patch version
_rt_ov = {}
# Translation is deliberately NOT blocked: a fan English (or other) translation IS the
# base game, localized — it inherits the real IGDB identity/art and carries language +
# version attributes on top. Homebrew/Hack/Unlicensed are different works -> blocked.
BLOCK_RELEASE_TYPES = {"Homebrew", "Hack", "Unlicensed"}
blocked_entries = set()          # (norm_key, platform) that forfeit a commercial identity
if config.source_enabled("emulation"):
    _rtph = ",".join("?" * len(ROM_EXTS))
    for _rom_db in _rom_indexes():
        try:
            _rrc = sqlite3.connect("file:%s?mode=ro" % _rom_db, uri=True)
        except sqlite3.OperationalError:
            continue
        try:
            _rcols = {_r[1] for _r in _rrc.execute("PRAGMA table_info(roms)")}
            _selsub = "subdir" if "subdir" in _rcols else "''"
            for _sys, _game, _fn, _subdir in _rrc.execute(
                    "SELECT system, game, filename, %s FROM roms WHERE ext IN (%s) "
                    "AND game<>''" % (_selsub, _rtph), list(ROM_EXTS)):
                _ep = _emu_ep("emulation", _sys, _game)
                _k = _mkey(_game, _ep)
                if not _k:
                    continue
                _ek = (_k, _ep)
                # filename scene tag -> folder-path provenance -> anachronistic year: a
                # dump under a "…/Homebrew/" tree, or one whose filename year is impossible
                # for the console (Atari 2600 "Doom (2011)"), is aftermarket/homebrew.
                _rt = homebrew.classify(_fn) or homebrew.classify_path(_subdir)
                if _rt is None:
                    _yr = homebrew.year_from_name(_fn)
                    if _yr and console_eras.impossible(_ep, _yr):
                        _rt = "Homebrew"
                if _rt is None:
                    rt_map[_ek] = None          # a commercial dump present -> commercial
                elif _ek not in rt_map:
                    rt_map[_ek] = _rt           # first non-commercial type seen
                if _rt == "Translation":        # pull language + version off the patch
                    _lg = homebrew.extract_language(_fn)
                    _vr = homebrew.extract_version(_fn)
                    if _lg and _ek not in lang_map:
                        lang_map[_ek] = _lg
                    if _vr and _ek not in ver_map:
                        ver_map[_ek] = _vr
        except sqlite3.OperationalError:
            pass
        _rrc.close()
    try:                             # user corrections win (blank/"Commercial" clears it)
        _rt_ov = {_nk: (_kinds.get("release_type", {}).get("value") or "").strip()
                  for _nk, _kinds in overrides.all_overrides().items()
                  if "release_type" in _kinds}
    except Exception:
        _rt_ov = {}


def _entry_release_type(base, plat):
    """Effective release_type for an entry — the user override wins; blank = commercial."""
    if base in _rt_ov:
        _v = _rt_ov[base]
        return _v if _v in homebrew.TYPES else None
    return rt_map.get((base, plat))


for (_b, _p), _g in games.items():
    _hs = any(s[0] not in ("emulation", "archive") for s in _g["sources"])
    if not _hs and _entry_release_type(_b, _p) in BLOCK_RELEASE_TYPES:
        blocked_entries.add((_b, _p))
if blocked_entries:
    print("# unofficial-match block: %d homebrew/hack/unlicensed entr(y/ies) kept off a "
          "commercial IGDB identity" % len(blocked_entries), file=sys.stderr)


# ---- write ----
# Build into a TEMP db and atomically swap it in at the very end, so a reader (the
# server serving the library while a sync rebuilds) always sees a COMPLETE catalog —
# the old one until the swap, the new one after — and never a locked or half-built
# file. A single in-place transaction here held game-library.sqlite locked for the
# whole rebuild (~10 min on the array), throwing "database is locked" at every
# concurrent /api read. The swap also makes a crashed rebuild non-destructive (the
# old catalog survives). Same directory as OUT => os.replace is atomic.
TMP = OUT + ".building"
if os.path.exists(TMP):
    os.remove(TMP)
con = sqlite3.connect(TMP)
cur = con.cursor()
cur.executescript("""
CREATE TABLE games (id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
  platform TEXT, entry_key TEXT,   -- one entry per (norm_key, platform); entry_key = norm_key@platform
  base_key TEXT,                   -- cross-ref group ("also owned on" + metadata fan-out); = norm_key unless era-separated
  game_key TEXT,                   -- resolved-identity key (DESIGN §11.9): igdb:<id> when the entry adopts a resolved identity (identified or stray retro-handheld port), else title:<norm_key> — SUFFIX-FREE, matching media_fetch.game_key (era-collision, detached or unidentified). Serve matches this against media.game_key (Phase 3).
  n_sources INTEGER, n_kinds INTEGER, sources_summary TEXT,
  has_emulation INT, has_steam INT, has_gog INT, has_epic INT, has_itch INT,
  has_archive INT, in_playnite INT, in_launchbox INT,
  wanted INT DEFAULT 0,   -- wanted=1: a wishlist-only entry (no owned source)
  -- ADD-ONS (2026-08-22). An owned DLC or expansion stays a FULL entry: its own
  -- attributes, media, providers and detail page, because datbird asked for them to be
  -- clickable with their own date/description/art and a separate store would mean a
  -- second copy of every enrichment path. It simply leaves the grid, which filters
  -- parent_key IS NULL, and is listed under the game it extends instead.
  parent_key TEXT,        -- the base game's base_key. NULL for a game, and for an
                          -- add-on whose parent is NOT owned: hiding something you own
                          -- under something you do not is strictly worse.
  content_kind TEXT);     -- 'dlc' | 'expansion'. Set even when parent_key is NULL, so
                          -- Discover can offer the base game as a want.
CREATE TABLE sources (game_id INTEGER, source TEXT, platform TEXT,
  source_id TEXT, title_raw TEXT, detail TEXT, state TEXT DEFAULT 'have',
  via_collection TEXT);
  -- state: 'have' (owned via this source) | 'want' (per-format wish)
  -- via_collection: coll_key of the compilation this copy comes from (DESIGN §13).
  -- state stays 'have' because it IS owned — §13 calls collection credit "a form of
  -- ownership" — so every existing owned/count/facet query keeps working untouched.
  -- The provenance lives here instead, which keeps it queryable without redefining
  -- ownership across the codebase.
-- store-wishlist provenance for wanted games (which store(s) they're wanted from)
CREATE TABLE wanted (game_id INTEGER, store TEXT, store_id TEXT, title_raw TEXT);
-- Playnite-parity attributes:
CREATE TABLE source_attrs (game_id INTEGER, source TEXT, source_id TEXT,
  attrs_json TEXT);                       -- lossless per-provider record (export)
CREATE TABLE game_attributes (game_id INTEGER, kind TEXT, value TEXT,
  origin TEXT DEFAULT '');  -- origin = comma-joined source(s): steam/igdb/ai/…
-- Lossless per-metadata-provider attribute retention (tiered ingest): EVERY value
-- each provider (igdb/screenscraper/…) contributed per kind, incl. scalar "losers"
-- the game_attributes merge dropped. Powers per-provider provenance + the
-- disable/re-point identity-badge cascade (chosen value recomputed excluding a
-- disabled provider). game_attributes stays the merged/winning view.
CREATE TABLE provider_attrs (game_id INTEGER, provider TEXT, kind TEXT, value TEXT);
CREATE TABLE metadata_links (game_id INTEGER, provider TEXT, provider_id TEXT,
  slug TEXT, url TEXT);                    -- canonical ids from metadata providers
CREATE TABLE game_tags (game_id INTEGER, tag TEXT, origin TEXT);  -- origin: playnite/ludodex/…
-- Identities the ALGO tier refused, for a later tier to adjudicate. Algo never guesses:
-- when it can prove a match is unsafe (an IGDB bundle/pack id standing in for a single
-- owned app) it declines the identity, keeps the entries separate, and records WHY here.
-- Light/Heavy read this to scope AI match-verification at the games that actually need
-- it, instead of sweeping the catalog. Rebuilt with the catalog like everything else.
CREATE TABLE identity_review (norm_key TEXT, reason TEXT, detail TEXT);
""")

if _identity_refused:
    cur.executemany("INSERT INTO identity_review(norm_key,reason,detail) VALUES(?,?,?)",
                    _identity_refused)

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
    bkey = sep_base.get((base, plat), base)     # cross-ref/metadata key (usually = base)
    cur.execute(
        "INSERT INTO games(canonical_title,norm_key,platform,entry_key,base_key,game_key,"
        "n_sources,n_kinds,sources_summary,has_emulation,has_steam,has_gog,has_epic,has_itch,"
        "has_archive,in_playnite,in_launchbox,wanted,content_kind,parent_key) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (canonical, base, plat, "%s@%s" % (base, plat), bkey,
         ("title:%s" % base if (base, plat) in blocked_entries
          else _game_key(base, plat, bkey)),
         len(srcs), len(kinds), summary,
         int("emulation" in kinds), int("steam" in kinds),
         int("gog" in kinds), int("epic" in kinds), int("itch" in kinds),
         int("archive" in kinds), int(base in playnite_keys),
         int(base in launchbox_keys), 0 if owned else 1,
         # add-on content: kind is set whenever IGDB says so, parent_key only when the
         # base game is owned too (see _ADDON).
         (_ADDON.get(base) or (None, None))[0],
         (_ADDON.get(base) or (None, None))[1]))
    gid = cur.lastrowid
    key_to_gid[(base, plat)] = gid
    # metadata/tags fan out by the CROSS-REF key, so era-separated entries don't get
    # the shared title's metadata (an era-separated bkey != any provider norm_key).
    base_to_gids.setdefault(bkey, []).append(gid)
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
        "INSERT INTO games(canonical_title,norm_key,platform,entry_key,base_key,game_key,"
        "n_sources,n_kinds,sources_summary,has_emulation,has_steam,has_gog,has_epic,has_itch,"
        "has_archive,in_playnite,in_launchbox,wanted) "
        "VALUES(?,?,?,?,?,?,0,0,?,0,0,0,0,0,0,0,0,1)",
        (w["title"], key, plat, "%s@%s" % (key, plat), key, _game_key(key, plat, key),
         "wishlist:" + ",".join(stores)))
    gid = cur.lastrowid
    key_to_gid[(key, plat)] = gid
    base_to_gids.setdefault(key, []).append(gid)
    cur.executemany("INSERT INTO wanted(game_id,store,store_id,title_raw) "
                    "VALUES(?,?,?,?)", [(gid,) + s for s in w["stores"]])
    _wrote += 1
    if _wrote % 200 == 0:
        print("PROG\t%d\t%d\t%s\tcatalog" % (_wrote, _wtotal, key), flush=True)

# ---- collection members: materialize the games inside an OWNED compilation ----
# DESIGN §13 originally recorded membership and credited it at READ time only, which
# costs nothing but produces nothing when the bundle is your ONLY copy — the common
# case. Owning "Castlevania Anniversary Collection" left its games nowhere in the
# library. Members now become real entries, following the wishlist precedent: an entry
# that exists without a separate purchase, distinguished by its own marker.
#
# state stays 'have' (it IS owned — §13 calls collection credit "a form of ownership")
# so counts and facets keep working; `via_collection` carries the provenance.
# A member that ALREADY has an entry is skipped — the real one wins and picks up the
# existing read-time credit, so a game owned standalone never doubles.
_owned_bases = {r[0] for r in cur.execute(
    "SELECT DISTINCT g.base_key FROM games g JOIN sources s ON s.game_id=g.id "
    "WHERE s.state='have'")}
_lib_plats = [r[0] for r in cur.execute(
    "SELECT DISTINCT platform FROM games WHERE platform IS NOT NULL AND platform!=''")]
# An OWNED game's identity — the evidence that a member IS a game already owned under a
# different title. Only ids held by exactly ONE owned base_key; ambiguity credits nothing.
# `_ids` is already bundle-refused, so a compilation's own identity can never be the
# thing two members are matched on. Mirrors catalog_patch.materialize_members.
_owned_by_id = {}
for _bk in _owned_bases:
    _oid = _ids.get(_bk)
    if _oid:
        _owned_by_id.setdefault(_oid, []).append(_bk)
_owned_by_id = {_i: _b[0] for _i, _b in _owned_by_id.items() if len(_b) == 1}
_coll_made = _coll_sat = 0
for _c in compilations.all_collections(DATA):
    if _c["coll_key"] not in _owned_bases:
        continue                        # only an OWNED bundle credits anything
    _full = compilations.get_collection(DATA, _c["coll_key"]) or {}
    # the store/platform the bundle is owned on — inherited by its members.
    # Deterministic: constrained to an entry that actually HOLDS the owned source
    # (a base with both an owned and a wishlist entry must not hand its members a
    # platform/source picked by row luck), lowest id on ties.
    _crow = cur.execute(
        "SELECT g.platform, (SELECT s.source FROM sources s WHERE s.game_id=g.id "
        "AND s.state='have' LIMIT 1) FROM games g WHERE g.base_key=? "
        "AND EXISTS(SELECT 1 FROM sources s2 WHERE s2.game_id=g.id "
        "AND s2.state='have') ORDER BY g.id LIMIT 1",
        (_c["coll_key"],)).fetchone()
    _cplat, _csrc = (_crow[0] or "pc", _crow[1] or "") if _crow else ("pc", "")
    # The base_key this membership should CREDIT, resolved exactly as the surgical path
    # resolves it — patched==rebuilt, the same contract member_platform_label is shared
    # under. This pass used the stored member_key raw, so a rebuild re-created every
    # phantom `materialize_members` had just collapsed: 'The Ultimate DOOM' beside the
    # owned 'DOOM + DOOM II', both on IGDB 10192 (invariant I9).
    _sibs = catalog_patch.purchase_siblings(cur, DATA, _c["coll_key"])
    for _m in _full.get("members") or []:
        _mk = catalog_patch.resolve_member_key(
            cur, _m.get("member_title"), _m["member_key"], _sibs,
            _ids.get(_m["member_key"]), _owned_by_id)
        if not _mk or _mk == _c["coll_key"]:
            continue
        # Canonicalise: member_platform is an AI-supplied DISPLAY string ("PC",
        # "Game Boy Advance") while the catalog stores canonical labels ("pc", "gba").
        # member_platform_label prefers a label the library ALREADY uses for that
        # platform (platmap equivalence), so the facet never splits — 'Game Boy' must
        # land on the catalog's existing 'gameboy', not mint 'game boy'. Shared with
        # catalog_patch.materialize_members per the patched==rebuilt contract.
        _mplat = catalog_patch.member_platform_label(
            _m.get("member_platform"), _cplat, _lib_plats)
        if _mk in base_to_gids:
            if _mk in _owned_bases:
                continue                # standalone owned — read-time credit only
            # WISHLIST-ONLY entry: §13.3 — "a want for G is satisfied when G is owned
            # via a collection". Attach the via-ownership and clear the want, matching
            # the wishlist pass's own rule ("now owned -> no longer wanted").
            _gid = min(base_to_gids[_mk])
            cur.execute(
                "INSERT INTO sources(game_id,source,platform,source_id,title_raw,"
                "detail,state,via_collection) VALUES(?,?,?,?,?,?,'have',?)",
                (_gid, _csrc, _cplat, "", _m["member_title"], _c["name"],
                 _c["coll_key"]))
            cur.execute("UPDATE games SET wanted=0, n_sources=n_sources+1 "
                        "WHERE id=?", (_gid,))
            _coll_sat += 1
            continue
        cur.execute(
            "INSERT INTO games(canonical_title,norm_key,platform,entry_key,base_key,"
            "game_key,n_sources,n_kinds,sources_summary,has_emulation,has_steam,has_gog,"
            "has_epic,has_itch,has_archive,in_playnite,in_launchbox,wanted) "
            "VALUES(?,?,?,?,?,?,1,0,?,0,0,0,0,0,0,0,0,0)",
            (_m["member_title"], _mk, _mplat, "%s@%s" % (_mk, _mplat), _mk,
             _game_key(_mk, _mplat, _mk), "via:" + _c["name"]))
        _gid = cur.lastrowid
        key_to_gid[(_mk, _mplat)] = _gid
        base_to_gids.setdefault(_mk, []).append(_gid)
        cur.execute(
            "INSERT INTO sources(game_id,source,platform,source_id,title_raw,detail,"
            "state,via_collection) VALUES(?,?,?,?,?,?,'have',?)",
            (_gid, _csrc, _mplat, "", _m["member_title"], _c["name"], _c["coll_key"]))
        _coll_made += 1
if _coll_made or _coll_sat:
    print("build_library: materialized %d collection member(s), satisfied %d want(s) "
          "from %d owned compilation(s)" % (_coll_made, _coll_sat, len(_owned_bases & {
              c["coll_key"] for c in compilations.all_collections(DATA)})),
          file=sys.stderr)

# ---- release type -> editable attribute (classification computed above) ----
# gids of blocked entries, so the enrichment below skips them (no rename / no metadata
# from the wrongly-matched commercial game); their game_key is already title:<nk>.
blocked_gids = {key_to_gid[_e] for _e in blocked_entries if _e in key_to_gid}
_rt_rows = []
for (_base, _plat), _gid in key_to_gid.items():
    _rt = _entry_release_type(_base, _plat)
    if _rt:
        _rt_rows.append((_gid, "release_type", _rt, "rom"))
    _lg = lang_map.get((_base, _plat))       # translation language + patch version
    if _lg:
        _rt_rows.append((_gid, "language", _lg, "rom"))
    _vr = ver_map.get((_base, _plat))
    if _vr:
        _rt_rows.append((_gid, "version", _vr, "rom"))
if _rt_rows:
    cur.executemany("INSERT INTO game_attributes(game_id,kind,value,origin) "
                    "VALUES(?,?,?,?)", _rt_rows)
    print("# release types: flagged %d rows (type/language/version)" % len(_rt_rows),
          file=sys.stderr)

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
# Lossless retention: EVERY (gid, provider, kind, value) any provider fed, incl.
# scalar values that lose the first-wins merge above. Deduped on insert.
_prov_all = set()   # {(gid, provider, kind, value)}


def _accum(gid, kind, val, origin):
    if isinstance(val, list):
        d = p_multi.setdefault(gid, {}).setdefault(kind, {})
        for v in val:
            if v not in (None, ""):
                d.setdefault(str(v), set()).add(origin)
                _prov_all.add((gid, origin, kind, str(v)))
    elif val not in (None, ""):
        _prov_all.add((gid, origin, kind, str(val)))
        cur_s = p_scalar.setdefault(gid, {}).get(kind)
        if cur_s is None:
            p_scalar[gid][kind] = [str(val), {origin}]
        elif cur_s[0] == str(val):           # same value from another provider
            cur_s[1].add(origin)


# Steam appdetails attributes (tiered ingest, Algo tier) — Steam's OWN data, fed
# FIRST so it wins scalar disagreements for Steam-owned games (Steam is authoritative
# for its titles per the precedence rule); IGDB/ScreenScraper then fill the rest.
# Cached by media_fetch's Steam-media pass; absent until the first such pass.
STEAM_META_DB = os.path.join(DATA, "steam-meta.sqlite")
if os.path.exists(STEAM_META_DB):
    sm = sqlite3.connect(STEAM_META_DB)
    try:
        srows = sm.execute("SELECT norm_key, payload_json FROM steam_meta").fetchall()
    except sqlite3.OperationalError:
        srows = []
    sm.close()
    for nk, payload in srows:
        gids = base_to_gids.get(nk)
        if not gids:
            continue
        try:
            sattrs = json.loads(payload)
        except ValueError:
            continue
        for gid in gids:
            if gid in blocked_gids:
                continue
            for kind, val in sattrs.items():
                _accum(gid, kind, val, "steam")

CACHE_DB = os.path.join(DATA, "metadata-cache.sqlite")
n_link = n_attr = 0
if config.metadata_enabled("igdb") and os.path.exists(CACHE_DB):
    mc = sqlite3.connect(CACHE_DB)
    try:
        rows = mc.execute(
            "SELECT r.norm_key, r.igdb_id, r.slug, m.payload_json, r.matched_by "
            "FROM igdb_resolution r JOIN igdb_meta m ON m.igdb_id=r.igdb_id "
            "WHERE r.igdb_id>0").fetchall()
    except sqlite3.OperationalError:
        rows = []                   # cache exists but enrich hasn't populated it
    try:
        erows = mc.execute(
            "SELECT er.norm_key, er.platform, er.igdb_id, m.payload_json, er.matched_by "
            "FROM entry_resolution er JOIN igdb_meta m ON m.igdb_id=er.igdb_id "
            "WHERE er.igdb_id>0").fetchall()
    except sqlite3.OperationalError:
        erows = []                  # no per-entry overrides yet
    # AI-refined confidence for gray-zone matches (task #13, Phase 4) — nk -> (igdb_id,score)
    try:
        ai_conf = {nk: (iid, sc) for nk, iid, sc in mc.execute(
            "SELECT norm_key, igdb_id, score FROM match_confidence_ai")}
    except sqlite3.OperationalError:
        ai_conf = {}                # AI layer not present yet
    mc.close()
    # match confidence (task #13): identity certainty per identified entry, stored as an
    # attribute so `confidence:low` is a normal library filter. Title-level first; a
    # per-entry resolution (erows) overrides with that entry's own source + record. AI
    # band scores (match_confidence_ai) win when present + still pointing at the same id.
    import matchconf
    gid_platform = {gid: plat for (nk, plat), gid in key_to_gid.items()}
    _conf = {}                      # gid -> (score:int, reason:str)
    for nk, iid, slug, payload, matched_by in rows:
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
            if gid in blocked_gids:          # homebrew/hack/unlicensed: NOT this game
                continue
            _sc, _rs = matchconf.match_confidence(matched_by, nk, rec, gid_platform.get(gid))
            if nk in ai_conf and ai_conf[nk][0] == iid:   # AI band score wins (same id)
                _sc, _rs = ai_conf[nk][1], "AI-scored"
            _conf[gid] = (_sc, _rs)
            cur.execute("INSERT INTO metadata_links(game_id,provider,provider_id,"
                        "slug,url) VALUES(?,?,?,?,?)",
                        (gid, "igdb", str(iid), slug, url))
            n_link += 1
            # rename-on-match: adopt the provider's official title for ROM/archive-only
            # games (their title is just the filename, e.g. "0001 - F-Zero"). Store-owned
            # games already have clean titles, so leave those alone. norm_key is unchanged
            # (stays the dedupe key), and the ROM filename stays in the source's title_raw.
            # A refused identity must never define the entry — its TITLE included.
            # The bundle's metadata is retained (that's deliberate), but renaming a
            # ROM/archive entry to the compilation's name would donate exactly the
            # identity the refusal denied it.
            if name and rec.get("game_type") not in (3, 13):
                cur.execute(
                    "UPDATE games SET canonical_title=? WHERE id=? AND NOT EXISTS("
                    "SELECT 1 FROM sources WHERE game_id=? AND source NOT IN "
                    "('emulation','archive'))", (name, gid, gid))
            for kind, val in mapped.items():
                _accum(gid, kind, val, "igdb")
    # per-entry resolutions: a same-title split entry (the 1986 Portal ROMs, whose store
    # sibling stays Valve's Portal) gets ITS OWN game's link + metadata, on just that
    # entry's gid — keyed by (norm_key, platform), overriding the era-collision forfeit.
    for nk, plat, iid, payload, matched_by in erows:
        gid = key_to_gid.get((nk, plat))
        if not gid or gid in blocked_gids:
            continue
        try:
            rec = json.loads(payload)
        except ValueError:
            continue
        _sc, _rs = matchconf.match_confidence(matched_by, nk, rec, plat)  # entry override
        if nk in ai_conf and ai_conf[nk][0] == iid:
            _sc, _rs = ai_conf[nk][1], "AI-scored"
        _conf[gid] = (_sc, _rs)
        cur.execute("DELETE FROM metadata_links WHERE game_id=? AND provider='igdb'", (gid,))
        cur.execute("INSERT INTO metadata_links(game_id,provider,provider_id,slug,url) "
                    "VALUES(?,?,?,?,?)", (gid, "igdb", str(iid), None,
                    "https://www.igdb.com/games/%s" % iid))
        n_link += 1
        name = (rec.get("name") or "").strip()
        if name and rec.get("game_type") not in (3, 13):   # see the rename guard above
            cur.execute("UPDATE games SET canonical_title=? WHERE id=? AND NOT EXISTS("
                        "SELECT 1 FROM sources WHERE game_id=? AND source NOT IN "
                        "('emulation','archive'))", (name, gid, gid))
        for kind, val in igdb_map(rec).items():
            _accum(gid, kind, val, "igdb")
    # persist the confidence + reason as attributes (one value per entry)
    if _conf:
        _crows = []
        for _gid, (_sc, _rs) in _conf.items():
            _crows.append((_gid, "match_confidence", str(_sc), "derived"))
            _crows.append((_gid, "match_reason", _rs, "derived"))
        cur.executemany("INSERT INTO game_attributes(game_id,kind,value,origin) "
                        "VALUES(?,?,?,?)", _crows)
        _low = sum(1 for _s, _ in _conf.values()
                   if _s < int(config.get("match_confidence_threshold") or 60))
        print("# match confidence: scored %d identified entr(y/ies), %d low-confidence"
              % (len(_conf), _low), file=sys.stderr)

# ScreenScraper (emulation metadata; one scrape yields metadata + media, media is
# indexed separately). Unioned with IGDB per the merge above.
SS_CACHE = os.path.join(DATA, "screenscraper-cache.sqlite")
ss_attr = 0
if config.metadata_enabled("screenscraper") and os.path.exists(SS_CACHE):
    from screenscraper import extract_metadata as ss_map
    sc = sqlite3.connect(SS_CACHE)
    try:
        ss_rows = sc.execute("SELECT norm_key, ss_id, payload_json FROM ss_game "
                             "WHERE status='ok'").fetchall()
    except sqlite3.OperationalError:
        ss_rows = []
    sc.close()
    import matchconf as _mc
    # entry platform per gid (IGDB block defines its own copy; recompute here so SS
    # confidence works even when IGDB enrichment is disabled/absent).
    ss_gid_platform = {gid: plat for (nk, plat), gid in key_to_gid.items()}
    _ss_conf = {}                            # gid -> (score, reason)
    for nk, ss_id, payload in ss_rows:
        gids = base_to_gids.get(nk)          # metadata is title-level → every platform entry
        if not gids or not payload:
            continue
        try:
            jeu = json.loads(payload)
        except ValueError:
            continue
        mapped = ss_map(jeu)
        # SS candidate names + platforms for the confidence scorer (per-provider, task #3)
        _nm = mapped.get("name")
        ss_names = ([_nm] if isinstance(_nm, str) else list(_nm or []))
        ss_plats = mapped.get("platforms")
        ss_plat_canons = {platmap.canon(p) for p in (ss_plats or [])} if isinstance(ss_plats, list) else set()
        _mb = "ss_id" if ss_id else "ss_name"
        for gid in gids:
            if gid in blocked_gids:          # homebrew/hack/unlicensed: NOT this game
                continue
            # NB the ScreenScraper LINK is not written here. It used to be, and only for
            # games that happened to have a cached SS payload — so a rebuild kept 152 of
            # 1807 recorded matches and silently dropped the rest. Links for every
            # non-IGDB provider now come from `provider_links.sync()` below, straight off
            # the identity cache, so a rebuild is lossless.
            _ss_conf[gid] = _mc.ss_match_confidence(
                _mb, nk, ss_names, ss_plat_canons, ss_gid_platform.get(gid))
            for kind, val in mapped.items():
                if kind != "name":                  # 'name' isn't an attribute
                    _accum(gid, kind, val, "screenscraper")
    if _ss_conf:
        _sscrows = []
        for _gid, (_sc, _rs) in _ss_conf.items():
            _sscrows.append((_gid, "match_confidence_ss", str(_sc), "derived"))
            _sscrows.append((_gid, "match_reason_ss", _rs, "derived"))
        cur.executemany("INSERT INTO game_attributes(game_id,kind,value,origin) "
                        "VALUES(?,?,?,?)", _sscrows)

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
# Retain every provider's raw contribution (incl. merge losers) losslessly.
if _prov_all:
    cur.executemany("INSERT INTO provider_attrs(game_id,provider,kind,value) "
                    "VALUES(?,?,?,?)", sorted(_prov_all))

# ---- AI metadata supplement (accepted findings, fill-gaps, LOWEST precedence) ----
# Only attributes the user accepted in the metadata review, and only for kinds no
# owned/provider source supplied — AI never overrides real data.
ai_attr = 0
try:
    import aimeta
    # with_origin: an AI value the model RECALLED and one it went and READ are
    # different claims, and flattening both to "ai" tells the user the weaker thing
    # about the stronger one. NB this is only for attributes AI itself produced — an
    # AI-assisted MATCH credits the provider, a few blocks up, because the values are
    # the provider's and only the matching was AI's.
    for nk, (attrs, _ai_origin) in aimeta.accepted_supplements(with_origin=True).items():
        for gid in base_to_gids.get(nk, ()):   # title-level → every platform entry
            existing = have.setdefault(gid, set())
            new_rows = []
            for kind, val in attrs.items():
                if kind in existing:
                    continue
                for v in (val if isinstance(val, list) else [val]):
                    if v not in (None, ""):
                        new_rows.append((gid, kind, str(v), _ai_origin))
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
CREATE INDEX IF NOT EXISTS ix_pattr_game ON provider_attrs(game_id);
CREATE INDEX IF NOT EXISTS ix_mlink_game ON metadata_links(game_id);
CREATE INDEX IF NOT EXISTS ix_gtag_game ON game_tags(game_id);
""")
con.commit()

# Provider links for everything that isn't IGDB, rebuilt from the identity cache.
#
# This has to happen HERE, after the catalog exists, because a rebuild recreates
# `metadata_links` from nothing: before this, every ScreenScraper match without a cached
# payload and EVERY SteamGridDB match was simply lost, and nothing downstream could tell
# a match had ever been made. Since the tiered ingest runs this script again after its
# media phase, that quietly threw away the matching done earlier in the same job.
import provider_links                          # noqa: E402
_pl = provider_links.sync(con, CACHE_DB, blocked_gids=blocked_gids)
con.commit()
for _p, _n in sorted(_pl.items()):
    if _n:
        print("# %s: linked %d entr(y/ies) from the identity cache" % (_p, _n),
              file=sys.stderr)

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
if n_attr:
    print("# provider attributes: +%d rows (IGDB+SS unioned per value, origins kept)"
          % n_attr, file=sys.stderr)
print("# total unique games: %d (%d available from >1 source KIND)" % (tot, multi),
      file=sys.stderr)
con.close()
# atomic swap: readers see the OLD catalog until this instant, then the NEW one —
# no lock window, no half-built reads (see the TMP note above).
os.replace(TMP, OUT)

# The identities this build just decided are now authoritative — including the ones it
# REFUSED (compilation_identity, many_to_one). Media is stamped at fetch time from
# `igdb_resolution`, which knows nothing of a refusal, so any row whose entry moved is
# now keyed to an identity the catalog no longer gives it and its neutral art cannot
# serve (DESIGN §11.9). Reconcile here, after the swap: run before it and the repair
# would faithfully re-key media to the identities this build is replacing.
try:
    import media_fetch as _mf
    _n = _mf.reconcile_after_build()
    if _n:
        print("# media identity: re-keyed %d row(s) to the rebuilt catalog" % _n,
              file=sys.stderr)
except Exception as e:                  # never undo a successful build over media
    print("# media identity reconcile skipped: %s" % str(e)[:120], file=sys.stderr)
