#!/usr/bin/env python3
"""Contract test for catalog_patch.materialize_members (DESIGN §13).

Offline, no network, no AI, no live DBs — a synthetic catalog + collections store.
Asserts the four behaviors the reviewers found untested:

  1. CREATE     — a bundle-only member becomes a real entry (state='have',
                  via_collection provenance, 'via:' summary).
  2. SKIP       — a member owned standalone is never doubled.
  3. SATISFY    — a member whose only entry is wishlist gets the via-ownership
                  attached and leaves the Wanted view (§13.3 want-satisfaction).
  4. RECONCILE  — deleting the collection removes the phantom entry it created and
                  restores the satisfied want; a shrunk member list drops only the
                  removed member.

Plus idempotency: a second run of every step changes nothing.
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog_patch                                    # noqa: E402
import compilations                                     # noqa: E402

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


def add_entry(con, title, nk, plat="pc", state="have", source="steam", wanted=0):
    cur = con.execute(
        "INSERT INTO games(canonical_title,norm_key,platform,entry_key,base_key,"
        "game_key,n_sources,n_kinds,sources_summary,has_emulation,has_steam,has_gog,"
        "has_epic,has_itch,has_archive,in_playnite,in_launchbox,wanted) "
        "VALUES(?,?,?,?,?,?,?,0,?,0,1,0,0,0,0,0,0,?)",
        (title, nk, plat, "%s@%s" % (nk, plat), nk, "title:%s" % nk,
         1 if state else 0, source, wanted))
    gid = cur.lastrowid
    if state:
        con.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw,"
                    "state) VALUES(?,?,?,?,?,?)", (gid, source, plat, "1", title, state))
    return gid


def q1(con, sql, *args):
    r = con.execute(sql, args).fetchone()
    return r[0] if r else None


def main():
    data = tempfile.mkdtemp(prefix="ludodex-mm-")
    con = fresh_catalog()

    # the owned bundle + one standalone-owned member + one wishlist-only member
    add_entry(con, "Retro Mega Pack", "retro mega pack")
    add_entry(con, "Alpha Quest", "alpha quest")                       # owned standalone
    wl = add_entry(con, "Beta Blaster", "beta blaster", state=None, wanted=1)
    compilations.set_collection(data, "retro mega pack", "Retro Mega Pack", [
        {"title": "Alpha Quest", "platform": "PC"},
        {"title": "Beta Blaster", "platform": "PC"},
        {"title": "Gamma Racer", "platform": "Game Boy Advance"},
    ], origin="ai")

    print("materialize: create + skip + satisfy")
    n = catalog_patch.materialize_members(con, data)
    check("run reports work done", n > 0)
    check("bundle-only member CREATED as a real entry",
          q1(con, "SELECT COUNT(*) FROM games WHERE base_key='gamma racer'") == 1)
    check("created member is owned via the collection",
          q1(con, "SELECT COUNT(*) FROM sources s JOIN games g ON g.id=s.game_id "
                  "WHERE g.base_key='gamma racer' AND s.state='have' "
                  "AND s.via_collection='retro mega pack'") == 1)
    check("created member carries the 'via:' summary",
          (q1(con, "SELECT sources_summary FROM games WHERE base_key='gamma racer'")
           or "").startswith("via:"))
    check("member platform resolved through platmap ('Game Boy Advance' -> gba)",
          q1(con, "SELECT platform FROM games WHERE base_key='gamma racer'") == "gba")
    check("standalone-owned member NOT doubled",
          q1(con, "SELECT COUNT(*) FROM games WHERE base_key='alpha quest'") == 1)
    check("wishlist member got the via-ownership attached (§13.3)",
          q1(con, "SELECT COUNT(*) FROM sources WHERE game_id=? AND state='have' "
                  "AND via_collection='retro mega pack'", wl) == 1)
    check("satisfied want left the Wanted view",
          q1(con, "SELECT wanted FROM games WHERE id=?", wl) == 0)

    print("idempotency")
    check("second run is a no-op", catalog_patch.materialize_members(con, data) == 0)

    print("reconcile: member-list shrink")
    compilations.set_collection(data, "retro mega pack", "Retro Mega Pack", [
        {"title": "Alpha Quest", "platform": "PC"},
        {"title": "Beta Blaster", "platform": "PC"},
    ], origin="ai")                                   # Gamma Racer removed
    catalog_patch.materialize_members(con, data)
    check("removed member's phantom entry is gone",
          q1(con, "SELECT COUNT(*) FROM games WHERE base_key='gamma racer'") == 0)
    check("kept member's credit survives",
          q1(con, "SELECT COUNT(*) FROM sources WHERE game_id=? AND "
                  "via_collection='retro mega pack'", wl) == 1)

    print("reconcile: collection deleted")
    compilations.clear_collection(data, "retro mega pack")
    catalog_patch.materialize_members(con, data)
    check("satisfied want RESTORED when its only ownership was the credit",
          q1(con, "SELECT wanted FROM games WHERE id=?", wl) == 1)
    check("no stray via rows remain",
          q1(con, "SELECT COUNT(*) FROM sources WHERE via_collection IS NOT NULL") == 0)
    check("standalone-owned member untouched",
          q1(con, "SELECT COUNT(*) FROM games WHERE base_key='alpha quest'") == 1)
    check("delete-then-run is idempotent",
          catalog_patch.materialize_members(con, data) == 0)

    print("\nALL PASS (%d checks)" % len(PASS))


if __name__ == "__main__":
    main()
