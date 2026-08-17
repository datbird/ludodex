#!/usr/bin/env python3
"""ONE table that resolves any handle on a game to every other handle on that game.

THE PROBLEM THIS EXISTS TO END. Identity used to be answered per provider, per call:
ludodex held a Steam appid and had to go ASK IGDB what that was, then ask ScreenScraper
separately, each over the network, each through the acceptance gate, each able to
disagree. A GOG id in hand told you nothing about the Steam id for the same game.

So every handle becomes a row in ONE table, and every question becomes the SAME query:

    SELECT k2.ns, k2.val, k2.kind
      FROM identity_key k1 JOIN identity_key k2 USING (identity_id)
     WHERE k1.ns = ? AND k1.val = ?;

  a GOG id            -> every other store id, the ScreenScraper id, every ROM hash
  a normalized name   -> the same, via ns='name' or ns='alias'
  a CRC off a filename-> the same, with no name matching involved at all

WHAT A ROW MEANS. `kind` separates the two things that must never be confused:

  exact    the source PUBLISHES this pairing. A Steam appid from IGDB's own
           external_games table, or a ROM hash from ScreenScraper's own dump list.
           It needs no acceptance gate and cannot be a wrong bind.
  derived  WE concluded it, by matching a name and a year through matchgate. It is
           only ever as good as the gate, and it is marked so a caller can demand
           exactness when the cost of being wrong is high.

IDENTITY IDS. An IGDB game uses its igdb_id directly as the identity id — IGDB is the
spine and its ids are ~420k, six orders below the offset. A game ScreenScraper knows and
IGDB does not still deserves an identity, so it gets SS_ID_BASE + ss_id. Asserted at
build time rather than assumed: if IGDB ever reaches the offset, the build fails loudly
instead of silently merging two different games.

OPTIONAL, AND THEREFORE FAIL-OPEN. This lives in its OWN file because it is entirely
derived and runs to ~1 GB: the main match db holds decisions, this holds a rebuildable
index, and backups should not be carrying the second to protect the first. ludodex must
work without it — which makes absence newly reachable in every call site at once, and
absence here means NO EVIDENCE, never NO MATCH. A miss falls back to the network path.
Reading a miss as consent is the recurring defect in this codebase and an optional index
is the easiest place yet to make it, so `open_index()` returns None when the file is not
there and callers are expected to branch on that rather than on an empty result.

REBUILDABLE. Everything here is derived from the two mirrors, so a rebuild is always
safe and the index is never the only copy of anything.
"""
import json
import os
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
sys.path.insert(0, DIR)
import config                    # noqa: E402
import matchgate                 # noqa: E402
from titlenorm import norm       # noqa: E402


class _Con(sqlite3.Connection):
    """A connection that can carry the resolved preference. sqlite3.Connection has no
    __dict__, so this is the difference between caching it once and paying a settings
    lookup per resolve() — which at 0.25 ms a call, across a 573k-file library, would
    cost more than the resolution."""
    _prefer = None

DB = os.path.join(DATA, "match-index.sqlite")        # optional, rebuildable supplement
MAIN_DB = os.path.join(DATA, "metadata-cache.sqlite")  # where it used to live
IGDB_DB = os.path.join(DATA, "igdb-catalog.sqlite")
SS_DB = os.path.join(DATA, "ss-catalog.sqlite")

SS_ID_BASE = 100_000_000
LEARNED_ID_BASE = 200_000_000    # a game neither mirror knows, found by live search
# A ROM the free TheGamesDB hash map knows and NEITHER mirror does. Its own range so a
# later rebuild can drop the whole layer without touching anything else, and so a stray
# id can always be traced back to the source that minted it.
TGDB_ID_BASE = 300_000_000
YEAR_SLACK = 1                   # a year that disagrees by more than this is a refusal

# Which ROM hashes earn their place. CRC32 is what No-Intro, TOSEC and every frontend
# key on, and what ludodex already computes for a file; sha1 covers the DATs that
# publish nothing else. MD5 was a third of the index and duplicated both — dropped
# deliberately, not overlooked, and cheap to reinstate since this is all rebuilt.
HASH_NS = ("crc", "sha1")

