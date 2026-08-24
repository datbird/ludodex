#!/usr/bin/env python3
"""A DETACHED entry must not inherit the title's identity — not its link, not its name.

`entry_res.set_detach` says what it promises: an entry marked detached "forfeits the
title-level resolution and stays its own identity ... so it never inherits the official
game's metadata or art". `_game_key()` honoured that, so the entry's own art was safe.
Nothing else did.

The IGDB enrichment loop fans metadata out by `base_to_gids[nk]` — every platform entry
under the title — and skipped only `blocked_gids` (homebrew/hack/unlicensed). A detached
`(nk, platform)` shares its base_key with the title, so it still received:

  * an `INSERT INTO metadata_links(... 'igdb' ...)` row,
  * a `match_confidence` score for a match it was explicitly detached from,
  * every IGDB attribute the title's record carries, and
  * the rename-on-match `UPDATE games SET canonical_title=?`, which renames a
    ROM/archive entry to the game it is NOT.

`provider_links.sync()` was passed the same `blocked_gids` and nothing else, so
ScreenScraper and SteamGridDB links landed on it too.

The Atari 2600 homebrew "Doom" is the case the feature exists for: detaching it and then
handing it id Software's title, cover and genres is worse than never having detached it,
because the entry now LOOKS adjudicated.

Offline. Drives the real build_library over a fixture data dir, which is the only way to
reach the fan-out at all.
"""
import os
import sqlite3
import subprocess
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-ident-detach-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import json                                                    # noqa: E402
import entry_res                                               # noqa: E402
import ownership                                               # noqa: E402
import provider_ids                                            # noqa: E402

LIB = os.path.join(DATA, "game-library.sqlite")
CACHE = os.path.join(DATA, "metadata-cache.sqlite")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def build():
    env = dict(os.environ, LUDODEX_DATA=DATA)
    p = subprocess.run([sys.executable, os.path.join(DIR, "ludodex", "build_library.py")],
                       cwd=DIR, env=env, capture_output=True, text=True, timeout=900)
    if p.returncode != 0:
        sys.exit("build failed (rc=%d):\n%s" % (p.returncode, p.stderr[-3000:]))
    return p


def rows(sql, args=()):
    con = sqlite3.connect(LIB)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def main():
    print("a detached entry inherits nothing from the title")

    # Two entries under one title: the real game owned on PC, and an Atari 2600 ROM that
    # merely shares the name. Both key to `doom`.
    ownership.set_fact(DATA, "doom", "Doom", "physical", "pc", "have")
    ownership.set_fact(DATA, "doom", "Doom", "rom", "atari 2600", "have")

    cc = sqlite3.connect(CACHE)
    cc.execute("CREATE TABLE IF NOT EXISTS igdb_resolution(norm_key TEXT PRIMARY KEY, "
               "igdb_id INTEGER, slug TEXT, matched_by TEXT, resolved_at INTEGER)")
    cc.execute("CREATE TABLE IF NOT EXISTS igdb_meta(igdb_id INTEGER PRIMARY KEY, "
               "payload_json TEXT, fetched_at INTEGER)")
    cc.execute("INSERT OR REPLACE INTO igdb_resolution VALUES('doom',1234,'doom',"
               "'search',0)")
    cc.execute("INSERT OR REPLACE INTO igdb_meta VALUES(1234,?,0)",
               (json.dumps({"id": 1234, "name": "DOOM (1993)", "game_type": 0,
                            "genres": [{"name": "Shooter"}]}),))
    # A ScreenScraper identity for the same title, so provider_links.sync() has something
    # to fan out too.
    provider_ids.ensure_tables(cc)
    provider_ids.record(cc, "screenscraper", "doom", 4321, name="Doom",
                        matched_by="search")
    entry_res.set_detach(cc, "doom", "atari 2600")
    cc.commit()
    cc.close()

    build()

    ent = {plat: (gid, title) for gid, plat, title in rows(
        "SELECT id, platform, canonical_title FROM games WHERE norm_key='doom'")}
    check("both entries exist: %r" % sorted(ent), len(ent) == 2)
    check("the detached entry is present", "atari 2600" in ent)
    det_gid = ent["atari 2600"][0]
    keep_gid = ent["pc"][0]

    print()
    print("1. the detached entry keeps its own identity key")
    gk = dict(rows("SELECT platform, game_key FROM games WHERE norm_key='doom'"))
    check("it is its own title identity: %r" % gk.get("atari 2600"),
          gk.get("atari 2600") == "title:doom")
    check("while the real entry adopts the igdb identity: %r" % gk.get("pc"),
          gk.get("pc") == "igdb:1234")

    print()
    print("2. no provider LINK is attached to it")
    links = rows("SELECT provider, provider_id FROM metadata_links WHERE game_id=?",
                 (det_gid,))
    check("no links at all: %r" % (links,), links == [])
    kept = rows("SELECT provider FROM metadata_links WHERE game_id=?", (keep_gid,))
    check("and the real entry still has them: %r" % (kept,), len(kept) >= 1)

    print()
    print("3. it is not RENAMED to the game it was detached from")
    check("the detached entry keeps its own title: %r" % (ent["atari 2600"][1],),
          ent["atari 2600"][1] != "DOOM (1993)")

    print()
    print("4. it inherits no IGDB attribute and no confidence in the match")
    attrs = {k: v for k, v in rows(
        "SELECT kind, value FROM game_attributes WHERE game_id=?", (det_gid,))}
    check("no igdb genre: %r" % attrs.get("genres"), "genres" not in attrs)
    check("no match_confidence for a match it does not hold",
          "match_confidence" not in attrs)
    keep_attrs = {k for k, _v in rows(
        "SELECT kind, value FROM game_attributes WHERE game_id=?", (keep_gid,))}
    check("while the real entry did get the metadata: %r" % sorted(keep_attrs),
          "genres" in keep_attrs)

    print()
    print("5. a rebuild does not quietly re-attach any of it")
    build()
    check("still no links after a rebuild",
          rows("SELECT provider FROM metadata_links WHERE game_id IN "
               "(SELECT id FROM games WHERE norm_key='doom' AND platform='atari 2600')")
          == [])

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
