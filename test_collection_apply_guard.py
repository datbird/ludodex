#!/usr/bin/env python3
"""Contract test for the APPLY path's one-product-one-collection guard (DESIGN §13).

Offline, no network, no AI, no live DBs — a synthetic catalog + steam-meta cache in a
temp LUDODEX_DATA, driving the REAL server function that accepting a review card runs.

The defect this pins: the canonical-appid dedupe lived ONLY in `_collection_candidates`
(the scan path). `_aimeta_apply` wrote every accepted collection straight to the store,
so accepting a sibling app's card — `ys 2` proposing "Ys I & II Chronicles+" while
`ys i` is already recorded for the SAME purchase — created a second collection and
materialized its members a second time.

  1. GUARD      — a sibling app's card is a no-op when the product is already recorded.
  2. NOT BROAD  — an unrelated new collection still records (the guard must not become
                  "never record anything new").
  3. PINNED     — manual curation still wins over a replayed AI finding.
  4. NO CACHE   — a catalog with no steam-meta cache records normally (nothing to
                  dedupe against must not mean nothing gets through).

Run:  ./.venv/bin/python test_collection_apply_guard.py
"""
import os
import sqlite3
import sys
import tempfile

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def build_data_dir(with_steam_meta=True):
    """A minimal but REAL data dir: the catalog columns app.py reads, plus the
    appid -> canonical_appid cache that makes two owned apps one product."""
    d = tempfile.mkdtemp(prefix="ludodex-apply-guard-")
    lib = sqlite3.connect(os.path.join(d, "game-library.sqlite"))
    lib.executescript("""
    CREATE TABLE games(
      id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT, platform TEXT,
      entry_key TEXT, base_key TEXT, game_key TEXT, n_sources INT, n_kinds INT,
      sources_summary TEXT, has_emulation INT, has_steam INT, has_gog INT,
      has_epic INT, has_itch INT, has_archive INT, in_playnite INT,
      in_launchbox INT, wanted INT);
    CREATE TABLE sources(
      id INTEGER PRIMARY KEY, game_id INT, source TEXT, platform TEXT,
      source_id TEXT, title_raw TEXT, detail TEXT, state TEXT,
      via_collection TEXT);
    CREATE TABLE game_attributes(game_id INT, kind TEXT, value TEXT, origin TEXT);
    """)
    # (norm_key, title, owned steam appid).  223810 + 223870 are the two apps Steam
    # grants for the ONE "Ys I & II Chronicles+" purchase; 3320 is an unrelated game.
    for nk, title, appid in (("ys i", "Ys I", "223810"),
                             ("ys 2", "Ys II", "223870"),
                             ("pirates gold plus", "Pirates! Gold Plus", "3320")):
        cur = lib.execute(
            "INSERT INTO games(canonical_title,norm_key,platform,entry_key,base_key,"
            "game_key,n_sources,n_kinds,sources_summary,has_emulation,has_steam,"
            "has_gog,has_epic,has_itch,has_archive,in_playnite,in_launchbox,wanted) "
            "VALUES(?,?,'pc',?,?,?,1,0,'steam',0,1,0,0,0,0,0,0,0)",
            (title, nk, "%s@pc" % nk, nk, "title:%s" % nk))
        lib.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw,"
                    "state) VALUES(?,'steam','pc',?,?,'have')",
                    (cur.lastrowid, appid, title))
    lib.commit()
    lib.close()
    if with_steam_meta:
        sm = sqlite3.connect(os.path.join(d, "steam-meta.sqlite"))
        sm.execute("CREATE TABLE steam_meta(appid TEXT PRIMARY KEY, "
                   "canonical_appid TEXT, store_name TEXT)")
        for appid, canon, name in (("223810", "223810", "Ys I & II Chronicles+"),
                                   ("223870", "223810", "Ys I & II Chronicles+"),
                                   ("3320", "3320", "Pirates! Gold Plus")):
            sm.execute("INSERT INTO steam_meta(appid,canonical_appid,store_name) "
                       "VALUES(?,?,?)", (appid, canon, name))
        sm.commit()
        sm.close()
    return d


def finding(coll_key, name, members):
    """The shape aimeta.accepted_collections() hands the apply path."""
    return {"coll_key": coll_key, "name": name,
            "members": [{"title": t, "platform": "pc", "year": None} for t in members]}


def load(mod, d):
    return {c["coll_key"]: c for c in mod.all_collections(d)}


def main():
    d = build_data_dir()
    os.environ["LUDODEX_DATA"] = d
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import compilations
    from server import app as srv

    print("1. GUARD — sibling app's card is a no-op once the product is recorded")
    compilations.set_collection(d, "ys i", "Ys I & II Chronicles+",
                                [{"title": "Ys I"}, {"title": "Ys II"}], origin="ai")
    srv._record_accepted_collections(
        [finding("ys 2", "Ys I & II Chronicles+", ["Ys I", "Ys II"])])
    colls = load(compilations, d)
    check("the sibling did NOT create a second collection", "ys 2" not in colls)
    check("the canonical app's collection survives untouched", "ys i" in colls)
    check("exactly one collection for the product", len(colls) == 1)

    print("2. NOT BROAD — an unrelated new collection still records")
    srv._record_accepted_collections(
        [finding("pirates gold plus", "Pirates! Gold Plus",
                 ["Pirates!", "Pirates! Gold"])])
    colls = load(compilations, d)
    check("a different product IS recorded", "pirates gold plus" in colls)
    check("both collections now present", len(colls) == 2)

    print("3. PINNED — manual curation beats a replayed AI finding")
    compilations.set_collection(d, "pirates gold plus", "Pirates! Gold Plus",
                                [{"title": "Pirates!"}], origin="manual")
    srv._record_accepted_collections(
        [finding("pirates gold plus", "Pirates! Gold Plus",
                 ["Pirates!", "Pirates! Gold", "Pirates! Gold Plus"])])
    full = compilations.get_collection(d, "pirates gold plus")
    check("manual member list not reset by the AI finding",
          len(full["members"]) == 1)
    check("origin stays manual", full.get("origin") == "manual")

    print("4. NO CACHE — no steam-meta cache still records")
    d2 = build_data_dir(with_steam_meta=False)
    os.environ["LUDODEX_DATA"] = d2
    srv.DATA = d2                       # the module read DATA at import
    srv._record_accepted_collections(
        [finding("ys i", "Ys I & II Chronicles+", ["Ys I", "Ys II"])])
    check("records normally with nothing to dedupe against",
          "ys i" in load(compilations, d2))

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
