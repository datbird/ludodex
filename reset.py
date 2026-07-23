#!/usr/bin/env python3
"""Reset — put the install back to a known state without hand-deleting files.

Three scopes, each a strict superset of the one before:

  library   Everything an IMPORT produced. Credentials, your account, configured
            devices and all hand-curation survive. This is the "let me try that
            import again" button, and the one you want almost every time.
  curation  The above, plus what YOU made about games — tags, art pins, merges,
            splits, attribute overrides, framing, ownership, manual entries.
            Nothing about how to reach a service is touched.
  factory   The above, plus credentials and device connections. Your login and
            your backup archives are kept, so this is recoverable and cannot lock
            you out.

Two things a database snapshot does NOT cover are handled here, because leaving
them behind is exactly what makes a "reset" fail to reset:

  * store ownership TSVs (steam_games.tsv & co) — a later rebuild reads these and
    silently repopulates a library you thought you had emptied
  * the content-addressed media repo — orphan image blobs

Everything is previewable: plan() reports precisely what run() would remove, so a
destructive button can show its work first. run() never deletes a file it was not
asked about, and the caller is expected to take a safety snapshot beforehand.
"""
import glob
import os
import shutil

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)

SCOPES = ("library", "curation", "factory")

# Everything an import builds or caches. Deleting these costs only the time to
# re-import — no human decision is lost.
IMPORT_DBS = [
    "game-library.sqlite",      # the catalog itself (rebuilt from sources)
    "media-index.sqlite",       # where art lives (re-indexed / re-fetched)
    "metadata-cache.sqlite",    # IGDB resolution + payload cache
    "screenscraper-cache.sqlite",
    "sync_cache.sqlite",
    "ai-metadata.sqlite",       # scan findings — all about games being removed
    "ingest-hints.sqlite",      # AI ingest conclusions
    "os.sqlite",                # fetched per-appid OS support
    "ra.sqlite",                # RetroAchievements cache
    "scores.sqlite",            # fetched ratings, not user-entered
    "steam-tags.sqlite",        # SteamSpy community tags — a 30-day-TTL fetch cache,
                                # so leaving it behind makes a "reset" quietly partial:
                                # the next sync skips every game still inside the TTL
    "crawl-index.sqlite",       # file crawl inventory + extracted facts (re-crawlable)
    "steam-meta.sqlite",        # Steam appdetails attribute cache (tiered ingest) —
                                # regenerable from the next Steam-media pass
]
# What the user personally decided about their games.
CURATION_DBS = [
    "tags.sqlite", "pins.sqlite", "user-media.sqlite", "manual-games.sqlite",
    "collections.sqlite", "attr-overrides.sqlite", "merges.sqlite",
    "splits.sqlite", "framing.sqlite", "ownership.sqlite",
    "media-flags.sqlite",       # assets you banned / marked non-redistributable
    "identity-disable.sqlite",  # providers you turned off per game (tiered ingest)
]
# How to reach the outside world.
CONFIG_DBS = ["config.sqlite", "connections.sqlite", "file-profiles.sqlite"]
# Never removed by any scope: the way back in, and the way back.
KEEP_ALWAYS = {"auth.sqlite", "backups.sqlite", "ai-usage.sqlite"}

# Store ownership/wishlist dumps. A rebuild treats these as truth, so a reset that
# leaves them behind isn't a reset.
TSV_GLOB = "*.tsv"
# Per-manager ROM indexes, rebuilt by rescanning the device.
ROM_INDEX_GLOB = "roms-index*.sqlite"
# Cached store auth tokens (device-code / OAuth logins), removed with credentials.
TOKEN_DIRS = [".steam", ".gog", ".ea", ".psn", ".xbox"]


def _dbs_for(scope):
    out = list(IMPORT_DBS)
    if scope in ("curation", "factory"):
        out += CURATION_DBS
    if scope == "factory":
        out += CONFIG_DBS
    return [f for f in out if f not in KEEP_ALWAYS]


def _size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def _repo_dir():
    """The media repo. Defer to media_choose so the two can never disagree about
    where the blobs live — that resolution honours LUDODEX_MEDIA (which the server
    sets in its entrypoint, and which is NOT visible to `docker exec`), then the
    media_repo config key, then <DATA>/media."""
    try:
        import media_choose
        return media_choose.repo_dir()
    except Exception:                           # noqa: BLE001  (CLI without deps)
        p = os.environ.get("LUDODEX_MEDIA", "").strip()
        if not p:
            try:
                import config
                p = config.get("media_repo") or ""
            except Exception:                   # noqa: BLE001
                p = ""
        return p or os.path.join(DATA, "media")


# Regenerable subdirectories of the repo — safe to clear, rebuilt on demand.
REPO_CLEARABLE_DIRS = (".thumbs",)


