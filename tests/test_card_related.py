#!/usr/bin/env python3
"""What a card shows INSTEAD of merging: other versions, and the series.

The fold rule is deliberately narrow, so "Dark Souls: Remastered" is its own card rather
than hidden inside "Dark Souls". The relationship is not thrown away, it is shown. Two
tiers, because they answer different questions:

  * OTHER VERSIONS — same product lineage. Prepare To Die Edition and Remastered both
    point at Dark Souls in IGDB's parent graph. "Is there another way to own this game?"
  * SERIES — the franchise. Dark Souls II, III, Scholar of the First Sin. "What else is
    part of this?"

Both come from data the catalog already holds: the IGDB parent links, and the `series`
attribute, which is on 820 games today. Owned games only: a list of things you do not
have is Discover's job, not this panel's.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond, detail=""):
    PASS.append((label, bool(cond)))
    print("  %s   %s%s" % ("ok " if cond else "FAIL", label,
                           "" if cond else "   <- " + str(detail)[:200]))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    from server import app as srv

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
      platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, card_key TEXT,
      card_title TEXT, has_emulation INT DEFAULT 0, n_sources INTEGER DEFAULT 1,
      wanted INT DEFAULT 0);
    CREATE TABLE game_attributes(game_id INTEGER, kind TEXT, value TEXT);
    -- the real Dark Souls shelf under the corrected rule: five products, five cards
    INSERT INTO games VALUES(1,'DARK SOULS: Prepare To Die Edition',
      'dark souls prepare to die','pc','dark souls prepare to die@pc',
      'dark souls prepare to die','igdb:21040','igdb:21040',
      'DARK SOULS: Prepare To Die Edition',0,1,0);
    INSERT INTO games VALUES(2,'DARK SOULS: REMASTERED','dark souls','pc',
      'dark souls@pc','dark souls','igdb:81085','igdb:81085','DARK SOULS: REMASTERED',0,1,0);
    INSERT INTO games VALUES(3,'DARK SOULS: REMASTERED','dark souls','switch',
      'dark souls@switch','dark souls','igdb:81085','igdb:81085','DARK SOULS: REMASTERED',0,1,0);
    INSERT INTO games VALUES(4,'Dark Souls II','dark souls 2','pc',
      'dark souls 2@pc','dark souls 2','igdb:2368','igdb:2368','Dark Souls II',0,1,0);
    INSERT INTO games VALUES(5,'DARK SOULS III','dark souls 3','pc',
      'dark souls 3@pc','dark souls 3','igdb:11133','igdb:11133','DARK SOULS III',0,1,0);
    INSERT INTO games VALUES(6,'Elden Ring','elden ring','pc',
      'elden ring@pc','elden ring','igdb:119133','igdb:119133','Elden Ring',0,1,0);
    -- a WISHLIST entry: in the library, but not owned, so it must not be listed
    INSERT INTO games VALUES(7,'Dark Souls','dark souls base','pc',
      'dark souls base@pc','dark souls base','igdb:2155','igdb:2155','Dark Souls',0,1,1);
    """)
    for gid in (1, 2, 3, 4, 5):
        con.execute("INSERT INTO game_attributes VALUES(?,'series','Dark Souls')", (gid,))
    con.execute("INSERT INTO game_attributes VALUES(6,'series','Elden Ring')")
    con.commit()

    # IGDB's parent graph: both the edition and the remaster descend from Dark Souls
    graph = {2155: (0, None, None), 81085: (9, None, 2155), 21040: (3, 2155, None),
             2368: (0, None, None), 11133: (0, None, None), 119133: (0, None, None)}

    rel = srv._card_related(con, "igdb:21040", graph)

    versions = rel.get("versions") or []
    vkeys = {v["card_key"] for v in versions}
    check("Prepare To Die lists the Remastered as another version",
          "igdb:81085" in vkeys, versions)
    check("and does not list itself", "igdb:21040" not in vkeys, vkeys)
    check("a sequel is NOT another version of this game",
          "igdb:2368" not in vkeys, vkeys)

    series = rel.get("series") or []
    skeys = {x["card_key"] for x in series}
    check("the series lists the sequels", {"igdb:2368", "igdb:11133"} <= skeys, skeys)
    check("the series does not repeat this card", "igdb:21040" not in skeys, skeys)
    check("a different franchise stays out", "igdb:119133" not in skeys, skeys)
    check("a WISHLIST game is not listed: owned only",
          "igdb:2155" not in skeys and "igdb:2155" not in vkeys, (skeys, vkeys))
    check("the series is named", rel.get("series_name") == "Dark Souls", rel)

    # each related card must be openable, and appear ONCE however many platforms it has
    check("a related card carries a title", all(x.get("title") for x in versions + series))
    remaster = [v for v in versions if v["card_key"] == "igdb:81085"]
    check("a two-platform product appears once", len(remaster) == 1, versions)
    check("and reports both platforms",
          set((remaster[0].get("platforms") or "").split(",")) == {"pc", "switch"},
          remaster)

    # a card with no relatives says so cleanly rather than erroring
    solo = srv._card_related(con, "igdb:119133", graph)
    check("a lone game has no versions", solo.get("versions") == [])
    check("and an empty series rather than itself", solo.get("series") == [])

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
