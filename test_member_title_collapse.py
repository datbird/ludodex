#!/usr/bin/env python3
"""Contract test for member-title collapse in catalog_patch (DESIGN §13).

Offline, no network, no AI, no live DBs — a synthetic catalog + collections store.

The defect this pins: a store can grant SEVERAL apps for ONE purchase and name them
after the games inside ("Ys I & II Chronicles+" grants apps called "Ys I" and "Ys II").
The AI then enumerates that compilation's members under their FULL original titles
("Ys II: Ancient Ys Vanished - The Final Chapter"), which normalize to a different
base_key than the app you own. So materialization created a PHANTOM entry beside the
game you already own — four Ys entries where there should be two, the opposite of §13,
where a member you own gets a credit and never a second entry.

The collapse is evidence-gated, NOT title-shaped: a member may only land on an entry
granted by the SAME PURCHASE as the collection (same canonical appid). Title shape
alone is not enough and the live catalog proved it — "Tomb Raider: Chronicles" would
have been swallowed by the separately-bought 1996 "Tomb Raider", and "Contra: Hard
Corps" by "Contra".

  1. COLLAPSE   — a sibling app of the same purchase credits the owned entry, no phantom.
  2. SEQUEL     — "<title> N" never collapses into "<title>".
  3. SELF       — a member resolving to the collection's OWN entry is skipped.
  3b. OTHER BUY — a same-named head owned via a DIFFERENT purchase is never taken.
  4. NO MATCH   — with no sibling to land on, the full title is created as before.
  5. REPAIR     — a phantom created by the OLD resolver is removed on the next run.

Run:  ./.venv/bin/python test_member_title_collapse.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog_patch                                    # noqa: E402
import compilations                                     # noqa: E402
from titlenorm import norm                              # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def fresh_catalog():
    con = sqlite3.connect(":memory:")
    con.executescript("""
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
    return con


def add_entry(con, title, appid="", plat="pc", state="have", summary="steam"):
    nk = norm(title)
    cur = con.execute(
        "INSERT INTO games(canonical_title,norm_key,platform,entry_key,base_key,"
        "game_key,n_sources,n_kinds,sources_summary,has_emulation,has_steam,has_gog,"
        "has_epic,has_itch,has_archive,in_playnite,in_launchbox,wanted) "
        "VALUES(?,?,?,?,?,?,?,0,?,0,1,0,0,0,0,0,0,0)",
        (title, nk, plat, "%s@%s" % (nk, plat), nk, "title:%s" % nk,
         1 if state else 0, summary))
    gid = cur.lastrowid
    if state:
        con.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw,"
                    "state) VALUES(?,'steam',?,?,?,?)", (gid, plat, appid, title, state))
    return gid


def data_dir(appid_to_canonical=None):
    """A fresh LUDODEX_DATA holding just the steam-meta cache the resolver consults."""
    d = tempfile.mkdtemp(prefix="ludodex-collapse-")
    if appid_to_canonical:
        sm = sqlite3.connect(os.path.join(d, "steam-meta.sqlite"))
        sm.execute("CREATE TABLE steam_meta(appid TEXT PRIMARY KEY, "
                   "canonical_appid TEXT, store_name TEXT)")
        for a, c in appid_to_canonical.items():
            sm.execute("INSERT INTO steam_meta(appid,canonical_appid) VALUES(?,?)", (a, c))
        sm.commit()
        sm.close()
    return d


def entries(con):
    return {r[0]: r[1] for r in con.execute("SELECT base_key, canonical_title FROM games")}


def credits(con, base_key):
    return con.execute(
        "SELECT COUNT(*) FROM sources s JOIN games g ON g.id=s.game_id "
        "WHERE g.base_key=? AND s.via_collection IS NOT NULL", (base_key,)).fetchone()[0]


# Ys I & II Chronicles+ — ONE purchase, two granted apps.
YS_META = {"223810": "223810", "223870": "223810"}


