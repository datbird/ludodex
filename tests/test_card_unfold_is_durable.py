#!/usr/bin/env python3
"""An unfold pin is a HUMAN DECISION, and the three ways this feature nearly lost one.

Found by auditing the card work against the 2026-08-23 campaign, whose headline defect
was the FAIL-OPEN LOOKUP: a miss returns empty and the caller reads it as consent.

  1. `card_unfold` first shipped inside metadata-cache.sqlite. reset.py classes that as
     an IMPORT db, and the comment above that list promises "no human decision is lost".
     A library-scope reset would have silently deleted every pin the user had set. It now
     lives in its own curation store, named by CURATION_DBS.
  2. `build_library._card_unfolds()` swallowed a read failure into an EMPTY SET, so a
     locked or corrupt store meant the rebuild folded every card the user had separated,
     and said nothing. A failure to read the user's decisions must stop the rebuild.
  3. The mirror readers returned a PARTIALLY built dict when the query failed part way
     through, so some cards folded and some did not, differently on each run.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "ludodex"))
    data = os.environ["LUDODEX_DATA"]
    import reset
    import unfold
    import igdb_mirror

    # --- 1. a reset must treat a pin as a decision, not as a cache -----------------
    check("unfold declares its own store", hasattr(unfold, "DB"))
    dbname = os.path.basename(unfold.DB)
    check("the pins do NOT live in metadata-cache",
          dbname != "metadata-cache.sqlite")
    check("a reset names the store as CURATION", dbname in reset.CURATION_DBS)
    check("and never as an import cache", dbname not in reset.IMPORT_DBS)

    con = sqlite3.connect(unfold.DB)
    unfold.set_unfold(con, "super bit blaster xl@pc")
    con.commit()
    con.close()
    lib = reset.plan("library")
    check("a library reset keeps the pins", dbname not in lib["databases"])
    cur = reset.plan("curation")
    check("a curation reset DOES claim them, and says so",
          dbname in cur["databases"])

    # --- 2. a failed read must not read as "the user pinned nothing" --------------
    bl = open(os.path.join(root, "ludodex", "build_library.py"), encoding="utf-8").read()
    check("build_library reads the pins through the raising loader",
          "unfold.load_all()" in bl)
    check("and no longer has a local loader that can swallow the error",
          "def _card_unfolds" not in bl)
    check("and does not read the pins out of metadata-cache",
          "unfold.load(" not in bl)

    # an absent store is NOT a failure: it legitimately means no pins yet
    os.remove(unfold.DB)
    check("an absent store means no pins, not an error", unfold.load_all() == set())

    # a corrupt store IS a failure and must be loud
    with open(unfold.DB, "wb") as f:
        f.write(b"this is not a database")
    raised = False
    try:
        unfold.load_all()
    except Exception:
        raised = True
    check("a corrupt store raises rather than reporting no pins", raised)
    os.remove(unfold.DB)

    # --- 3. a mirror reader returns a COMPLETE dict or nothing --------------------
    mir = os.path.join(data, "igdb-catalog.sqlite")
    m = sqlite3.connect(mir)
    m.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, name TEXT, norm_key TEXT,
                       game_type INTEGER, parent_game INTEGER, version_parent INTEGER);
    INSERT INTO games VALUES(2155,'Dark Souls','dark souls',0,NULL,NULL);
    INSERT INTO games VALUES(81085,'Dark Souls: Remastered','dark souls remastered',
                             9,2155,NULL);
    """)
    m.commit()
    m.close()
    check("a good mirror reads complete", len(igdb_mirror.fold_graph()) == 2)
    check("and its names read complete", len(igdb_mirror.names()) == 2)
    # a rebuild asks only about the roots it holds: the whole table is 71 MB resident
    check("names() answers a narrowed ask", igdb_mirror.names({2155}) == {2155: "Dark Souls"})
    check("names() of nothing is nothing", igdb_mirror.names(set()) == {})
    check("names() ignores an id the mirror lacks", igdb_mirror.names({999999}) == {})
    check("and its title index reads", igdb_mirror.title_index().get("dark souls") == 2155)

    # a mirror predating the columns yields NOTHING, never a half-built graph
    os.remove(mir)
    m = sqlite3.connect(mir)
    m.executescript("CREATE TABLE games(id INTEGER PRIMARY KEY, name TEXT);"
                    "INSERT INTO games VALUES(1,'x');"
                    "INSERT INTO games VALUES(2,'y');")
    m.commit()
    m.close()
    check("an old mirror yields an empty graph, not a partial one",
          igdb_mirror.fold_graph() == {})
    check("an old mirror yields an empty title index",
          igdb_mirror.title_index() == {})

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
