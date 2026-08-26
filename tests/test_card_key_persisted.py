#!/usr/bin/env python3
"""Every catalog row carries a card_key, and the mirror can answer the fold questions.

Two halves, because `build_library.py` runs its whole build at module scope and cannot
be imported. The MIRROR READERS live in `igdb_mirror`, which is importable, so they are
driven for real against a seeded mirror. `build_library` itself is checked the way
`test_ingest_order` and `test_member_title_collapse` check it: by reading its source,
which is the established convention here.

The DDL half matters more than it looks. A row whose card_key is NULL vanishes from a
GROUPed grid with no error at all, so the column has to exist and every insert has to
fill it.
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

    # --- the readers, against a mirror holding the real Dark Souls linkage ---
    import igdb_mirror

    check("an absent mirror yields an empty graph, not a crash",
          igdb_mirror.fold_graph() == {})

    mir = sqlite3.connect(os.path.join(data, "igdb-catalog.sqlite"))
    mir.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, name TEXT, slug TEXT, norm_key TEXT,
                       game_type INTEGER, year INTEGER, first_release_date INTEGER,
                       platforms TEXT, parent_game INTEGER, version_parent INTEGER,
                       updated_at INTEGER, seen_at INTEGER);
    INSERT INTO games(id,name,norm_key,game_type,parent_game,version_parent)
      VALUES(2155,'Dark Souls','dark souls',0,NULL,NULL);
    INSERT INTO games(id,name,norm_key,game_type,parent_game,version_parent)
      VALUES(81085,'Dark Souls: Remastered','dark souls remastered',9,2155,NULL);
    INSERT INTO games(id,name,norm_key,game_type,parent_game,version_parent)
      VALUES(21040,'Dark Souls: Prepare to Die Edition',
             'dark souls prepare to die edition',3,NULL,2155);
    """)
    mir.commit()
    mir.close()

    graph = igdb_mirror.fold_graph()
    check("the graph reads game_type", graph[81085][0] == 9)
    check("the graph reads version_parent", graph[21040][1] == 2155)
    check("the graph reads parent_game", graph[81085][2] == 2155)
    check("a root has no parents", graph[2155] == (0, None, None))

    names = igdb_mirror.names()
    check("names are read from the mirror", names[2155] == "Dark Souls")


    # the reader feeds the pure rule, end to end
    import cardkey
    check("a remaster is NOT folded into the original",
          cardkey.card_key_for("igdb:81085", graph) == "igdb:81085")

    # --- build_library, read as source (it cannot be imported: it builds on import) ---
    bl = open(os.path.join(root, "ludodex", "build_library.py"), encoding="utf-8").read()
    ddl = bl[bl.index("CREATE TABLE games"):bl.index("CREATE TABLE sources")]
    check("the games DDL declares card_key", "card_key" in ddl)
    check("game_key is untouched", "game_key" in ddl)
    check("entry_key is untouched", "entry_key" in ddl)
    check("build_library imports the fold rule", "import cardkey" in bl)
    check("build_library loads the fold graph", "igdb_mirror.fold_graph()" in bl)
    check("every games INSERT names card_key",
          bl.count("INSERT INTO games(") == bl.count("card_key,")
          or all("card_key" in seg[:400]
                 for seg in bl.split("INSERT INTO games(")[1:]))

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
