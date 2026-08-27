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

Loose files have no such record and must be read in full. THE SIZE CAP IS THE GUARD, NOT
A SWITCH. Measured on a real 42,536-file loose set totalling 1,399.6 GB, the 736 files
above the cap held 1,341.6 GB of it — 96% of the bytes, and almost all disc images. What
is left under the cap is 41,800 files and 58.0 GB, read once, in exchange for identifying
tens of thousands of games exactly. So loose hashing is ON, and the cap does the work the
switch used to be asked to do.

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
# Same derivation every other module uses: DIR is this package, DATA is the repo root
# above it, where the databases live. Deriving DATA from DIR instead would silently
# relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
# The one list of what counts as a ROM. romtags owns it; restating it here is how a
# format gets added in one place and silently ignored in the other.
import romtags                                   # noqa: E402

# Recompressed disc formats. Their bytes are not the bytes any DAT hashed, so a hash of
# one identifies nothing and computing it is pure waste.
RECOMPRESSED = {".chd", ".rvz", ".wux", ".wud", ".nkit", ".cso", ".zso", ".rpx"}

# Archives whose central directory records the member CRC32, so the hash is free.
ZIP_LIKE = {".zip"}

# Archives this cannot read a member CRC out of. THEY MUST BE REFUSED, NOT READ. Reading
# a .7z end to end hashes the COMPRESSED CONTAINER, and no DAT ever recorded that number,
# so the result is a hash that identifies nothing while looking exactly like a real one.
# Measured on this library: 5,060 .7z and 3,678 .rar files.
UNREADABLE_ARCHIVES = {".7z", ".rar", ".rar5", ".tar", ".gz", ".bz2", ".xz"}

# Loose files are read end to end. 64 MB covers cartridge-era ROMs, which is where the
# DAT coverage is; a 4 GB disc image read for a hash the DATs may not even hold is a bad
# trade, so the cap is a default rather than a limit of the code.
DEFAULT_LOOSE_MAX = 64 * 1024 * 1024

# Read loose files unless told otherwise. This was off, and defending that took an
# argument nobody should have to hear: a bare .smc has no other way to be identified, and
# refusing to read it means guessing at its filename instead. The fear was I/O, and the
# cap above already answers it — 96% of the bytes on a real library sit in 736 disc
# images the cap refuses anyway.
DEFAULT_LOOSE = True

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


def hash_one(fullpath, loose=DEFAULT_LOOSE, loose_max=DEFAULT_LOOSE_MAX):
    """-> (crc, sha1, source) for one file. crc may be None; that is a normal answer.

    A single-member zip is the common emulation case and its member CRC IS the rom's.
    A multi-member zip has no single answer, so it reports none rather than guessing
    which member the game is."""
    ext = os.path.splitext(fullpath)[1].lower()
    # A FILE THAT IS NOT THERE IS NOT AN UNREADABLE ARCHIVE. Both used to report
    # `zip_unreadable`, and the two mean opposite things: one says this archive is
    # damaged, the other says the index is describing a file this machine cannot see —
    # a wrong root, a missing mount, an index built elsewhere. 9,078 files reported
    # themselves as corrupt zips when every one of them opened perfectly at its real
    # path. A diagnosis that names the wrong fault is worse than none.
    if not os.path.exists(fullpath):
        return None, None, "missing"
    if ext in RECOMPRESSED:
        return None, None, "recompressed"
    if ext in UNREADABLE_ARCHIVES:
        return None, None, "archive_unsupported"
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


# Sources that record a DECISION NOT TO LOOK rather than an answer about the file.
# `skipped` is a loose file passed over because loose hashing was off; `too_big` is one
# over the size cap; `zip_unreadable` is an archive that would not open; `missing` is a
# file this machine could not see at all. None of them says the file has no usable hash —
# they say we did not compute one. `missing` especially: a remounted share or a corrected
# root makes the file readable again, and a permanent row would keep it skipped forever.
UNANSWERED = ("skipped", "too_big", "zip_unreadable", "missing")


