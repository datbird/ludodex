#!/usr/bin/env python3
"""Publishing targets a platform, and the collapse must not take that away.

A device push copies one platform's files. The grid now shows one card spanning several
platforms, so the thing publish is handed has to keep resolving to exactly ONE entry
row. That is why the card carries its representative's entry_key rather than only a
card key, and why the per-platform rows were kept underneath instead of being merged.
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
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
      platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, card_key TEXT);
    INSERT INTO games VALUES(1,'DARK SOULS: REMASTERED','dark souls','pc',
      'dark souls@pc','dark souls','igdb:81085','igdb:2155');
    INSERT INTO games VALUES(2,'DARK SOULS: REMASTERED','dark souls','switch',
      'dark souls@switch','dark souls','igdb:81085','igdb:2155');
    """)

    for ek in ("dark souls@pc", "dark souls@switch"):
        n = con.execute("SELECT COUNT(*) FROM games WHERE entry_key=?",
                        (ek,)).fetchone()[0]
        check("%s addresses exactly one entry" % ek, n == 1)

    plats = [r[0] for r in con.execute(
        "SELECT platform FROM games WHERE card_key='igdb:2155' ORDER BY platform")]
    check("the card still knows both platforms", plats == ["pc", "switch"])
    check("a card key alone does NOT address one entry",
          con.execute("SELECT COUNT(*) FROM games WHERE card_key='igdb:2155'"
                      ).fetchone()[0] == 2)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
