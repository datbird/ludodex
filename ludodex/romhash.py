#!/usr/bin/env python3
"""ROM file hashes, and the exact identification they unlock.

WHY THIS EXISTS. The match index holds 829,779 CRC and 769,759 SHA1 keys, harvested from
ScreenScraper and the No-Intro/Redump DATs. A hash hit is an EXACT join: no title
normalisation, no acceptance gate, no provider request, no AI call. Until now nothing in
the ingest computed a hash, so every one of those keys had nothing to match against and
identification fell to name matching for every single file.

THE ZIP SHORTCUT IS THE WHOLE REASON THIS IS AFFORDABLE. A zip stores the CRC32 of each
member in its central directory. Reading it costs one seek to the end of the file — the
ROM data is never decompressed and never read. A 573,000-file library is therefore mostly
free to hash, because emulation collections are overwhelmingly zipped, and the DATs hash
the DECOMPRESSED rom, which is exactly what the zip already recorded.

Loose files have no such record and must be read in full. That is real I/O, so it is
bounded by size and off by default.

WHAT CANNOT BE HASHED USEFULLY. CHD and RVZ are recompressions: converting a disc image
changes every byte, so its CRC and SHA1 no longer match anything a DAT recorded. That is
not a gap to work around here — it is why disc SERIALS exist in the index (60,104 of
them), and serials are read from the disc content by a different path.
"""
import os
import sqlite3
import sys
import zipfile

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)

# Recompressed disc formats. Their bytes are not the bytes any DAT hashed, so a hash of
# one identifies nothing and computing it is pure waste.
RECOMPRESSED = {".chd", ".rvz", ".wux", ".wud", ".nkit", ".cso", ".zso", ".rpx"}

# Archives whose central directory records the member CRC32, so the hash is free.
ZIP_LIKE = {".zip"}