def _repo_stats(root):
    """(files, bytes, preserved_dirs) for the media repo.

    Counts ONLY what a reset would actually delete: the content-addressed blobs at
    the top level plus the regenerable caches. Every OTHER directory is preserved
    and reported — the repo is a plausible home for a media backup (a real install
    had a 9.4 GB `.backup-<date>/` sitting in it), and a reset that removes the
    library must never take a backup with it."""
    n = size = 0
    preserved = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return 0, 0, []
    for name in entries:
        p = os.path.join(root, name)
        if os.path.isdir(p):
            if name in REPO_CLEARABLE_DIRS:
                for base, _d, files in os.walk(p):
                    for f in files:
                        n += 1
                        size += _size(os.path.join(base, f))
            else:
                preserved.append(name)
        else:
            n += 1
            size += _size(p)
    return n, size, preserved


def plan(scope="library"):
    """What run(scope) would remove — file names, counts and bytes. Pure: touches
    nothing. This is what a confirmation dialog should render."""
    if scope not in SCOPES:
        raise ValueError("unknown scope %r" % scope)
    dbs = [f for f in _dbs_for(scope) if os.path.exists(os.path.join(DATA, f))]
    tsvs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(DATA, TSV_GLOB)))
    roms = sorted(os.path.basename(p) for p in glob.glob(os.path.join(DATA, ROM_INDEX_GLOB)))
    repo = _repo_dir()
    media_n, media_bytes, media_kept = _repo_stats(repo) if os.path.isdir(repo) \
        else (0, 0, [])
    tokens = [d for d in TOKEN_DIRS if os.path.isdir(os.path.join(DATA, d))] \
        if scope == "factory" else []
    db_bytes = sum(_size(os.path.join(DATA, f)) for f in dbs)
    tsv_bytes = sum(_size(os.path.join(DATA, f)) for f in tsvs)
    rom_bytes = sum(_size(os.path.join(DATA, f)) for f in roms)
    return {
        "scope": scope,
        "databases": dbs, "database_bytes": db_bytes,
        "tsvs": tsvs, "tsv_bytes": tsv_bytes,
        "rom_indexes": roms, "rom_index_bytes": rom_bytes,
        "media_files": media_n, "media_bytes": media_bytes, "media_repo": repo,
        "media_preserved": media_kept,
        "token_dirs": tokens,
        "kept": sorted(KEEP_ALWAYS),
        "total_bytes": db_bytes + tsv_bytes + rom_bytes + media_bytes,
    }


def run(scope="library"):
    """Perform the reset. Take a snapshot FIRST — this does not do it for you, so
    that the caller decides where the way-back lives.

    Databases are deleted rather than emptied: every module recreates its schema on
    next open (they all use CREATE TABLE IF NOT EXISTS), so a missing file is the
    cleanest possible empty state and cannot leave a stale column behind."""
    if scope not in SCOPES:
        raise ValueError("unknown scope %r" % scope)
    p = plan(scope)
    removed, failed = [], []

    def _rm(path, label):
        try:
            os.remove(path)
            removed.append(label)
        except FileNotFoundError:
            pass
        except OSError as e:
            failed.append("%s: %s" % (label, e))

    for f in p["databases"]:
        _rm(os.path.join(DATA, f), f)
        for side in ("-wal", "-shm"):           # a stale WAL would replay onto the
            _rm(os.path.join(DATA, f + side), f + side)   # file we just removed
    for f in p["tsvs"] + p["rom_indexes"]:
        _rm(os.path.join(DATA, f), f)
    # Media: remove the content-addressed blobs and the regenerable caches ONLY.
    # Never rmtree the repo root — anything else in there (a media backup, say) is
    # not ours to delete and is reported as preserved instead.
    repo = p["media_repo"]
    if p["media_files"] and os.path.isdir(repo):
        gone = 0
        for name in sorted(os.listdir(repo)):
            path = os.path.join(repo, name)
            if os.path.isdir(path):
                if name not in REPO_CLEARABLE_DIRS:
                    continue                    # preserved — see plan()["media_preserved"]
                try:
                    shutil.rmtree(path)
                    os.makedirs(path, exist_ok=True)
                except OSError as e:
                    failed.append("%s: %s" % (name, e))
                continue
            try:
                os.remove(path)
                gone += 1
            except OSError as e:
                failed.append("%s: %s" % (name, e))
        removed.append("%d media file(s)" % gone)
    for d in p["token_dirs"]:
        try:
            shutil.rmtree(os.path.join(DATA, d))
            removed.append(d + "/")
        except OSError as e:
            failed.append("%s: %s" % (d, e))
    return {"ok": not failed, "scope": scope, "removed": removed,
            "failed": failed, "freed_bytes": p["total_bytes"]}