# Which of the two lower layers is asked first when BOTH have an answer for a handle.
# Defaults to the user's own data: it was obtained on this library, about these files,
# and a shipped supplement is by definition someone else's conclusion. Flippable because
# the opposite is a legitimate preference — a user who trusts a curated catalog over
# matches their own earlier, worse rules produced wants the supplement in front, and
# should not have to delete anything to get it. Overrides outrank both either way.
PREFER_KEY = "matchindex.prefer"
PREFER_DYNAMIC, PREFER_SUPPLEMENT = "dynamic", "supplement"

# The supplement is a FILE, and the user gets to say where. It is ~0.85 GB of
# rebuildable data with no reason to sit on the same disk as the app — a NAS share, an
# external drive or a read-only mount are all reasonable, and pointing at one must not
# require moving anything ludodex owns.
PATH_KEY = "matchindex.path"

# ScreenScraper's database is CC BY-NC-SA 4.0. An index built from it is a DERIVATIVE
# WORK, so all three conditions ride along: attribution (below), non-commercial (a
# standing constraint on however this is distributed — it can never be sold or bundled
# into anything sold), and share-alike (this file carries the same licence onward).
# Stamped INTO the database rather than into release notes, because the file outlives
# the page it was downloaded from.
LICENSE = "CC BY-NC-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
ATTRIBUTION = ("Game data from ScreenScraper.fr (CC BY-NC-SA 4.0) and its contributors, "
               "from IGDB.com, and TheGamesDB ids via sselph/scraper's hash.csv (MIT). "
               "Built by ludodex. Non-commercial use only; derivative works must carry "
               "the same licence.")
SOURCES = [
    {"name": "sselph/scraper hash.csv", "url": "https://github.com/sselph/scraper",
     "license": "MIT (code); the data it carries is derived from TheGamesDB",
     "license_url": "https://github.com/sselph/scraper/blob/master/LICENSE",
     "provides": "SHA1 -> TheGamesDB game id, for ROM hashes"},
    {"name": "ScreenScraper.fr", "url": "https://www.screenscraper.fr",
     "license": "CC BY-NC-SA 4.0", "license_url": LICENSE_URL,
     "provides": "game identities, regional names, ROM hashes"},
    {"name": "IGDB.com", "url": "https://www.igdb.com",
     "license": "see IGDB API terms", "license_url": "https://www.igdb.com/api",
     "provides": "game identities, names, alternative names, release dates, store ids"},
]
# Where to look for a published build. A URL, not a hardcoded repo, because whoever runs
# this may publish their own — and because a supplement built from someone else's
# catalogs is theirs to distribute or not.
RELEASE_KEY = "matchindex.release_url"


def index_path():
    """Where the supplement lives — the configured path, else beside the other dbs."""
    try:
        p = (config.get(PATH_KEY, "") or "").strip()
    except Exception:                            # noqa: BLE001
        p = ""
    return p or DB

# IGDB store-source names -> the namespace a caller will ask with. Anything not named
# here still gets indexed, under a slug of its own name: a store we have no importer for
# is exactly the kind of thing this table should already know when we add one.
NS_ALIAS = {"steam": "steam", "gog": "gog", "epic games store": "epic",
            "microsoft": "xbox", "xbox marketplace": "xbox_marketplace",
            "playstation store us": "psn", "itchio": "itch", "apple": "apple",
            "android": "android", "amazon": "amazon", "twitch": "twitch",
            "giantbomb": "giantbomb", "youtube": "youtube", "oculus": "oculus"}


def _slug(s):
    import re
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")


