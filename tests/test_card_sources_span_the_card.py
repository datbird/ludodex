#!/usr/bin/env python3
"""The "In your library" table lists every copy on the card, not one entry's.

Live 2026-08-26: the Dark Souls: Remastered page said "also owned on Switch" in the hero
and then listed a single row underneath, `steam / pc`. The Nintendo purchase was missing.
The page told you two different things about the same card.

The table was right until the cards landed. A card used to BE one platform, so "this
entry's sources" and "this card's sources" were the same list. Collapsing platforms split
those apart, and the hero was updated while the table was not.

Its own heading promises "one row per format, store entry, or console", so a console you
own it on belongs there.

Three things read this list and all three want the card: the table, the store chips under
"Identified via", and the `identified` test in the UI (a card is identified when ANY copy
has a store source).
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
      platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, card_key TEXT);
    CREATE TABLE sources(game_id INTEGER, source TEXT, platform TEXT, source_id TEXT,
      title_raw TEXT, detail TEXT, state TEXT DEFAULT 'have', via_collection TEXT);
    -- the real rows: one card, two platforms, two different stores
    INSERT INTO games VALUES(1,'DARK SOULS: REMASTERED','dark souls','pc',
      'dark souls@pc','dark souls','igdb:81085','igdb:81085');
    INSERT INTO games VALUES(2,'DARK SOULS: REMASTERED','dark souls','switch',
      'dark souls@switch','dark souls','igdb:81085','igdb:81085');
    INSERT INTO games VALUES(3,'Some Other Game','other','pc',
      'other@pc','other','igdb:999','igdb:999');
    INSERT INTO sources VALUES(1,'steam','pc','374320','DARK SOULS: REMASTERED','','have',NULL);
    INSERT INTO sources VALUES(2,'nintendo','switch','7001','DARK SOULS: REMASTERED','','have',NULL);
    INSERT INTO sources VALUES(3,'steam','pc','111','Some Other Game','','have',NULL);
    """)
    con.commit()

    # opened by the PC entry, which is what the grid's representative gives you
    rows = srv._card_sources(con, "igdb:81085", 1)
    got = sorted((r["source"], r["platform"]) for r in rows)
    check("both purchases are listed", got == [("nintendo", "switch"), ("steam", "pc")], got)
    check("the Switch copy is no longer missing",
          ("nintendo", "switch") in got, got)
    check("no other card's sources leak in",
          all(r["source_id"] != "111" for r in rows), rows)

    # and from the OTHER side: opening the Switch entry gives the same list
    rows2 = srv._card_sources(con, "igdb:81085", 2)
    check("the list does not depend on which copy you opened",
          sorted((r["source"], r["platform"]) for r in rows2) == got)

    # a single-platform card is unchanged
    solo = srv._card_sources(con, "igdb:999", 3)
    check("a one-platform card still lists its one row", len(solo) == 1, solo)

    # an un-rebuilt catalog has no card_key: fall back to the entry, exactly as before
    old = sqlite3.connect(":memory:")
    old.row_factory = sqlite3.Row
    old.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT, entry_key TEXT);
    CREATE TABLE sources(game_id INTEGER, source TEXT, platform TEXT, source_id TEXT,
      title_raw TEXT, detail TEXT);
    INSERT INTO games VALUES(1,'dark souls','dark souls@pc');
    INSERT INTO sources VALUES(1,'steam','pc','374320','DARK SOULS','');
    """)
    old.commit()
    check("it degrades to the entry without a card_key column",
          len(srv._card_sources(old, None, 1)) == 1)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
