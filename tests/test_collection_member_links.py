#!/usr/bin/env python3
"""A member the user can reach must be told apart from one they cannot.

The collection panel lists a compilation's members, and each member that exists in the
catalog is an entry of its own — one click away, with no click to make. Making them all
navigable is wrong in the other direction: a member list is the AI's account of what a
bundle CONTAINS, and a game that was never materialized (not owned, or the bundle names
something outside the library) has no entry to open. A link to it 404s.

So the detail payload resolves each member to an `entry_key`, or None. The UI links
exactly the ones that resolve, and the rest stay plain text.

Members join the catalog on the normalized title — `base_key`, the same key
`materialize_members` stamps — so this must not match on `norm_key` alone: a
per-platform split gives several entries one base_key, and the member should land on
one of them rather than nowhere.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-collmemlink-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    from server import app as srv

    lib = sqlite3.connect(srv.LIBRARY_DB)
    rows = [("champions of krynn", "Champions of Krynn", "champions of krynn@pc"),
            ("death knights of krynn", "Death Knights of Krynn",
             "death knights of krynn@pc")]
    for nk, title, ek in rows:
        lib.execute("INSERT INTO games(canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,n_sources,n_kinds,sources_summary,wanted) "
                    "VALUES(?,?,'pc',?,?,?,1,0,'steam',0)",
                    (title, nk, ek, nk, "title:" + nk))
    lib.commit()

    members = [{"title": "Champions of Krynn"},
               {"title": "Death Knights of Krynn"},
               {"title": "The Dark Queen of Krynn"}]     # owned by nobody here
    import compilations
    compilations.set_collection(D, "dungeons and dragons krynn series",
                                "Dungeons & Dragons: Krynn Series", members)

    con = srv.lib()
    try:
        coll = srv._collection_with_links(con, "dungeons and dragons krynn series")
    finally:
        con.close()
    by = {m["member_title"]: m for m in coll["members"]}

    check("a member that exists resolves to its entry",
          by["Champions of Krynn"]["entry_key"] == "champions of krynn@pc")
    check("every existing member resolves, not just the first",
          by["Death Knights of Krynn"]["entry_key"] == "death knights of krynn@pc")
    check("a member with no catalog entry resolves to None",
          by["The Dark Queen of Krynn"]["entry_key"] is None)
    check("the member list is otherwise untouched", len(coll["members"]) == 3)
    check("the collection's own fields survive",
          coll["name"] == "Dungeons & Dragons: Krynn Series")
    con = srv.lib()
    try:
        check("an entry that is not a collection still returns None",
              srv._collection_with_links(con, "champions of krynn") is None)
    finally:
        con.close()

    print("\n  %d/%d passed" % (sum(1 for _, c in PASS if c), len(PASS)))


if __name__ == "__main__":
    main()