def connect():
    """THE handle the pipeline uses. Always succeeds.

    Two stores, split by PROVENANCE rather than by size, because they have opposite
    lifecycles:

      learned_key   in the main db. Written by the live search path when the index had
                    no answer. This is a DECISION, it cost a rate-limited round trip and
                    an acceptance gate to obtain, it is not reproducible from any mirror,
                    and it must survive a rebuild and be backed up. Always present, even
                    when empty — an empty table is a populated-ness question, not an
                    existence one.
      identity_key  in match-index.sqlite. Bulk, derived, ~0.85 GB, rebuilt from the
                    mirrors whenever the rules improve, excluded from backups.

    Put learned rows in the rebuildable file and the next rebuild silently deletes the
    only copy of the most expensive data in the system."""
    con = sqlite3.connect(MAIN_DB, timeout=60, factory=_Con)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS learned_identity(
      id INTEGER PRIMARY KEY, name TEXT, norm_key TEXT, year INTEGER, learned_at INTEGER);
    CREATE TABLE IF NOT EXISTS learned_key(
      ns TEXT, val TEXT, identity_id INTEGER, kind TEXT, provider TEXT,
      learned_at INTEGER, PRIMARY KEY(ns, val, identity_id));
    CREATE INDEX IF NOT EXISTS ix_lk_ident ON learned_key(identity_id);
    -- The user's word, and the ONLY thing that outranks the shipped supplement. A
    -- shipped index is someone else's conclusion about the user's library; when they
    -- disagree, they are right and the file is not editable to say so. action='unbind'
    -- exists because "this is not that game" is a different statement from "this is
    -- some other game", and only one of them names a replacement.
    CREATE TABLE IF NOT EXISTS override_key(
      ns TEXT, val TEXT, identity_id INTEGER, action TEXT, note TEXT,
      created_at INTEGER, PRIMARY KEY(ns, val));
    """)
    con.commit()
    _ix = index_path()
    if os.path.exists(_ix):
        try:
            con.execute("ATTACH DATABASE ? AS ix", ("file:%s?mode=ro" % _ix,))
        except sqlite3.Error:
            pass
    # Read the preference ONCE per connection. resolve() runs at 0.25 ms and is called
    # per file in a 573k-file library; a settings lookup inside it would cost more than
    # the resolution does.
    try:
        con._prefer = config.get(PREFER_KEY, PREFER_DYNAMIC) or PREFER_DYNAMIC
    except Exception:                            # noqa: BLE001 — config is optional here
        con._prefer = PREFER_DYNAMIC
    return con


def set_preference(value):
    """Flip which layer answers first. Takes effect on the next connect()."""
    if value not in (PREFER_DYNAMIC, PREFER_SUPPLEMENT):
        raise ValueError("prefer must be %r or %r" % (PREFER_DYNAMIC, PREFER_SUPPLEMENT))
    config.set_(PREFER_KEY, value)
    return value


def has_index(con):
    """Is the bulk index attached? NOT the same question as whether a game is in it.

    A caller that conflates them refuses games it has merely never looked up — the
    fail-open shape this codebase keeps rediscovering."""
    return bool(con.execute("SELECT COUNT(*) FROM pragma_database_list "
                            "WHERE name='ix'").fetchone()[0])


def open_index():
    """Read-only handle on the bulk index alone, or None when it is not present."""
    _ix = index_path()
    if not os.path.exists(_ix):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % _ix, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        if not con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                           "AND name='identity_key'").fetchone()[0]:
            con.close()
            return None
    except sqlite3.Error:
        con.close()
        return None
    return con


def learn(con, pairs, name=None, year=None, provider=None, identity_id=None):
    """Record what the live search path found, so the next lookup is local.

    `pairs` is [(ns, val), ...] — everything the search established about ONE game.
    Binds to an existing identity when any pair already resolves to one, so a Steam
    search and a later ScreenScraper search on the same game converge instead of
    minting two. Otherwise mints a learned identity.

    Returns the identity id it wrote against."""
    now = int(time.time())
    pairs = [(ns, str(v)) for ns, v in (pairs or []) if ns and v not in (None, "")]
    if not pairs:
        return None
    if identity_id is None:
        for ns, val in pairs:
            hit = _lookup_identity(con, ns, val)
            if hit is not None:
                identity_id = hit
                break
    if identity_id is None:
        identity_id = LEARNED_ID_BASE + (
            con.execute("SELECT COALESCE(MAX(id), ?) + 1 FROM learned_identity "
                        "WHERE id >= ?", (LEARNED_ID_BASE, LEARNED_ID_BASE)
                        ).fetchone()[0] - LEARNED_ID_BASE)
        con.execute("INSERT OR REPLACE INTO learned_identity"
                    "(id,name,norm_key,year,learned_at) VALUES(?,?,?,?,?)",
                    (identity_id, name, norm(name or ""), year, now))
    for ns, val in pairs:
        con.execute("INSERT OR IGNORE INTO learned_key"
                    "(ns,val,identity_id,kind,provider,learned_at) "
                    "VALUES(?,?,?,'learned',?,?)", (ns, val, identity_id, provider, now))
    con.commit()
    return identity_id


def _lookup_identity(con, ns, val):
    r = con.execute("SELECT identity_id FROM learned_key WHERE ns=? AND val=?",
                    (ns, str(val))).fetchone()
    if r:
        return r["identity_id"]
    if has_index(con):
        r = con.execute("SELECT identity_id FROM ix.identity_key WHERE ns=? AND val=?",
                        (ns, str(val))).fetchone()
        if r:
            return r["identity_id"]
    return None


def _evict_legacy():
    """The index shipped briefly inside metadata-cache.sqlite. Leaving it there would
    mean two copies, one of them silently stale, and the main db carrying the ~1 GB this
    split exists to remove."""
    if not os.path.exists(MAIN_DB):
        return
    try:
        con = sqlite3.connect(MAIN_DB, timeout=30)
        have = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        gone = [t for t in ("identity_key", "identity", "identity_state") if t in have]
        for t in gone:
            con.execute("DROP TABLE %s" % t)
        if gone:
            con.commit()
            con.execute("VACUUM")          # the pages are the entire point of moving it
            con.commit()
        con.close()
    except sqlite3.Error:
        pass


def con_db():
    _evict_legacy()
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS identity(
      id INTEGER PRIMARY KEY,
      name TEXT, norm_key TEXT, year INTEGER, first_release_date INTEGER,
      built_at INTEGER);
    CREATE INDEX IF NOT EXISTS ix_ident_norm ON identity(norm_key);
    CREATE TABLE IF NOT EXISTS identity_key(
      ns TEXT, val TEXT, identity_id INTEGER, kind TEXT,
      PRIMARY KEY(ns, val, identity_id));
    CREATE INDEX IF NOT EXISTS ix_ik_ident ON identity_key(identity_id);
    CREATE TABLE IF NOT EXISTS identity_state(k TEXT PRIMARY KEY, v TEXT);
    """)
    con.commit()
    return con


