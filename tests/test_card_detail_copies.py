#!/usr/bin/env python3
"""One card, several copies.

The detail page used to be one platform entry with an `also_owned_on` chip strip
pointing at its siblings. Now the card IS the game, so the siblings are its copies, and
the page has to accept the card key as well as the entry key so old links keep working.

`game_detail` opens six ATTACHed database files through `lib()`, so the copies query
lives in `_card_copies`, a helper that takes a connection. That is what this drives.
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
    from server import app as srv

    # --- the two key shapes cannot collide ---
    check("_split_entry_key still splits an entry key",
          srv._split_entry_key("dark souls@pc") == ("dark souls", "pc"))
    check("an igdb card key is recognised",
          srv._card_key_lookup("igdb:2155") == "igdb:2155")
    check("a title card key is recognised",
          srv._card_key_lookup("title:some rom") == "title:some rom")
    check("an entry key is not a card key",
          srv._card_key_lookup("dark souls@pc") is None)
    check("a bare norm_key is not a card key",
          srv._card_key_lookup("dark souls") is None)

    # --- the edition label ---
    check("the label is what the card title does not carry",
          srv._edition_label("DARK SOULS: REMASTERED", "DARK SOULS") == "REMASTERED")
    check("an exact match has no label",
          srv._edition_label("Dark Souls", "Dark Souls") == "")
    check("an unrelated title has no label",
          srv._edition_label("Mega Man 2", "Dark Souls") == "")
    check("an empty title is safe", srv._edition_label("", "Dark Souls") == "")

    # --- copies ---
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
      platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, card_key TEXT,
      content_kind TEXT);
    INSERT INTO games VALUES(1,'DARK SOULS: REMASTERED','dark souls','pc',
      'dark souls@pc','dark souls','igdb:81085','igdb:2155',NULL);
    INSERT INTO games VALUES(2,'DARK SOULS: REMASTERED','dark souls','switch',
      'dark souls@switch','dark souls','igdb:81085','igdb:2155',NULL);
    INSERT INTO games VALUES(3,'DARK SOULS: Prepare To Die Edition',
      'dark souls prepare to die','pc','dark souls prepare to die@pc',
      'dark souls prepare to die','title:dark souls prepare to die','igdb:2155',NULL);
    INSERT INTO games VALUES(4,'Dark Souls II','dark souls 2','pc',
      'dark souls 2@pc','dark souls 2','igdb:2368','igdb:2368',NULL);
    """)

    copies = srv._card_copies(con, "igdb:2155", "DARK SOULS")
    check("the card has three copies", len(copies) == 3)
    check("every copy is separately addressable",
          len({c["entry_key"] for c in copies}) == 3)
    check("no copy from another card leaks in",
          all(c["entry_key"] != "dark souls 2@pc" for c in copies))
    plats = sorted(c["platform"] for c in copies)
    check("the copies carry their platforms", plats == ["pc", "pc", "switch"])
    labels = {c["entry_key"]: c["edition"] for c in copies}
    check("the Remastered copies are labelled",
          labels["dark souls@pc"] == "REMASTERED")
    check("the Prepare To Die copy is labelled",
          labels["dark souls prepare to die@pc"] == "Prepare To Die Edition")
    check("copies are ordered deterministically",
          [c["entry_key"] for c in copies]
          == [c["entry_key"] for c in srv._card_copies(con, "igdb:2155", "DARK SOULS")])

    check("a card with one copy still returns a list",
          len(srv._card_copies(con, "igdb:2368", "Dark Souls II")) == 1)
    check("an unknown card returns nothing",
          srv._card_copies(con, "igdb:999999", "Nothing") == [])

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
