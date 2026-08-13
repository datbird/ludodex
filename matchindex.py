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
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import matchgate                 # noqa: E402
from titlenorm import norm       # noqa: E402

DB = os.path.join(DATA, "match-index.sqlite")        # optional, rebuildable supplement
MAIN_DB = os.path.join(DATA, "metadata-cache.sqlite")  # where it used to live
IGDB_DB = os.path.join(DATA, "igdb-catalog.sqlite")
SS_DB = os.path.join(DATA, "ss-catalog.sqlite")

SS_ID_BASE = 100_000_000
YEAR_SLACK = 1                   # a year that disagrees by more than this is a refusal

# Which ROM hashes earn their place. CRC32 is what No-Intro, TOSEC and every frontend
# key on, and what ludodex already computes for a file; sha1 covers the DATs that
# publish nothing else. MD5 was a third of the index and duplicated both — dropped
# deliberately, not overlooked, and cheap to reinstate since this is all rebuilt.
HASH_NS = ("crc", "sha1")

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


def open_index():
    """The index if it is present, else None — the ONLY way a caller should reach it.

    None means "this machine has no index", which is not the same as "this game is not
    in the index". A caller that cannot tell those apart will refuse games it has simply
    never looked up, so the distinction is a return value rather than an empty dict."""
    if not os.path.exists(DB):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=30)
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
def resolve(con, ns, val):
    """Every handle on the game that `ns`/`val` identifies. THE query — one shape for
    a store id, a name, or a ROM hash."""
    rows = con.execute(
        "SELECT k2.ns, k2.val, k2.kind, k2.identity_id "
        "FROM identity_key k1 JOIN identity_key k2 USING (identity_id) "
        "WHERE k1.ns=? AND k1.val=?", (ns, str(val))).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["ns"], []).append(r["val"])
    if rows:
        out["_identity_id"] = rows[0]["identity_id"]
        ident = con.execute("SELECT name, year FROM identity WHERE id=?",
                            (rows[0]["identity_id"],)).fetchone()
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
    seen, out = set(), []
    for r in con.execute(
            "SELECT k.identity_id, i.name, i.year FROM identity_key k "
            "JOIN identity i ON i.id=k.identity_id "
            "WHERE k.ns IN ('name','alias') AND k.val=?", (nk,)):
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

    st = status(con)
    st.update({"ss_merged": ss_merged, "ss_own_identity": ss_own,
               "ss_hash_keys": ss_roms, "elapsed": round(time.time() - t0, 1)})
    con.execute("INSERT OR REPLACE INTO identity_state(k,v) VALUES('built_at',?)",
                (str(now),))
    con.commit()
    con.close()
    return st


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
    out = {"identities": q("SELECT COUNT(*) FROM identity"),
           "keys": q("SELECT COUNT(*) FROM identity_key"),
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
