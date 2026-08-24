#!/usr/bin/env python3
"""A ROM hash is evidence about ONE FILE. It must be filed the way the catalog files it.

`enrich_from_hashes` wrote the identity a CRC unlocked under `norm(r["game"])` — no
platform, no merge alias — and let the FIRST row for that key win, out of a SELECT with no
ORDER BY. Four things followed:

  * THE KEY WAS NOT THE CATALOG'S KEY. build_library keys an entry with
    `_mkey(title, platform)`, which strips an in-title hardware tag and applies the user's
    durable merges: `norm("Doom 32X", "sega 32x")` is `doom`. romhash wrote `doom 32x`, a
    norm_key no catalog row carries, so every hash-identified ROM with a hardware tag
    bought an identity nothing could read.
  * WHICH FILE WON CHANGED RUN TO RUN. No ORDER BY, and `seen_keys` keeps the first row
    for a key — so on a title present on two consoles, which console's record became the
    title's identity depended on sqlite's row order.
  * ONE FILE CLAIMED THE WHOLE TITLE BUCKET. A Game Boy "Uno" CRC recorded igdb/ss ids
    under `uno`, which the Steam "UNO" entry shares. `matched_by='hash'` is exempt from
    the collision guard and excluded from `rescore()`, so nothing downstream ever
    questions it. Where the hashed files DISAGREE about the game, that disagreement is the
    evidence, and the honest answer is to record nothing.
  * THE WRITES COULD ALL FAIL IN SILENCE. `ensure_tables` was never called and each
    `record()` sat inside `except Exception: continue`, so a cache missing a column failed
    every single write while the report still said `hash_hits: N`.

Plus two hygiene items in the same file: `record()` committed on EVERY call, so a hash
pass over hundreds of thousands of files did one fsync per (game, provider) while
`scan()` right above it batches 2,000 rows per commit; and the `if __name__ == "__main__"`
guard sat ABOVE three later function definitions.

Offline. No network — the match index is a local fixture.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-ident-hash-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import matchindex                                              # noqa: E402
import provider_ids                                            # noqa: E402
import romhash                                                 # noqa: E402
import titlenorm                                               # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def build_index(entries):
    """entries: [(identity_id, name, year, {ns: val}, [crc, ...])]"""
    if os.path.exists(matchindex.DB):
        os.unlink(matchindex.DB)
    ix = sqlite3.connect(matchindex.DB)
    ix.executescript("""
    CREATE TABLE identity(id INTEGER PRIMARY KEY, name TEXT, norm_key TEXT, year INTEGER,
      first_release_date INTEGER, built_at INTEGER);
    CREATE TABLE identity_key(ns TEXT, val TEXT, identity_id INTEGER, kind TEXT,
      platform TEXT, PRIMARY KEY(ns, val, identity_id));
    CREATE TABLE identity_state(k TEXT PRIMARY KEY, v TEXT);
    """)
    for iid, name, year, handles, crcs in entries:
        ix.execute("INSERT INTO identity VALUES(?,?,?,?,NULL,0)",
                   (iid, name, titlenorm.norm(name), year))
        for ns, val in handles.items():
            ix.execute("INSERT INTO identity_key(ns,val,identity_id,kind) "
                       "VALUES(?,?,?,'exact')", (ns, str(val), iid))
        for crc in crcs:
            ix.execute("INSERT INTO identity_key(ns,val,identity_id,kind) "
                       "VALUES('crc',?,?,'exact')", (crc, iid))
    ix.commit()
    ix.close()


def rom_db(rows, path=None):
    """rows: [(relpath, system, game, ext, crc)]"""
    path = path or os.path.join(DATA, "roms-index.sqlite")
    if os.path.exists(path):
        os.unlink(path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE roms(id INTEGER PRIMARY KEY, system TEXT, game TEXT, ext TEXT,
      relpath TEXT, fullpath TEXT, size_bytes INTEGER);
    """)
    romhash.ensure_schema(con)
    for rel, system, game, ext, crc in rows:
        con.execute("INSERT INTO roms(system,game,ext,relpath,fullpath,size_bytes) "
                    "VALUES(?,?,?,?,?,0)", (system, game, ext, rel, "/nope/" + rel))
        con.execute("INSERT INTO rom_hashes(relpath,size_bytes,crc,sha1,source,hashed_at)"
                    " VALUES(?,0,?,NULL,'zip',0)", (rel, crc))
    con.commit()
    return con


def fresh_cache(name="metadata-cache.sqlite", make_tables=False):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        os.unlink(p)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    if make_tables:
        provider_ids.ensure_tables(con)
    return con


