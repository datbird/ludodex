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
  * A DECISION NOT TO LOOK IS NOT AN ANSWER. A loose file passed over because loose
    hashing was off must be re-examined when it is turned on. Storing that as a row and
    skipping it forever is the same defect, one layer down: turning the setting on
    silently does nothing.
  * THE PROVIDER MAP HAS ONE HOME. This module inverts provider_ids.INDEX_NS rather than
    restating it, so a provider ruled unusable there cannot still be written here.
  * THE CLI MUST RUN ON A BARE DEVICE. pull_roms copies this file to a remote device and
    runs it with nothing else of ludodex present. A hard import of config would kill
    every remote scan, and silently, because the hash is allowed to fail.
  * HASHING NEVER FAILS A SYNC. It saves requests; it is not a step of the pull.
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
    print("9. turning loose hashing ON re-examines what was passed over")
    # The first scan wrote source='skipped' for the loose file. Rows are rows, so a
    # later --loose scan found it present and examined nothing at all.
    row = con.execute("SELECT source FROM rom_hashes WHERE relpath=?",
                      (os.path.basename(loose),)).fetchone()
    check("the loose file was recorded as 'skipped': %r" % (row and row[0]),
          row and row[0] == "skipped")
    lo = romhash.scan(con, loose=True, progress=False)
    check("a loose scan re-examines it: %d" % lo["examined"], lo["examined"] == 1)
    check("and it now has a real read hash", lo.get("read") == 1)
    row = con.execute("SELECT crc, sha1, source FROM rom_hashes WHERE relpath=?",
                      (os.path.basename(loose),)).fetchone()
    check("crc and sha1 both stored from one pass",
          row["crc"] and row["sha1"] and row["source"] == "read")
    check("a settled answer is never recomputed",
          romhash.scan(con, loose=True, progress=False)["examined"] == 0)

    print()
    print("10. the provider map is INVERTED from provider_ids, not restated")
    m = romhash._ns_to_provider()
    check("ss maps to screenscraper", m.get("ss") == "screenscraper")
    check("mobygames is absent, exactly as it is in provider_ids",
          "mobygames" not in m and "mobygames" not in provider_ids.INDEX_NS)
    check("every namespace comes from INDEX_NS",
          set(m) == set(provider_ids.INDEX_NS.values()))

    print()
    print("11. a hash hit records every provider id, as EXACT evidence")
    ix = sqlite3.connect(matchindex.DB)
    ix.executescript("""
    CREATE TABLE IF NOT EXISTS identity(id INTEGER PRIMARY KEY, name TEXT,
      norm_key TEXT, year INTEGER, first_release_date INTEGER, built_at INTEGER);
    CREATE TABLE IF NOT EXISTS identity_key(ns TEXT, val TEXT, identity_id INTEGER,
      kind TEXT, PRIMARY KEY(ns, val, identity_id));
    CREATE TABLE IF NOT EXISTS identity_state(k TEXT PRIMARY KEY, v TEXT);
    """)
    ix.execute("INSERT OR REPLACE INTO identity VALUES(77,'Game','game',1990,NULL,0)")
    for ns, val in (("crc", want_crc), ("igdb", "77"), ("ss", "5150"),
                    ("thegamesdb", "900"), ("mobygames", "game-slug")):
        ix.execute("INSERT OR IGNORE INTO identity_key VALUES(?,?,77,'exact')",
                   (ns, val))
    ix.commit(); ix.close()

    cat = sqlite3.connect(os.path.join(os.environ["LUDODEX_DATA"],
                                       "metadata-cache.sqlite"))
    cat.row_factory = sqlite3.Row
    provider_ids.ensure_tables(cat)
    rep = romhash.enrich_from_hashes(con, cat, progress=False)
    check("the crc was identified: %d hit(s)" % rep["hash_hits"], rep["hash_hits"] >= 1)
    got = provider_ids.cached(cat, "igdb", "game")
    check("igdb id recorded from the hash: %r" % (got and got[0]), got and got[0] == 77)
    check("recorded as matched_by='hash': %r" % (got and got[1]),
          got and got[1] == "hash")
    check("screenscraper id recorded too",
          (provider_ids.cached(cat, "screenscraper", "game") or [None])[0] == 5150)

    print()
    print("12. a slug is never written as a numeric provider's id")
    # 'mobygames' in the index mixes URL slugs with numeric catalogue ids. Nothing may
    # turn one into a recorded identity for a provider that ids by number.
    for prov in ("igdb", "screenscraper", "thegamesdb"):
        r = provider_ids.cached(cat, prov, "game")
        check("%s id is numeric or absent: %r" % (prov, r and r[0]),
              r is None or r[0] is None or isinstance(r[0], int))

    print()
    print("13. hashing never fails a sync, and says why it did nothing")
    # Remote devices build the index on the device, so every fullpath names a path this
    # host cannot open. Hashing those would write a negative row for every rom.
    far = os.path.join(os.environ["LUDODEX_DATA"], "far.sqlite")
    fc = sqlite3.connect(far)
    fc.executescript("CREATE TABLE roms(id INTEGER PRIMARY KEY, system TEXT, game TEXT,"
                     " relpath TEXT, fullpath TEXT, size_bytes INTEGER)")
    fc.execute("INSERT INTO roms VALUES(1,'nes','X','x.zip','/nowhere/on/this/host/x.zip',1)")
    fc.commit()
    check("files on another machine are seen as unreachable",
          not romhash.files_reachable(fc))
    fc.close()
    out = romhash.hash_and_enrich(far, progress=False)
    check("so the scan does not run: %r" % out.get("skipped"), bool(out.get("skipped")))
    check("and nothing was written", sqlite3.connect(far).execute(
        "SELECT COUNT(*) FROM rom_hashes").fetchone()[0] == 0)
    gone = romhash.hash_and_enrich(
        os.path.join(os.environ["LUDODEX_DATA"], "no-such-index.sqlite"), progress=False)
    check("a missing index reports, it does not raise", "skipped" in gone)

    print()
    print("14. the CLI runs on a device that has nothing else of ludodex")
    rsrc = open(os.path.join(here, "romhash.py")).read()
    i_try = rsrc.find("try:\n        import config")
    check("config is imported defensively, not required", i_try > 0)
    dsrc = open(os.path.join(here, "devices.py")).read()
    check("pull_roms ships romhash.py to the device", '"romhash.py")' in dsrc)
    check("and runs the scan there, after build_romdb",
          dsrc.find("build_romdb.py /tmp/ldx_romscan.tsv")
          < dsrc.find("python3 /tmp/romhash.py --db"))
    # Sharing the index build's 300s SSH budget would let a large library time the call
    # out and raise "remote scan failed" for a pull whose index was already built.
    check("the remote hash gets its OWN ssh call and timeout",
          "ROMHASH_REMOTE_TIMEOUT" in dsrc
          and "timeout=ROMHASH_REMOTE_TIMEOUT" in dsrc)
    check("and its non-zero exit is logged, never raised",
          "romhash on device" in dsrc)

    cat.close()
    con.close()

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