def scan(con, limit=None, loose=DEFAULT_LOOSE, loose_max=DEFAULT_LOOSE_MAX,
         progress=True):
    """Hash every rom not hashed yet. -> counts by source.

    Resumable by construction: a row already answered is skipped, so a scan interrupted
    at 400,000 files resumes rather than restarting.

    A DECISION NOT TO LOOK IS NOT AN ANSWER, and treating it as one is this codebase's
    signature defect wearing a new hat. The first scan runs with loose hashing off and
    writes `skipped` for every loose file. Those rows are rows, so a later scan with
    --loose found them present and examined NOTHING — turning the setting on could never
    take effect, in silence, forever. Measured on a four-file fixture: the second run
    reported `examined: 0`.

    So rows carrying an UNANSWERED source are re-examined whenever the current settings
    could produce a different result. A real answer — a CRC, a multi-member archive, a
    recompressed disc image — is never recomputed."""
    import time
    ensure_schema(con)
    # ONLY FILES THAT ARE ROMS. `roms` holds every file under the rom path, and on a
    # real library most of them are not games: measured on 572,951 rows, 16.1% carried a
    # ROM extension and the rest were artwork, audio and extracted game internals —
    # 79,983 .png alone. Hashing those wrote half a million rows that could never match
    # anything, and with loose hashing on it would have READ every one of them end to
    # end. The size cap does not stop that; only asking what the file is does.
    exts = ["." + e.lower().lstrip(".") for e in romtags.ROM_EXTS]
    # Retry what we declined to compute; keep what we actually determined.
    retry = list(UNANSWERED) if loose else ["zip_unreadable", "missing"]
    q = ("SELECT r.relpath, r.fullpath, r.size_bytes FROM roms r "
         "LEFT JOIN rom_hashes h ON h.relpath = r.relpath "
         "WHERE LOWER(r.ext) IN (%s) AND (h.relpath IS NULL OR h.source IN (%s))"
         % (",".join("?" * len(exts)), ",".join("?" * len(retry))))
    rows = con.execute(q + (" LIMIT %d" % int(limit) if limit else ""),
                       [e.lstrip(".") for e in exts] + retry).fetchall()

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
    exts = ",".join("'%s'" % e.lower().lstrip(".") for e in romtags.ROM_EXTS)
    return {
        "files": q("SELECT COUNT(*) FROM roms"),
        "roms": q("SELECT COUNT(*) FROM roms WHERE LOWER(ext) IN (%s)" % exts),
        "hashed": q("SELECT COUNT(*) FROM rom_hashes WHERE crc IS NOT NULL"),
        "by_source": dict(con.execute(
            "SELECT source, COUNT(*) FROM rom_hashes GROUP BY source").fetchall()),
    }


def main(argv):
    import json
    # config IS NOT REQUIRED HERE, and requiring it broke the case this CLI exists for.
    # devices.pull_roms copies build_romdb.py, romtags.py and this file to a remote
    # device and runs them there, with nothing else of ludodex present. A hard `import
    # config` made the scan die on every remote device — and because the hash is
    # deliberately allowed to fail without failing the sync, it would have died
    # silently, forever. Hashing needs no configuration; only hash_and_enrich does,
    # and that never runs on a device.
    try:
        import config                              # noqa: F401  (path side effects)
    except Exception:                              # noqa: BLE001
        pass
    path = None
    for i, a in enumerate(argv):
        if a == "--db" and i + 1 < len(argv):
            path = argv[i + 1]
    if not path:
        print("usage: romhash.py --db <roms-index.sqlite> [--scan] [--no-loose] "
              "[--limit N]", file=sys.stderr)
        return 2
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    if "--scan" in argv:
        limit = None
        if "--limit" in argv:
            limit = int(argv[argv.index("--limit") + 1])
        # --no-loose exists for the rare case of a device whose storage makes the read
        # genuinely expensive. --loose is kept so older callers do not break.
        out = scan(con, limit=limit, loose="--no-loose" not in argv)
        print(json.dumps(out, indent=2))
    else:
        print(json.dumps(coverage(con), indent=2))
    con.close()
    return 0



def _ns_to_provider():
    """index namespace -> ludodex provider, INVERTED FROM provider_ids.

    This module used to state the mapping itself, and the copy went stale the moment
    provider_ids dropped MobyGames — leaving the hash path writing a Moby handle whose
    form the provider layer had already ruled unusable. One derivation per fact is the
    rule this codebase keeps relearning, so there is now exactly one map and it lives
    where the id form is decided."""
    import provider_ids
    return {ns: provider for provider, ns in provider_ids.INDEX_NS.items()}


