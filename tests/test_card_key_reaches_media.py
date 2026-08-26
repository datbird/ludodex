#!/usr/bin/env python3
"""Every endpoint that takes a game key must understand a CARD key.

Live regression, found by looking at the app: game detail pages lost their hero and
background art. The grid now opens a game by its CARD key ("igdb:2155"), and
`_split_entry_key` only knew two shapes, "<norm_key>@<platform>" and a bare norm_key. A
card key has no "@", so it came through whole as the title key, and every media query
asked for art belonging to a game called "igdb:2155". There is none, so the panel showed
nothing and reported no error.

Twenty call sites take a key through that one function, so the fix belongs THERE and not
in the two endpoints that happened to be noticed. This pins the function, because the
next key shape will otherwise do this again.
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
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    data = os.environ["LUDODEX_DATA"]

    # a real catalog on disk, because _split_entry_key has to read one to resolve a card
    lib = sqlite3.connect(os.path.join(data, "game-library.sqlite"))
    lib.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
      platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, card_key TEXT,
      card_title TEXT, n_sources INTEGER DEFAULT 1, has_emulation INT DEFAULT 0);
    INSERT INTO games VALUES(1,'DARK SOULS: REMASTERED','dark souls','pc',
      'dark souls@pc','dark souls','igdb:81085','igdb:2155','DARK SOULS',1,0);
    INSERT INTO games VALUES(2,'DARK SOULS: REMASTERED','dark souls','switch',
      'dark souls@switch','dark souls','igdb:81085','igdb:2155','DARK SOULS',1,0);
    INSERT INTO games VALUES(3,'Mega Man 2','mega man 2','nes',
      'mega man 2@nes','mega man 2','igdb:1715','igdb:170742','Mega Man 2',1,1);
    """)
    lib.commit()
    lib.close()

    from server import app as srv

    # the two shapes that already worked
    check("an entry key still splits",
          srv._split_entry_key("dark souls@pc") == ("dark souls", "pc"))
    check("a bare norm_key still passes through",
          srv._split_entry_key("dark souls") == ("dark souls", None))

    # THE REGRESSION: a card key must resolve to a real entry, not to itself
    base, plat = srv._split_entry_key("igdb:2155")
    check("a card key yields a REAL norm_key", base == "dark souls")
    check("and never the card key itself", base != "igdb:2155")
    check("and it carries a platform, so media stays siloed", plat in ("pc", "switch"))

    b2, p2 = srv._split_entry_key("igdb:170742")
    check("a second card resolves to its own entry", (b2, p2) == ("mega man 2", "nes"))

    # deterministic: the same card must not wander between calls
    check("resolution is stable",
          srv._split_entry_key("igdb:2155") == (base, plat))

    # an unknown card must not invent an entry
    check("an unknown card key falls back rather than guessing",
          srv._split_entry_key("igdb:999999") == ("igdb:999999", None))
    check("a title card key with no row falls back too",
          srv._split_entry_key("title:nothing here") == ("title:nothing here", None))

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
