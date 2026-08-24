#!/usr/bin/env python3
"""The quiet ones: a silent drop, a key of the wrong type, and two wrong dialects.

  * THE OWNERSHIP MERGE WAS WRAPPED IN `except Exception: pass`. Physical copies and
    per-platform wants are the one class of ownership no importer can re-derive, and any
    error at all dropped every one of them from the build without a word — then did it
    again on the next rebuild.
  * THE REGIONAL-DUP MERGE FOLDED ATTRIBUTES WITH THE WRONG KIND OF KEY. `games_attrs` is
    keyed by the ENTRY — `(norm_key, platform)`, which is what `add()` returns and what
    `add_attrs` is called with — and the fold looked it up by the bare norm_key. So the
    test was never true, every Playnite/LaunchBox attribute record on a merged-away entry
    was dropped, and the string key the fold created was read by nothing.
  * THE ADD-ON PARENT WAS ROW-ORDER DEPENDENT. First-wins over a dict built from a SELECT
    with no ORDER BY: when two norm_keys share one IGDB id, which became `parent_key`
    could change between rebuilds.
  * TWO PER-ENTRY LOOKUPS RAN BEFORE THEIR INDEX EXISTED. A correlated `NOT EXISTS
    (... FROM sources WHERE game_id=?)` and a `DELETE FROM metadata_links WHERE game_id=?`
    once per identified entry, full-scanning until `ix_src_game` was created at the end.
  * `ROM_EXTS` AND `MEDIA_GAMES` WERE RESTATED. romtags owns both; the copy listed "chd"
    twice and had drifted from `romtags.MEDIA_DIRS`.
  * `console_eras.is_handheld` SPOKE THE WRONG DIALECT. It compared `platform.lower()`
    against this table's own labels ('gameboy', 'gba'), so an entry carrying the platmap
    CANONICAL label (`gb`) was not a handheld and build_library's stray-handheld split
    never saw it. The file already documents that exact mismatch, for `era()`.
  * `platmap` HELD A DEAD ALIAS. `_norm()` strips non-alphanumerics before the lookup, so
    "nintendoswitch 2" could never match anything.
  * `process.py` CALLED A GENESIS FILE A VARIANT OF A SNES GAME. `is_variant` was
    title-level across every platform and `base_norm_key` was set to `norm_key`
    unconditionally, so the column PIPELINE.md describes carried nothing.

Offline. Drives the real build_library for the parts that only exist inside one.
"""
import json
import os
import sqlite3
import subprocess
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-ident-smalls-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import config                                                  # noqa: E402
import console_eras                                            # noqa: E402
import ownership                                               # noqa: E402
import platmap                                                 # noqa: E402
import romtags                                                 # noqa: E402

LIB = os.path.join(DATA, "game-library.sqlite")
CACHE = os.path.join(DATA, "metadata-cache.sqlite")
SRC = open(os.path.join(DIR, "ludodex", "build_library.py")).read()

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def build():
    env = dict(os.environ, LUDODEX_DATA=DATA)
    return subprocess.run(
        [sys.executable, os.path.join(DIR, "ludodex", "build_library.py")],
        cwd=DIR, env=env, capture_output=True, text=True, timeout=900)