def entry_platform(system, title=None):
    """The catalog's entry platform for a ROM found under folder `system`.

    Composed from the same two rules build_library's `_emu_ep` composes — `norm_system`
    for the console label and `platmap.platform_from_title` for filename-beats-folder — so
    a hash identity is filed against the platform the catalog will actually give the
    entry. It cannot simply CALL `_emu_ep`: build_library is a script that runs a whole
    build on import.
    """
    from media import norm_system
    import platmap
    ep = norm_system((system or "").strip()) if system else ""
    lbl = platmap.platform_from_title(title or "")
    if lbl and platmap.canon(lbl) != platmap.canon(ep):
        ep = norm_system(lbl)
    return ep


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

    THE HASH IS EVIDENCE ABOUT ONE FILE; THE CACHE IS KEYED BY TITLE. Those two facts do
    not fit, and the old code resolved the mismatch by letting whichever row came first
    stamp the whole title bucket — out of a query with no ORDER BY, so the winner changed
    between runs, and across platforms, so a ~1994 Game Boy "Uno" recorded the identity a
    Steam "UNO" entry then wore. So the evidence is COLLECTED first and written only where
    every hashed file agrees:

      * the key is `titlenorm.catalog_key(title, entry_platform)` — the key build_library
        will actually give the entry, hardware tag stripped and merges applied;
      * files sharing a key must agree on the provider id, or nothing is recorded for that
        (key, provider) and the disagreement is reported. Disagreement IS the signal that
        one title bucket holds two games;
      * the platform is recorded alongside, so a wrong bind is auditable offline.
    """
    import time
    import matchindex
    import provider_ids
    import titlenorm

    # The tables must exist before anything is written to them. They did not have to, and
    # each write sat inside `except Exception: continue` — so on a cache missing a column
    # every write failed and the report still said `hash_hits: N`.
    provider_ids.ensure_tables(cat_con)

    mi = matchindex.connect()
    ns_map = _ns_to_provider()
    # ORDERED. Without this the winner of a contested key was sqlite's row order.
    rows = rom_con.execute(
        "SELECT r.relpath, r.game, r.system, h.crc, h.sha1 "
        "FROM rom_hashes h JOIN roms r ON r.relpath = h.relpath "
        "WHERE h.crc IS NOT NULL ORDER BY r.relpath"
        + (" LIMIT %d" % int(limit) if limit else "")
    ).fetchall()

    hits = 0
    # (norm_key, provider) -> {id: platform}. A second distinct id is a CONFLICT.
    proposed = {}
    names = {}                                       # norm_key -> (name, year)
    for i, r in enumerate(rows, 1):
        got = identify(mi, crc=r["crc"], sha1=r["sha1"])
        if not got:
            continue
        hits += 1
        plat = entry_platform(r["system"], r["game"])
        nk = titlenorm.catalog_key(r["game"] or "", plat)
        if not nk:
            continue
        names.setdefault(nk, (got.get("_name"), got.get("_year")))
        for ns, provider in ns_map.items():
            vals = [v for v in (got.get(ns) or [])
                    if provider_ids._usable_id(provider, v)]
            # ONE CANDIDATE, OR NOTHING. Several ids is normal and intended, not a merge
            # fault: ScreenScraper keeps a record per system and TheGamesDB one per
            # region, and the build attaches all of them because choosing needs the
            # platform the caller holds. Taking one here would be a guess recorded as
            # exact evidence, which nothing downstream would ever question.
            if len(vals) != 1:
                continue
            # A PER-SYSTEM PROVIDER IS PROPOSED PER PLATFORM. ScreenScraper files one
            # record per system, so the Genesis dump and the Switch copy of one game
            # legitimately hash to two different ids — keyed on (nk, provider) alone
            # that read as a conflict and BOTH were discarded.
            _pk = plat if provider_ids.is_platform_keyed(provider) else None
            proposed.setdefault((nk, provider, _pk), {}).setdefault(str(vals[0]), plat)
        if progress and i % 20000 == 0:
            print("romhash: %d/%d examined, %d hash hits" % (i, len(rows), hits),
                  file=sys.stderr)

    recorded = conflicts = write_errors = 0
    first_error = None
    for (nk, provider, _pk), byid in sorted(
            proposed.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")):
        if len(byid) != 1:
            # Two hashed files under one catalog key point at two different games. That is
            # not a tie to break — it is the title bucket holding more than one game, and
            # the identity cache has one row for it. Recording either would hand a whole
            # title someone else's id under evidence exempt from every later re-judgement.
            conflicts += 1
            continue
        pid, plat = next(iter(byid.items()))
        nm, yr = names.get(nk, (None, None))
        try:
            provider_ids.record(cat_con, provider, nk, pid, name=nm, matched_by="hash",
                                year=yr, system=plat or None, commit=False,
                                platform=plat)
            recorded += 1
        except Exception as e:                       # noqa: BLE001 — counted, not hidden
            write_errors += 1
            if first_error is None:
                first_error = str(e)[:200]
    cat_con.commit()                                 # ONE commit, not one per write
    mi.close()
    out = {"examined": len(rows), "hash_hits": hits, "ids_recorded": recorded,
           "distinct_games": len({nk for nk, _p, _pk in proposed}),
           "conflicts": conflicts, "write_errors": write_errors,
           "at": int(time.time())}
    if first_error:
        out["write_error"] = first_error
    return out


# --- the pipeline entry point ---------------------------------------------- #
# How many `roms.fullpath` values to stat before deciding the files are reachable from
# here. One is not enough: a single missing file is a deleted rom, not a wrong mount.
REACH_SAMPLE = 25


def files_reachable(con, sample=REACH_SAMPLE):
    """Can THIS machine open the files this index describes?

    A remote device builds its index on the device and ships the file back, so every
    `fullpath` in it names a path on the DEVICE. Hashing those from here opens nothing
    and writes a `too_big`/`skipped` row for every rom — a permanent negative record
    that makes a later, correctly-placed scan skip the file for good. So the paths are
    PROBED rather than assumed, and a scan that cannot see its files does not run."""
    rows = con.execute("SELECT fullpath FROM roms WHERE fullpath IS NOT NULL "
                       "LIMIT %d" % int(sample)).fetchall()
    if not rows:
        return False
    return any(os.path.exists(r[0]) for r in rows)


def hash_and_enrich(rom_db, progress=True):
    """Hash a rom index, then record every provider id its hashes identify. -> report.

    NEVER RAISES. This runs inside a device sync, where one manager's failure is already
    reported per manager and must not abort the others. A hash is an optimisation: it
    saves requests that the name path would otherwise make, so failing to compute one
    costs time and nothing else. Reporting the reason keeps a silent no-op from looking
    like a clean run.

    Loose files are read unless `romhash_loose` says otherwise. A zip needs no read at
    all — its central directory already records the CRC of the file inside, which IS the
    rom's fingerprint — so this only ever costs anything for bare files, and only for
    those under the size cap."""
    import config
    out = {"db": os.path.basename(rom_db)}
    con = None
    try:
        if not os.path.exists(rom_db):
            return dict(out, skipped="no rom index")
        con = sqlite3.connect(rom_db, timeout=60)
        con.row_factory = sqlite3.Row
        ensure_schema(con)
        if not files_reachable(con):
            # Expected for every remote device, so it is a plain fact, not an error.
            return dict(out, skipped="rom files are not reachable from this host")
        loose = config.get_bool("romhash_loose", DEFAULT_LOOSE)
        try:
            loose_max = int(config.get("romhash_loose_max_mb") or 64) * 1024 * 1024
        except (TypeError, ValueError):
            loose_max = DEFAULT_LOOSE_MAX
        out["scan"] = scan(con, loose=loose, loose_max=loose_max, progress=progress)
        cat = sqlite3.connect(os.path.join(DATA, "metadata-cache.sqlite"), timeout=60)
        try:
            out["enrich"] = enrich_from_hashes(con, cat, progress=progress)
        finally:
            cat.close()
        out["coverage"] = coverage(con)
        return out
    except Exception as e:                       # noqa: BLE001 — an optimisation, not a step
        return dict(out, error=str(e)[:200])
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:                    # noqa: BLE001
                pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