def main():
    print("a hash identity is keyed the way the catalog keys")

    print()
    print("1. an in-title hardware tag keys the same as the catalog's entry")
    build_index([(999, "Doom", 1993, {"igdb": 999, "ss": 5555}, ["aaaa1111"])])
    # The file is misfiled in the Genesis folder, which is the case platmap.TITLE_PLATFORM
    # exists for: the filename names the hardware and wins over the folder.
    rc = rom_db([("sega genesis/Doom 32X (U).zip", "sega genesis", "Doom 32X", "zip",
                  "aaaa1111")])
    cc = fresh_cache(make_tables=True)
    rep = romhash.enrich_from_hashes(rc, cc, progress=False)
    check("the hash hit: %r" % rep.get("hash_hits"), rep.get("hash_hits") == 1)
    got = dict(cc.execute("SELECT norm_key, igdb_id FROM igdb_resolution"))
    check("recorded under the catalog's key 'doom': %r" % got, "doom" in got)
    check("not under the hardware-tagged key", "doom 32x" not in got)
    check("and it carries the igdb id", got.get("doom") == 999)
    sysrow = cc.execute("SELECT system FROM igdb_resolution WHERE norm_key='doom'"
                        ).fetchone()
    check("with the ROM's own platform recorded: %r" % (sysrow[0],),
          sysrow[0] == "sega 32x")
    rc.close(); cc.close()

    print()
    print("2. the same key derived by ONE function, shared with build_library")
    check("titlenorm exposes the catalog key", hasattr(titlenorm, "catalog_key"))
    check("and it agrees with what romhash wrote",
          titlenorm.catalog_key("Doom 32X", "sega 32x") == "doom")
    bsrc = open(os.path.join(DIR, "ludodex", "build_library.py")).read()
    check("build_library's _mkey delegates to it rather than restating it",
          "titlenorm.catalog_key(" in bsrc)

    print()
    print("3. hashed files that DISAGREE about the game record nothing")
    # A ~1994 Game Boy "Uno" and a NES "Uno" are different games that share a title. Both
    # hash cleanly, both key to `uno`, and the identity cache holds one row per norm_key —
    # so whichever file was visited first used to become the title's identity for every
    # platform, including a Steam "UNO" that was never hashed at all.
    build_index([
        (111, "Uno", 1994, {"igdb": 111, "ss": 111111}, ["cafe0001"]),
        (222, "Uno", 1989, {"igdb": 222, "ss": 222222}, ["cafe0002"]),
    ])
    rc = rom_db([("gameboy/Uno.gb", "gameboy", "Uno", "gb", "cafe0001"),
                 ("nes/Uno.nes", "nes", "Uno", "nes", "cafe0002")])
    cc = fresh_cache(make_tables=True)
    rep = romhash.enrich_from_hashes(rc, cc, progress=False)
    check("both files hit: %r" % rep.get("hash_hits"), rep.get("hash_hits") == 2)
    check("no identity was recorded for the contested title",
          cc.execute("SELECT COUNT(*) FROM igdb_resolution").fetchone()[0] == 0)
    check("and the disagreement is REPORTED, not swallowed: %r" % rep.get("conflicts"),
          rep.get("conflicts", 0) >= 1)
    rc.close(); cc.close()

    print()
    print("4. agreeing files still record, whatever order they arrive in")
    build_index([(777, "Contra", 1988, {"igdb": 777, "ss": 777777},
                  ["beef0001", "beef0002"])])
    forward = [("nes/Contra.nes", "nes", "Contra", "nes", "beef0001"),
               ("arcade/Contra.zip", "arcade", "Contra", "zip", "beef0002")]
    seen = []
    for rows in (forward, list(reversed(forward))):
        rc = rom_db(rows)
        cc = fresh_cache(make_tables=True)
        romhash.enrich_from_hashes(rc, cc, progress=False)
        seen.append(dict(cc.execute("SELECT norm_key, igdb_id FROM igdb_resolution")))
        rc.close(); cc.close()
    check("the identity was recorded: %r" % seen[0], seen[0].get("contra") == 777)
    check("and row order does not change the answer", seen[0] == seen[1])
    src = open(os.path.join(DIR, "ludodex", "romhash.py")).read()
    check("the driving query is ordered", "ORDER BY" in src)

    print()
    print("5. the writes cannot fail in silence")
    build_index([(999, "Doom", 1993, {"igdb": 999}, ["aaaa1111"])])
    rc = rom_db([("dos/Doom.zip", "dos", "Doom", "zip", "aaaa1111")])
    # A cache with none of the identity tables — the shape that used to fail every write
    # while still reporting hash_hits.
    cc = fresh_cache("bare-cache.sqlite", make_tables=False)
    rep = romhash.enrich_from_hashes(rc, cc, progress=False)
    check("the tables were created and the id was written: %r" % rep.get("ids_recorded"),
          rep.get("ids_recorded", 0) >= 1)
    rc.close(); cc.close()

    # A write that genuinely fails must show up in the report.
    rc = rom_db([("dos/Doom.zip", "dos", "Doom", "zip", "aaaa1111")])
    cc = fresh_cache(make_tables=True)
    real = provider_ids.record

    def boom(*a, **k):
        raise sqlite3.OperationalError("no such column: system")

    provider_ids.record = boom
    try:
        rep = romhash.enrich_from_hashes(rc, cc, progress=False)
    finally:
        provider_ids.record = real
    check("a failing write is counted: %r" % rep.get("write_errors"),
          rep.get("write_errors", 0) >= 1)
    check("and the reason is reported: %r" % rep.get("write_error"),
          "no such column" in str(rep.get("write_error", "")))
    rc.close(); cc.close()

    print()
    print("6. a hash pass does not fsync once per (game, provider)")
    import inspect
    check("record() can defer its commit",
          "commit" in inspect.signature(provider_ids.record).parameters)
    check("and the hash pass defers it", "commit=False" in src)

    print()
    print("7. the module entry point sits at the END of the module")
    check("nothing is defined after the __main__ guard",
          src.rindex('if __name__ == "__main__":') > src.rindex("\ndef "))

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
