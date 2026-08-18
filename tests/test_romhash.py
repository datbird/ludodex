#!/usr/bin/env python3
"""Hashing a ROM library, and the exact identification it buys.

  * THE ZIP CRC MUST NOT COST A READ. A zip records its member's CRC32 in the central
    directory. If this ever regresses to decompressing, a 573,000-file scan stops being
    affordable and quietly becomes an overnight job.
  * A MULTI-MEMBER ZIP HAS NO ANSWER. Picking one member's CRC would be a guess, and a
    guessed hash resolves to a confidently wrong game.
  * CHD AND RVZ MUST BE REFUSED, NOT ATTEMPTED. Their bytes are a recompression, so any
    hash of them matches nothing any DAT recorded. Computing one is waste that looks
    like diligence.
  * A MISS WRITES NOTHING. The index not knowing a hash means the name path should run.
    Recording a miss would suppress the search that would have found the game — the
    recurring defect in this codebase.
"""
import os
import sys
import zipfile

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "ludodex")
    sys.path.insert(0, here)
    import test_support
    test_support.isolate("ludodex-romhash-")
    import sqlite3
    import romhash

    tmp = os.path.join(os.environ["LUDODEX_DATA"], "roms")
    os.makedirs(tmp, exist_ok=True)

    payload = b"NES\x1a" + b"rom bytes here" * 64
    import zlib
    want_crc = "%08x" % (zlib.crc32(payload) & 0xFFFFFFFF)

    single = os.path.join(tmp, "Game (USA).zip")
    with zipfile.ZipFile(single, "w") as z:
        z.writestr("Game (USA).nes", payload)

    multi = os.path.join(tmp, "Compilation.zip")
    with zipfile.ZipFile(multi, "w") as z:
        z.writestr("a.nes", payload)
        z.writestr("b.nes", payload + b"x")

    loose = os.path.join(tmp, "Loose.nes")
    with open(loose, "wb") as fh:
        fh.write(payload)

    chd = os.path.join(tmp, "Disc.chd")
    with open(chd, "wb") as fh:
        fh.write(payload)

    print()
    print("1. a zip's CRC comes from the central directory")
    crc, sha1, source = romhash.hash_one(single)
    check("crc matches the member's real crc32 (%s)" % crc, crc == want_crc)
    check("reported source is 'zip', not a read", source == "zip")
    check("no sha1 is invented for a zip member", sha1 is None)

    print()
    print("2. a multi-member zip refuses rather than guessing")
    crc, _s, source = romhash.hash_one(multi)
    check("no crc chosen from several members", crc is None)
    check("source says why: %r" % source, source == "zip_multi")

    print()
    print("3. recompressed disc formats are refused, not hashed")
    crc, _s, source = romhash.hash_one(chd)
    check("chd yields no crc", crc is None)
    check("source records the reason", source == "recompressed")

    print()
    print("4. a loose file is only read when asked")
    crc, sha1, source = romhash.hash_one(loose, loose=False)
    check("loose file skipped by default", crc is None and source == "skipped")
    crc, sha1, source = romhash.hash_one(loose, loose=True)
    check("with loose=True the crc matches (%s)" % crc, crc == want_crc)
    check("and a sha1 is produced", bool(sha1) and len(sha1) == 40)
    check("source records that it cost a read", source == "read")

    print()
    print("5. the size cap is honoured")
    crc, _s, source = romhash.hash_one(loose, loose=True, loose_max=8)
    check("a file over the cap is not read", crc is None and source == "too_big")

    print()
    print("6. scan() is resumable and skips what it already did")
    db = os.path.join(os.environ["LUDODEX_DATA"], "roms-index.sqlite")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript("""CREATE TABLE roms(id INTEGER PRIMARY KEY, system TEXT,
        game TEXT, relpath TEXT, fullpath TEXT, size_bytes INTEGER)""")
    for i, p in enumerate((single, multi, loose, chd), 1):
        con.execute("INSERT INTO roms VALUES(?,?,?,?,?,?)",
                    (i, "nes", "Game", os.path.basename(p), p, os.path.getsize(p)))
    con.commit()

    out = romhash.scan(con, progress=False)
    check("all four files examined", out["examined"] == 4)
    check("exactly one produced a zip crc", out.get("zip") == 1)

    again = romhash.scan(con, progress=False)
    check("a second scan re-examines nothing", again["examined"] == 0)

    cov = romhash.coverage(con)
    check("coverage counts only rows with a crc: %d" % cov["hashed"],
          cov["hashed"] == 1)

    print()
    print("7. a hash the index does not know returns {} — a real answer")
    import matchindex
    mi = matchindex.connect()
    check("unknown crc resolves to nothing",
          romhash.identify(mi, crc="deadbeef") == {})
    mi.close()

    print()
    print("8. 'hash' is EXACT evidence, exempt from the search-collision guard")
    import provider_ids
    src = open(os.path.join(here, "provider_ids.py")).read()
    check("record() treats hash alongside manual and steam_appid",
          '"manual", "steam_appid", "hash"' in src)

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
