#!/usr/bin/env python3
"""`_backfill_game_key` is documented as a cheap no-op. It has to actually be one.

    Cheap no-op once every row is stamped — guarded by an existence check.

The existence check is the LAST thing it reaches. Before it get there the function has
already run a `game_key LIKE 'title:%@%'` probe (a full table scan — SQLite cannot use
an index for LIKE under the default collation), an ATTACH + library-wide UPDATE for the
bundle repair, and a correlated-subquery UPDATE against the whole catalog for the
entry-derived repair. `con_index()` calls it unconditionally, and every media path opens
through `con_index()` — so a one-game wand pull paid a library-wide repair sweep, and so
did the next one, and the one after that, with the catalog unchanged between them.

The repairs themselves are right and must not move: a row can be STAMPED and still
wrong, so they deliberately run before the fully-stamped early-return. What was missing
is a guard on the only question that decides whether they can find anything: has any
input CHANGED since the last completed repair?

The inputs are the catalog, the metadata cache and the media rows themselves. Guarding
on those three is honest — and every seam that can make a stamp stale (a fetch's
`put()`, an identity change via `invalidate_resmap()`) clears the memo itself, so this
is a cache with an explicit invalidation rather than a hope.

Offline. No network.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-backfill-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import media_fetch                                             # noqa: E402
import media_index                                             # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


LIB = os.path.join(DATA, "game-library.sqlite")


def catalog(rows):
    lib = sqlite3.connect(LIB)
    lib.execute("CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY, norm_key TEXT, "
                "base_key TEXT, platform TEXT, game_key TEXT)")
    lib.execute("DELETE FROM games")
    for bk, gk in rows:
        lib.execute("INSERT INTO games(norm_key,base_key,platform,game_key) "
                    "VALUES(?,?,'pc',?)", (bk, bk, gk))
    lib.commit()
    lib.close()


def main():
    catalog([("shinobi 3", "igdb:500")])

    con = media_index.index_con()
    con.execute("INSERT INTO media(norm_key,system,kind,provider,ref_type,ref,ext,"
                "game_key,matched,indexed_at) VALUES('shinobi 3','','cover','esde',"
                "'file','/m/a.png','png','title:shinobi 3',1,1)")
    con.commit()

    stmts = []
    con.set_trace_callback(stmts.append)

    print("1. the first call does the work")
    media_fetch._backfill_game_key(con)
    first = len(stmts)
    check("it ran real SQL", first > 3)
    gk = con.execute("SELECT game_key FROM media WHERE ref='/m/a.png'").fetchone()[0]
    check("and repaired the stamp", gk == "igdb:500")

    print("2. calling it again with nothing changed costs nothing")
    stmts.clear()
    media_fetch._backfill_game_key(con)
    repeat = [q.strip().split()[0].upper() for q in stmts if q.strip()]
    check("no ATTACH", "ATTACH" not in repeat)
    check("no library-wide UPDATE", "UPDATE" not in repeat)
    check("no LIKE scan", not any("LIKE" in q.upper() for q in stmts))
    # what is left is the guard itself: MAX(id) on an INTEGER PRIMARY KEY, which SQLite
    # answers from the btree's right edge without scanning anything.
    check("one O(1) probe and nothing else — %r" % (repeat,), len(repeat) <= 1)
    stmts.clear()
    for _ in range(20):
        media_fetch._backfill_game_key(con)
    check("twenty more opens cost twenty probes, not twenty sweeps",
          len(stmts) <= 20 and not any("UPDATE" in q.upper() for q in stmts))

    print("3. but a CHANGED catalog is repaired, not skipped")
    catalog([("shinobi 3", "igdb:777")])          # the wand re-identified it
    stmts.clear()
    media_fetch._backfill_game_key(con)
    check("the repair ran again", len(stmts) > 3)
    gk = con.execute("SELECT game_key FROM media WHERE ref='/m/a.png'").fetchone()[0]
    check("and the new identity took", gk == "igdb:777")

    print("4. a NEW media row is stamped, not left behind by the memo")
    stmts.clear()
    con.execute("INSERT INTO media(norm_key,system,kind,provider,ref_type,ref,ext,"
                "matched,indexed_at) VALUES('shinobi 3','','logo','esde','file',"
                "'/m/b.png','png',1,1)")
    con.commit()
    media_fetch._backfill_game_key(con)
    gk = con.execute("SELECT game_key FROM media WHERE ref='/m/b.png'").fetchone()[0]
    check("the row that arrived after the memo is stamped", gk == "igdb:777")

    print("5. the seams that make a stamp stale clear the memo themselves")
    con.set_trace_callback(None)
    media_fetch._backfill_game_key(con)           # settle
    con.set_trace_callback(stmts.append)
    stmts.clear()
    media_fetch.invalidate_resmap()               # an identity changed under us
    media_fetch._backfill_game_key(con)
    check("invalidate_resmap() forces the next repair", len(stmts) > 3)

    con.set_trace_callback(None)
    media_fetch._backfill_game_key(con)           # settle
    con.set_trace_callback(stmts.append)
    stmts.clear()
    media_fetch.put(con, "shinobi 3", "cover", "igdb", "https://x/c.jpg", 5)
    con.commit()
    n_put = len(stmts)
    media_fetch._backfill_game_key(con)
    check("a fetch's put() forces the next repair", len(stmts) - n_put > 3)
    con.set_trace_callback(None)
    con.close()

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