# Loose files are read end to end. 64 MB covers cartridge-era ROMs, which is where the
# DAT coverage is; a 4 GB disc image read for a hash the DATs may not even hold is a bad
# trade, so the cap is a default rather than a limit of the code.
DEFAULT_LOOSE_MAX = 64 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS rom_hashes(
  relpath TEXT PRIMARY KEY,
  size_bytes INTEGER,
  crc TEXT,
  sha1 TEXT,
  source TEXT,          -- 'zip' (free, from the central directory) or 'read'
  hashed_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_rh_crc ON rom_hashes(crc);
CREATE INDEX IF NOT EXISTS ix_rh_sha1 ON rom_hashes(sha1);
"""


def ensure_schema(con):
    """Hashes live in their OWN table, not in `roms`.

    build_romdb drops and recreates `roms` on every scan. A hash column there would be
    deleted by each rebuild and re-earned by re-reading the files — which for loose files
    is the expensive half of this module."""
    con.executescript(SCHEMA)
    con.commit()


def zip_crcs(path):
    """-> [(member_name, crc32_hex, uncompressed_size)] read from the central directory.

    Never decompresses. Returns [] for anything unreadable: a corrupt archive is a fact
    about one file, not a reason to abort a 573,000-file scan."""
    out = []
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                out.append((info.filename, "%08x" % (info.CRC & 0xFFFFFFFF),
                            info.file_size))
    except Exception:                            # noqa: BLE001
        return []
    return out


def hash_loose(path, max_bytes=DEFAULT_LOOSE_MAX):
    """-> (crc32_hex, sha1_hex) for a file read in full, or (None, None).

    One pass feeds both digests. Reading the file twice to get two hashes would double
    the only genuinely expensive part of this module."""
    import hashlib
    import zlib
    try:
        if max_bytes and os.path.getsize(path) > max_bytes:
            return None, None
    except OSError:
        return None, None
    crc = 0
    sha = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                crc = zlib.crc32(chunk, crc)
                sha.update(chunk)
    except OSError:
        return None, None
    return "%08x" % (crc & 0xFFFFFFFF), sha.hexdigest()


def hash_one(fullpath, loose=False, loose_max=DEFAULT_LOOSE_MAX):
    """-> (crc, sha1, source) for one file. crc may be None; that is a normal answer.

    A single-member zip is the common emulation case and its member CRC IS the rom's.
    A multi-member zip has no single answer, so it reports none rather than guessing
    which member the game is."""
    ext = os.path.splitext(fullpath)[1].lower()
    if ext in RECOMPRESSED:
        return None, None, "recompressed"
    if ext in ZIP_LIKE:
        members = zip_crcs(fullpath)
        if len(members) == 1:
            return members[0][1], None, "zip"
        return None, None, "zip_multi" if members else "zip_unreadable"
    if loose:
        crc, sha1 = hash_loose(fullpath, loose_max)
        if crc:
            return crc, sha1, "read"
        return None, None, "too_big"
    return None, None, "skipped"


def scan(con, limit=None, loose=False, loose_max=DEFAULT_LOOSE_MAX, progress=True):
    """Hash every rom not hashed yet. -> counts by source.

    Resumable by construction: a row already in rom_hashes is skipped, so a scan that is
    interrupted at 400,000 files resumes rather than restarting."""
    import time
    ensure_schema(con)
    rows = con.execute(
        "SELECT r.relpath, r.fullpath, r.size_bytes FROM roms r "
        "LEFT JOIN rom_hashes h ON h.relpath = r.relpath "
        "WHERE h.relpath IS NULL" + (" LIMIT %d" % int(limit) if limit else "")
    ).fetchall()

    now = int(time.time())
    counts = {}
    batch = []
    for i, r in enumerate(rows, 1):
        crc, sha1, source = hash_one(r["fullpath"], loose=loose, loose_max=loose_max)
        counts[source] = counts.get(source, 0) + 1
        batch.append((r["relpath"], r["size_bytes"], crc, sha1, source, now))
        if len(batch) >= 2000:
            con.executemany("INSERT OR REPLACE INTO rom_hashes"
                            "(relpath,size_bytes,crc,sha1,source,hashed_at) "
                            "VALUES(?,?,?,?,?,?)", batch)
            con.commit()
            batch = []
            if progress:
                print("romhash: %d/%d" % (i, len(rows)), file=sys.stderr)
    if batch:
        con.executemany("INSERT OR REPLACE INTO rom_hashes"
                        "(relpath,size_bytes,crc,sha1,source,hashed_at) "
                        "VALUES(?,?,?,?,?,?)", batch)
        con.commit()
    counts["examined"] = len(rows)
    return counts


def identify(mi_con, crc=None, sha1=None):
    """A hash -> every provider handle for that game, or {}.

    SHA1 IS ASKED FIRST because it is the stronger claim. CRC32 is 32 bits over a corpus
    of ~830,000 hashes, so collisions are possible in principle; sha1 is not in doubt.
    Both come from the same index and cost the same lookup, so preferring the stronger
    one is free.

    An empty result means "this index does not know that hash" — a real answer, and the
    pipeline's signal to fall back to the name path."""
    import matchindex
    for ns, val in (("sha1", sha1), ("crc", crc)):
        if not val:
            continue
        hit = matchindex.resolve(mi_con, ns, str(val).lower())
        if hit:
            return hit
    return {}


def coverage(con):
    """How much of the library can be identified by hash, without asking anyone."""
    ensure_schema(con)
    q = lambda s: con.execute(s).fetchone()[0]      # noqa: E731
    return {
        "roms": q("SELECT COUNT(*) FROM roms"),
        "hashed": q("SELECT COUNT(*) FROM rom_hashes WHERE crc IS NOT NULL"),
        "by_source": dict(con.execute(
            "SELECT source, COUNT(*) FROM rom_hashes GROUP BY source").fetchall()),
    }


def main(argv):
    import json
    import config                                  # noqa: F401  (path side effects)
    path = None
    for i, a in enumerate(argv):
        if a == "--db" and i + 1 < len(argv):
            path = argv[i + 1]
    if not path:
        print("usage: romhash.py --db <roms-index.sqlite> [--scan] [--loose] "
              "[--limit N]", file=sys.stderr)
        return 2
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    if "--scan" in argv:
        limit = None
        if "--limit" in argv:
            limit = int(argv[argv.index("--limit") + 1])
        out = scan(con, limit=limit, loose=("--loose" in argv))
        print(json.dumps(out, indent=2))
    else:
        print(json.dumps(coverage(con), indent=2))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


# The index namespaces that map onto a ludodex provider identity cache. `ss` is the
# ScreenScraper namespace inside the index; provider_ids calls the same provider
# `screenscraper`. Names that differ for the same thing is exactly how a namespace query
# returns 0 and gets believed, so the mapping is stated once, here.
NS_TO_PROVIDER = {
    "igdb": "igdb",
    "ss": "screenscraper",
    "mobygames": "mobygames",
    "thegamesdb": "thegamesdb",
}


def enrich_from_hashes(rom_con, cat_con, limit=None, progress=True):
    """Identify roms by HASH and record every provider id that identity carries.

    THIS IS THE POINT OF THE WHOLE INDEX. A CRC hit is an exact join: no title
    normalisation, no acceptance gate, no provider request, no AI call, no rate limit. A
    game identified this way costs a single indexed lookup, and it arrives carrying every
    other provider's id for the same game at once.

    matched_by='hash' is deliberate and load-bearing. provider_ids.record() refuses a
    SEARCHED id that another game already holds, because a search is where wrong binds
    come from. A hash is not a search — the dump database published that pairing — so it
    is recorded as the exact evidence it is, alongside 'manual' and 'steam_appid'.

    A miss is a real answer and writes NOTHING. The index not knowing a hash means the
    name path should run, not that this game has no match — writing a miss here would
    suppress the search that would have found it.
    """
    import time
    import matchindex
    import provider_ids

    mi = matchindex.connect()
    rows = rom_con.execute(
        "SELECT r.relpath, r.game, r.system, h.crc, h.sha1 "
        "FROM rom_hashes h JOIN roms r ON r.relpath = h.relpath "
        "WHERE h.crc IS NOT NULL" + (" LIMIT %d" % int(limit) if limit else "")
    ).fetchall()

    from titlenorm import norm
    hits = recorded = 0
    seen_keys = set()
    for i, r in enumerate(rows, 1):
        got = identify(mi, crc=r["crc"], sha1=r["sha1"])
        if not got:
            continue
        hits += 1
        nk = norm(r["game"] or "")
        if not nk or nk in seen_keys:
            continue
        seen_keys.add(nk)
        for ns, provider in NS_TO_PROVIDER.items():
            vals = got.get(ns) or []
            if not vals:
                continue
            # One id per provider. A handle resolving to several ids for one provider
            # means the index merged something it should not have; take none rather
            # than pick arbitrarily.
            if len(vals) > 1:
                continue
            try:
                provider_ids.record(cat_con, provider, nk, vals[0],
                                    name=got.get("_name"), matched_by="hash",
                                    year=got.get("_year"), system=r["system"])
                recorded += 1
            except Exception:                    # noqa: BLE001
                continue
        if progress and i % 20000 == 0:
            print("romhash: %d/%d examined, %d hash hits" % (i, len(rows), hits),
                  file=sys.stderr)
    mi.close()
    return {"examined": len(rows), "hash_hits": hits, "ids_recorded": recorded,
            "distinct_games": len(seen_keys), "at": int(time.time())}