# --- the one query --------------------------------------------------------- #
_ALL_KEYS = """
    SELECT ns, val, kind, identity_id FROM learned_key
    UNION ALL SELECT ns, val, kind, identity_id FROM ix.identity_key
"""
_LEARNED_ONLY = "SELECT ns, val, kind, identity_id FROM learned_key"


def _keys_sql(con):
    """The union when the bulk index is attached, the learned table alone when it is
    not. A machine with no index still resolves everything it has LEARNED."""
    return _ALL_KEYS if has_index(con) else _LEARNED_ONLY


def override(con, ns, val, identity_id=None, note=None):
    """The user's correction. identity_id=None means "this handle is NOT that game" and
    suppresses whatever the supplement claims, without asserting a replacement."""
    con.execute("INSERT OR REPLACE INTO override_key"
                "(ns,val,identity_id,action,note,created_at) VALUES(?,?,?,?,?,?)",
                (ns, str(val), identity_id,
                 "bind" if identity_id is not None else "unbind", note,
                 int(time.time())))
    con.commit()


def resolve(con, ns, val):
    """Every handle on the game that `ns`/`val` identifies. THE query — one shape for a
    store id, a name, or a ROM hash.

    PRECEDENCE: override > learned > supplement. The supplement is shipped, read-only
    and replaced wholesale on sync, so the user's correction cannot live in it and must
    outrank it here instead — otherwise the next sync silently reverts them.

    An empty result means "not known here", which is a real answer and the pipeline's
    signal to go and search. It is NOT the same as having no index; ask has_index()."""
    # IDENTITY IS CHOSEN BY THE HIGHEST LAYER THAT HAS AN ANSWER — it is not voted on.
    # Unioning the layers and joining across them looks equivalent and is not: when the
    # dynamic table and the supplement disagree about a handle, that returns the keys of
    # BOTH games welded into one answer. A user who has been running without the
    # supplement and then adds it must not have their own conclusions diluted by it.
    ov = con.execute("SELECT identity_id, action FROM override_key WHERE ns=? AND val=?",
                     (ns, str(val))).fetchone()
    if ov is not None and ov["action"] == "unbind":
        return {}
    iid = ov["identity_id"] if ov is not None else None
    if iid is None:
        def _dyn():
            return con.execute("SELECT identity_id FROM learned_key "
                               "WHERE ns=? AND val=?", (ns, str(val))).fetchone()

        def _sup():
            if not has_index(con):
                return None
            return con.execute("SELECT identity_id FROM ix.identity_key "
                               "WHERE ns=? AND val=?", (ns, str(val))).fetchone()

        first, second = ((_sup, _dyn)
                         if getattr(con, "_prefer", PREFER_DYNAMIC) == PREFER_SUPPLEMENT
                         else (_dyn, _sup))
        r = first() or second()          # the second layer is a FALLBACK, not a vote
        if r is None:
            return {}
        iid = r["identity_id"]

    # Identity settled, every layer contributes its keys FOR THAT IDENTITY — a handle
    # learned against a supplement identity is exactly the case worth supporting.
    rows = con.execute(
        "WITH k AS (%s) SELECT ns, val, kind FROM k WHERE identity_id=?"
        % _keys_sql(con), (iid,)).fetchall()
    out, seen = {}, set()
    for r in rows:
        if (r["ns"], r["val"]) in seen:
            continue
        seen.add((r["ns"], r["val"]))
        out.setdefault(r["ns"], []).append(r["val"])
    if not out:
        return {}
    out["_identity_id"] = iid
    ident = con.execute("SELECT name, year FROM learned_identity WHERE id=?",
                        (iid,)).fetchone()
    if ident is None and has_index(con):
        ident = con.execute("SELECT name, year FROM ix.identity WHERE id=?",
                            (iid,)).fetchone()
    if ident:
        out["_name"], out["_year"] = ident["name"], ident["year"]
    return out