def main():
    print("1. COLLAPSE — a sibling app of the same purchase takes the credit")
    d = data_dir(YS_META)
    con = fresh_catalog()
    add_entry(con, "Ys I", "223810")            # the collection's own purchase
    add_entry(con, "Ys II", "223870")           # the other app the SAME purchase granted
    compilations.set_collection(d, norm("Ys I"), "Ys I & II Chronicles+", [
        {"title": "Ys II: Ancient Ys Vanished - The Final Chapter",
         "platform": "PC", "year": 1988}], origin="ai")
    catalog_patch.materialize_members(con, d)
    e = entries(con)
    check("no phantom full-title entry created",
          norm("Ys II: Ancient Ys Vanished - The Final Chapter") not in e)
    check("the owned short-title entry still exists", norm("Ys II") in e)
    check("exactly two entries, not three", len(e) == 2)
    check("owned member takes the read-time credit, not a via row",
          credits(con, norm("Ys II")) == 0)

    print("2. SEQUEL — '<title> N' is never swallowed by '<title>'")
    d = data_dir({"1": "1", "2": "1"})
    con = fresh_catalog()
    add_entry(con, "Sonic Mega Collection", "1")
    add_entry(con, "Sonic the Hedgehog", "2")   # same purchase, so only shape protects it
    compilations.set_collection(d, norm("Sonic Mega Collection"), "Sonic Mega Collection",
                                [{"title": "Sonic the Hedgehog 2", "platform": "Sega Genesis"}],
                                origin="ai")
    catalog_patch.materialize_members(con, d)
    e = entries(con)
    check("the sequel got its own entry", norm("Sonic the Hedgehog 2") in e)
    check("the predecessor was not credited for it",
          credits(con, norm("Sonic the Hedgehog")) == 0)

    print("3. SELF — a member resolving to the collection's own entry is skipped")
    d = data_dir(YS_META)
    con = fresh_catalog()
    add_entry(con, "Ys I", "223810")
    compilations.set_collection(d, norm("Ys I"), "Ys I & II Chronicles+", [
        {"title": "Ys I: Ancient Ys Vanished", "platform": "PC", "year": 1987}],
        origin="ai")
    catalog_patch.materialize_members(con, d)
    e = entries(con)
    check("no self-credit entry created", len(e) == 1)
    check("the collection's own entry is untouched", norm("Ys I") in e)
    check("no via row points the collection at itself", credits(con, norm("Ys I")) == 0)

    print("3b. DIFFERENT PURCHASE — a same-named head you own separately is NOT taken")
    # "Tomb Raider: Chronicles" is not "Tomb Raider" (1996). You own the 1996 game as
    # its own Steam purchase; the remaster bundle is a different product. Collapsing on
    # title shape alone silently merged them — the live catalog proved it.
    d = data_dir({"2478970": "2478970", "224960": "224960"})
    con = fresh_catalog()
    add_entry(con, "Tomb Raider IV-VI Remastered", "2478970")
    add_entry(con, "Tomb Raider", "224960")
    compilations.set_collection(d, norm("Tomb Raider IV-VI Remastered"),
                                "Tomb Raider IV-VI Remastered",
                                [{"title": "Tomb Raider: Chronicles", "platform": "PC"}],
                                origin="ai")
    catalog_patch.materialize_members(con, d)
    e = entries(con)
    check("the subtitled game got its OWN entry", norm("Tomb Raider: Chronicles") in e)
    check("the separately-owned 1996 game was not credited for it",
          credits(con, norm("Tomb Raider")) == 0)

    print("4. NO MATCH — no sibling to land on, so the full title is created")
    d = data_dir()                              # no steam-meta cache at all
    con = fresh_catalog()
    add_entry(con, "Some Bundle", "10")
    add_entry(con, "Unowned Game", "11")
    compilations.set_collection(d, norm("Some Bundle"), "Some Bundle", [
        {"title": "Unowned Game: The Subtitle", "platform": "PC"}], origin="ai")
    catalog_patch.materialize_members(con, d)
    e = entries(con)
    check("full-title member entry created as before",
          norm("Unowned Game: The Subtitle") in e)

    print("5. REPAIR — a phantom from the OLD resolver is removed on the next run")
    d = data_dir(YS_META)
    con = fresh_catalog()
    add_entry(con, "Ys I", "223810")
    add_entry(con, "Ys II", "223870")
    ph = add_entry(con, "Ys II: Ancient Ys Vanished - The Final Chapter",
                   plat="pc-8801", state=None, summary="via:Ys I & II Chronicles+")
    con.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw,detail,"
                "state,via_collection) VALUES(?,'steam','pc-8801','','Ys II','x','have',?)",
                (ph, norm("Ys I")))
    con.execute("UPDATE games SET n_sources=1 WHERE id=?", (ph,))
    compilations.set_collection(d, norm("Ys I"), "Ys I & II Chronicles+", [
        {"title": "Ys II: Ancient Ys Vanished - The Final Chapter",
         "platform": "PC", "year": 1988}], origin="ai")
    catalog_patch.materialize_members(con, d)
    e = entries(con)
    check("the stale phantom is gone",
          norm("Ys II: Ancient Ys Vanished - The Final Chapter") not in e)
    check("the two real entries survive", len(e) == 2)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