def main():
    print("the quiet ones")

    print()
    print("1. a merged-away entry's attribute records survive the merge")
    # Two store entries, different titles, ONE IGDB identity, same platform — the shape
    # the regional-dup merge exists for. Different stores, so the many-to-one identity
    # refusal (which is about one store listing a game twice) does not fire.
    pn = os.path.join(DATA, "playnite.json")
    json.dump([
        {"name": "Sonic the Hedgehog 2", "source": "steam", "source_id": "71163",
         "platforms": ["PC"], "genres": ["Platform"], "tags": ["classic"]},
        {"name": "Sonic 2", "source": "gog", "source_id": "gog-1234",
         "platforms": ["PC"], "genres": ["Arcade"], "tags": ["blue"]},
    ], open(pn, "w"))
    config.set_("playnite_import_json", pn)

    cc = sqlite3.connect(CACHE)
    cc.execute("CREATE TABLE IF NOT EXISTS igdb_resolution(norm_key TEXT PRIMARY KEY, "
               "igdb_id INTEGER, slug TEXT, matched_by TEXT, resolved_at INTEGER)")
    cc.execute("CREATE TABLE IF NOT EXISTS igdb_meta(igdb_id INTEGER PRIMARY KEY, "
               "payload_json TEXT, fetched_at INTEGER)")
    for nk in ("sonic the hedgehog 2", "sonic 2"):
        cc.execute("INSERT OR REPLACE INTO igdb_resolution VALUES(?,7000,'s2','search',0)",
                   (nk,))
    cc.execute("INSERT OR REPLACE INTO igdb_meta VALUES(7000,?,0)",
               (json.dumps({"id": 7000, "name": "Sonic the Hedgehog 2",
                            "game_type": 0}),))
    cc.commit()
    cc.close()

    p = build()
    check("the build succeeded (rc=%d)" % p.returncode, p.returncode == 0)
    con = sqlite3.connect(LIB)
    ents = con.execute("SELECT id, norm_key FROM games WHERE norm_key IN "
                       "('sonic the hedgehog 2','sonic 2')").fetchall()
    check("the two entries merged into one: %r" % (ents,), len(ents) == 1)
    gid = ents[0][0]
    got = {r[0] for r in con.execute(
        "SELECT source_id FROM source_attrs WHERE game_id=?", (gid,))}
    check("BOTH attribute records landed on the survivor: %r" % sorted(got),
          got == {"71163", "gog-1234"})
    tags = {r[0] for r in con.execute(
        "SELECT tag FROM game_tags WHERE game_id=?", (gid,))}
    check("including the merged-away entry's tag: %r" % sorted(tags),
          {"classic", "blue"} <= tags)
    con.close()

    print()
    print("2. a failing ownership merge is LOUD, and the build still finishes")
    # A path that exists and is not a database: the store cannot be opened at all.
    os.mkdir(os.path.join(DATA, "ownership.sqlite"))
    p = build()
    check("the build still produced a catalog (rc=%d)" % p.returncode,
          p.returncode == 0)
    check("and it said the ownership facts are missing",
          "OWNERSHIP MERGE FAILED" in p.stderr)
    os.rmdir(os.path.join(DATA, "ownership.sqlite"))

    print()
    print("3. and a WORKING ownership merge is still merged")
    ownership.set_fact(DATA, "panzer dragoon saga", "Panzer Dragoon Saga",
                       "physical", "saturn", "have")
    p = build()
    check("no failure was reported", "OWNERSHIP MERGE FAILED" not in p.stderr)
    con = sqlite3.connect(LIB)
    check("the physical copy is in the catalog",
          con.execute("SELECT COUNT(*) FROM games WHERE norm_key='panzer dragoon saga'"
                      ).fetchone()[0] == 1)
    con.close()

    print()
    print("4. the per-entry lookups have their index before they run")
    i_idx = SRC.index("CREATE INDEX IF NOT EXISTS ix_src_game")
    i_use = SRC.index("NOT EXISTS(")
    check("ix_src_game is created before the correlated NOT EXISTS",
          0 < i_idx < i_use)
    i_del = SRC.index("DELETE FROM metadata_links WHERE game_id=? AND provider='igdb'")
    check("and ix_mlink_game before the per-entry DELETE",
          0 < SRC.index("CREATE INDEX IF NOT EXISTS ix_mlink_game") < i_del)

    print()
    print("5. the add-on parent is not decided by sqlite's row order")
    check("the inversion is sorted", "for _nk, _iid in sorted(_ids.items()):" in SRC)

    print()
    print("6. ROM_EXTS and MEDIA_GAMES have one home")
    check("build_library reads romtags' list", "ROM_EXTS = romtags.ROM_EXTS" in SRC)
    check("and its media-folder set", "MEDIA_GAMES = romtags.MEDIA_DIRS" in SRC)
    check("it does not restate the extensions", '"sfc", "smc", "nes"' not in SRC)
    check("romtags lists chd once, not twice",
          sorted(romtags.ROM_EXTS) == sorted(set(romtags.ROM_EXTS)))

    print()
    print("7. a handheld is a handheld in either dialect")
    for label in ("gameboy", "gb", "gbc", "gba", "gamegear", "ngpc"):
        check("%-9s is a retro handheld" % label,
              console_eras.is_retro_handheld(label))
    for label in ("nds", "3ds", "psvita", "psp"):
        check("%-9s is a handheld" % label, console_eras.is_handheld(label))
    check("and a home console still is not", not console_eras.is_handheld("genesis"))
    check("nor is pc", not console_eras.is_handheld("pc"))

    print()
    print("8. no alias that can never match")
    for canon, aliases in platmap._ALIASES.items():
        for a in aliases:
            check("%s alias %r survives _norm()" % (canon, a), platmap._norm(a) == a)
    check("and Switch 2 still canonicalises",
          platmap.canon("Nintendo Switch 2") == "switch2")

    print()
    print("9. a variant is a variant OF something, on the same hardware")
    import process
    crawl = os.path.join(DATA, "crawl-index.sqlite")
    cx = sqlite3.connect(crawl)
    cx.execute("CREATE TABLE files(id INTEGER PRIMARY KEY, archive TEXT, kind TEXT, "
               "fullpath TEXT UNIQUE, filename TEXT, ext TEXT, size_bytes INTEGER, "
               "mtime REAL, first_seen REAL, last_seen REAL, processed INTEGER DEFAULT 0)")
    root = os.path.join(DATA, "arch")
    files = [
        ("genesis/Aladdin (USA).md", "Aladdin (USA).md", "md"),
        ("genesis/Aladdin (Europe).md", "Aladdin (Europe).md", "md"),
        ("snes/Aladdin (USA).sfc", "Aladdin (USA).sfc", "sfc"),
    ]
    for rel, fn, ext in files:
        cx.execute("INSERT INTO files(archive,kind,fullpath,filename,ext,size_bytes,"
                   "mtime,first_seen,last_seen,processed) "
                   "VALUES('a','rom',?,?,?,0,0,0,0,0)",
                   (os.path.join(root, rel), fn, ext))
    cx.commit(); cx.close()
    config.archive_set("a", root, "rom", 1)
    process.main([])

    cx = sqlite3.connect(crawl)
    rows = {r[0]: (r[1], r[2], r[3]) for r in cx.execute(
        "SELECT f.filename, e.system, e.is_variant, e.base_norm_key "
        "FROM extracted e JOIN files f ON f.id=e.file_id")}
    cx.close()
    check("the first Genesis dump is a new game: %r" % (rows["Aladdin (USA).md"],),
          rows["Aladdin (USA).md"][1] == 0)
    check("the second Genesis dump is its variant",
          rows["Aladdin (Europe).md"][1] == 1)
    check("and names the game it is a variant of",
          rows["Aladdin (Europe).md"][2] == "aladdin")
    check("while the SNES game is NOT a variant of the Genesis one: %r"
          % (rows["Aladdin (USA).sfc"],), rows["Aladdin (USA).sfc"][1] == 0)
    check("and carries no base key, because it is not a variant of anything",
          rows["Aladdin (USA).sfc"][2] is None)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