def resolve_name(con, title, year=None):
    """A title off a filename or a folder -> candidate identities, best first.

    Separate from resolve() because a name is not a handle: it can hit several games,
    and a year narrows but does not decide. The gate still runs, so a caller gets the
    same acceptance rule every provider gets."""
    nk = norm(title or "")
    if not nk:
        return []
    idents = "SELECT id,name,year FROM learned_identity" + (
        " UNION ALL SELECT id,name,year FROM ix.identity" if has_index(con) else "")
    seen, out = set(), []
    for r in con.execute(
            "WITH k AS (%s), i AS (%s) "
            "SELECT k.identity_id, i.name, i.year FROM k JOIN i ON i.id=k.identity_id "
            "WHERE k.ns IN ('name','alias') AND k.val=?" % (_keys_sql(con), idents),
            (nk,)):
        if r["identity_id"] in seen:
            continue
        seen.add(r["identity_id"])
        ok, sc = matchgate.score([title], r["name"], year, r["year"])
        if ok:
            out.append({"identity_id": r["identity_id"], "name": r["name"],
                        "year": r["year"], "score": round(sc, 3)})
    return sorted(out, key=lambda d: -d["score"])


# --- building -------------------------------------------------------------- #
def _attach(con):
    for path, alias in ((IGDB_DB, "ig"), (SS_DB, "ss")):
        if os.path.exists(path):
            con.execute("ATTACH DATABASE ? AS %s" % alias,
                        ("file:%s?mode=ro" % path,))
    return {a for a in ("ig", "ss")
            if con.execute("SELECT COUNT(*) FROM pragma_database_list "
                           "WHERE name=?", (a,)).fetchone()[0]}


def _has_table(con, alias, table):
    return bool(con.execute(
        "SELECT COUNT(*) FROM %s.sqlite_master WHERE type='table' AND name=?"
        % alias, (table,)).fetchone()[0])


def build(progress=True):
    """(Re)build the index from the mirrors. Safe to re-run: the SS walk is still
    filling, so this is expected to run again as the catalog grows."""
    con = con_db()
    have = _attach(con)
    if "ig" not in have:
        con.close()
        raise SystemExit("matchindex: no IGDB mirror at %s" % IGDB_DB)
    now = int(time.time())
    t0 = time.time()

    con.execute("DELETE FROM identity_key")
    con.execute("DELETE FROM identity")

    # 1. the spine — every IGDB game is an identity, using its own id.
    top = con.execute("SELECT MAX(id) FROM ig.games").fetchone()[0] or 0
    if top >= SS_ID_BASE:
        con.close()
        raise SystemExit("matchindex: IGDB id %d has reached SS_ID_BASE — the "
                         "identity id ranges would collide" % top)
    con.execute(
        "INSERT INTO identity(id,name,norm_key,year,first_release_date,built_at) "
        "SELECT id,name,norm_key,year,first_release_date,? FROM ig.games", (now,))
    con.execute("INSERT OR IGNORE INTO identity_key(ns,val,identity_id,kind) "
                "SELECT 'igdb', CAST(id AS TEXT), id, 'exact' FROM ig.games")

    # 2. names and aliases — the only keys that are not exact, by nature.
    con.execute("INSERT OR IGNORE INTO identity_key(ns,val,identity_id,kind) "
                "SELECT 'name', norm_key, id, 'derived' FROM ig.games "
                "WHERE norm_key IS NOT NULL AND norm_key != ''")
    con.execute("INSERT OR IGNORE INTO identity_key(ns,val,identity_id,kind) "
                "SELECT 'alias', norm_key, game_id, 'derived' FROM ig.alt_names "
                "WHERE norm_key IS NOT NULL AND norm_key != ''")

    # 3. store ids — IGDB publishes these, so they are exact by definition.
    stores = {r["id"]: (r["name"] or "") for r in con.execute("SELECT id,name FROM ig.stores")}
    ns_for = {sid: NS_ALIAS.get(nm.strip().lower(), _slug(nm) or "store_%d" % sid)
              for sid, nm in stores.items()}
    for sid, ns in ns_for.items():
        con.execute("INSERT OR IGNORE INTO identity_key(ns,val,identity_id,kind) "
                    "SELECT ?, uid, game_id, 'exact' FROM ig.external_ids "
                    "WHERE source_id=?", (ns, sid))
    con.commit()
    if progress:
        print("matchindex: igdb spine done, %d keys, %.0fs"
              % (con.execute("SELECT COUNT(*) FROM identity_key").fetchone()[0],
                 time.time() - t0), file=sys.stderr)

    # 4. ScreenScraper — the only part that has to be MATCHED rather than read.
    ss_merged = ss_own = ss_roms = 0
    if "ss" in have and _has_table(con, "ss", "ss_games"):
        # SS system -> the IGDB platform it is, so a candidate on the wrong hardware
        # cannot be accepted on a name alone.
        sysmap = {r["id"]: r["igdb_platform"] for r in
                  con.execute("SELECT id, igdb_platform FROM ss.ss_systems")}
        for g in con.execute("SELECT id,name,norm_key,year,systeme FROM ss.ss_games"):
            ident = _merge_ss(con, g, sysmap)
            if ident >= SS_ID_BASE:
                ss_own += 1
            else:
                ss_merged += 1
            con.execute("INSERT OR IGNORE INTO identity_key(ns,val,identity_id,kind) "
                        "VALUES('ss',?,?,'exact')", (str(g["id"]), ident))
            for r in con.execute("SELECT crc,md5,sha1 FROM ss.ss_roms WHERE game_id=?",
                                 (g["id"],)):
                for ns, v in (("crc", r["crc"]), ("sha1", r["sha1"])):
                    if v and ns in HASH_NS:
                        con.execute(
                            "INSERT OR IGNORE INTO identity_key(ns,val,identity_id,kind)"
                            " VALUES(?,?,?,'exact')", (ns, v, ident))
                        ss_roms += 1
            if progress and (ss_merged + ss_own) % 20000 == 0:
                print("matchindex: %d ss games (%d merged), %.0fs"
                      % (ss_merged + ss_own, ss_merged, time.time() - t0),
                      file=sys.stderr)
    con.commit()

    # 5. TheGamesDB ids, for free — see _merge_tgdb_freemap.
    tgdb_linked, tgdb_new = _merge_tgdb_freemap(con, now, progress, t0)

    st = status(con)
    st.update({"ss_merged": ss_merged, "ss_own_identity": ss_own,
               "ss_hash_keys": ss_roms, "tgdb_linked": tgdb_linked,
               "tgdb_new_identities": tgdb_new,
               "elapsed": round(time.time() - t0, 1)})
    # PROVENANCE TRAVELS WITH THE FILE, not with the release page it was downloaded
    # from. A published sqlite gets copied to a NAS, handed to a friend, restored from
    # a backup — and every one of those separates it from the notes that said who made
    # the data and under what licence. ScreenScraper's contents are CC BY-NC-SA 4.0, so
    # attribution is a condition of redistributing them, and a condition that only holds
    # while the file sits next to its README is not being met.
    for k, v in (("built_at", str(now)),
                 ("license", LICENSE),
                 ("attribution", ATTRIBUTION),
                 ("sources", json.dumps(SOURCES))):
        con.execute("INSERT OR REPLACE INTO identity_state(k,v) VALUES(?,?)", (k, v))
    con.commit()
    con.close()
    return st


def _merge_tgdb_freemap(con, now, progress=True, t0=None):
    """Fold the free SHA1 -> TheGamesDB-id map in. -> (linked, newly_minted).

    A TheGamesDB key is 1,000 requests A MONTH and name search does not batch — one
    request per title — so resolving a library through the API is measured in years. The
    free sselph/scraper hash.csv carries 58,843 SHA1 hashes against 11,008 game ids, and
    every one it hits is an id we never had to ask for. Measured on this deployment:
    23,649 of 720,097 ScreenScraper hashes hit, resolving 10,700 distinct games.

    A HASH IS EVIDENCE; THE NAMES IN THAT FILE ARE NOT. The file carries ROM names too,
    and using them would multiply the hit rate. They are used only to LABEL an identity
    the hash created, never to find one — a SHA1 collision is a cryptographic event, a
    name collision is Tuesday, and name-matching out of a file with no platform gate is
    exactly the fail-open shape this codebase keeps paying for.

    Never raises. This is an optional layer of an optional index; a rebuild that dies
    because GitHub was slow is a worse outcome than one that finishes without it."""
    linked = new = 0
    t0 = t0 or time.time()
    try:
        import tgdb_freemap
        if not (tgdb_freemap.enabled() and tgdb_freemap.fetch()):
            return 0, 0
        for sha1, gid, _plat, nm in tgdb_freemap.rows():
            r = con.execute("SELECT identity_id FROM identity_key WHERE ns='sha1' "
                            "AND val=? LIMIT 1", (sha1,)).fetchone()
            if r is not None:
                ident = r["identity_id"]
                linked += 1
            else:
                # Neither mirror knows this dump. Mint an identity of its own rather
                # than attaching the hash to a plausible neighbour — the miss IS the
                # answer, and a new identity is what a miss means here.
                ident = TGDB_ID_BASE + gid
                con.execute(
                    "INSERT OR IGNORE INTO identity(id,name,norm_key,year,"
                    "first_release_date,built_at) VALUES(?,?,?,NULL,NULL,?)",
                    (ident, nm, norm(nm), now))
                con.execute(
                    "INSERT OR IGNORE INTO identity_key(ns,val,identity_id,kind) "
                    "VALUES('sha1',?,?,'exact')", (sha1, ident))
                new += 1
            con.execute("INSERT OR IGNORE INTO identity_key(ns,val,identity_id,kind) "
                        "VALUES('thegamesdb',?,?,'exact')", (str(gid), ident))
        con.commit()
        if progress:
            print("matchindex: thegamesdb freemap — %d ids onto known identities, "
                  "%d new, %.0fs" % (linked, new, time.time() - t0), file=sys.stderr)
    except Exception as e:                          # noqa: BLE001
        print("matchindex: thegamesdb freemap skipped (%s)" % str(e)[:120],
              file=sys.stderr)
    return linked, new


def _merge_ss(con, g, sysmap):
    """Which identity is this ScreenScraper game? An existing IGDB one when the gate
    accepts it, otherwise its own.

    A miss here must create a NEW identity, never fall through to a plausible-looking
    neighbour — the recurring bug in this codebase is a lookup that misses and gets read
    as consent."""
    nk = g["norm_key"]
    plat = sysmap.get(g["systeme"])
    if nk:
        cands = con.execute(
            "SELECT DISTINCT k.identity_id, i.name, i.year FROM identity_key k "
            "JOIN identity i ON i.id=k.identity_id "
            "WHERE k.ns IN ('name','alias') AND k.val=? AND k.identity_id < ?",
            (nk, SS_ID_BASE)).fetchall()
        best, best_sc = None, 0.0
        for c in cands:
            # Hardware has to agree when both sides state it. SS says which system a
            # game is on; IGDB says which platforms it released on. A name that matches
            # on the wrong machine is a different product.
            if plat is not None:
                on = con.execute("SELECT 1 FROM ig.game_platforms WHERE game_id=? "
                                 "AND platform_id=?", (c["identity_id"], plat)).fetchone()
                if not on:
                    continue
            ok, sc = matchgate.score([g["name"] or ""], c["name"], g["year"], c["year"])
            if ok and sc > best_sc:
                best, best_sc = c["identity_id"], sc
        if best is not None:
            return best

    ident = SS_ID_BASE + int(g["id"])
    con.execute(
        "INSERT OR IGNORE INTO identity(id,name,norm_key,year,built_at) "
        "VALUES(?,?,?,?,?)", (ident, g["name"], nk, g["year"], int(time.time())))
    if nk:
        con.execute("INSERT OR IGNORE INTO identity_key(ns,val,identity_id,kind) "
                    "VALUES('name',?,?,'derived')", (nk, ident))
    return ident


def status(con=None):
    own = con is None
    con = con or con_db()
    q = lambda s: con.execute(s).fetchone()[0]      # noqa: E731
    meta = {r[0]: r[1] for r in con.execute("SELECT k,v FROM identity_state")}
    out = {"identities": q("SELECT COUNT(*) FROM identity"),
           "keys": q("SELECT COUNT(*) FROM identity_key"),
           "license": meta.get("license"),
           "attribution": meta.get("attribution"),
           "sources": json.loads(meta.get("sources") or "[]"),
           "by_ns": {r[0]: r[1] for r in con.execute(
               "SELECT ns, COUNT(*) FROM identity_key GROUP BY ns ORDER BY 2 DESC")}}
    if own:
        con.close()
    return out


def main(argv):
    if "--status" in argv:
        print(json.dumps(status(), indent=2))
        return 0
    if "--resolve" in argv:
        spec = argv[argv.index("--resolve") + 1]
        ns, _, val = spec.partition("=")
        con = con_db()
        print(json.dumps(resolve(con, ns, val), indent=2))
        con.close()
        return 0
    if "--name" in argv:
        title = argv[argv.index("--name") + 1]
        yr = int(argv[argv.index("--year") + 1]) if "--year" in argv else None
        con = con_db()
        print(json.dumps(resolve_name(con, title, yr), indent=2))
        con.close()
        return 0
    print("matchindex: " + json.dumps(build()), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
