#!/usr/bin/env python3
"""Index game media by REFERENCE into media-index.sqlite (keyed by norm_key).

Local providers (scanned here):
  * esde      — ES-DE downloaded_media sets (RetroDECK / EmuDeck), one or more
                registered media mounts. Files live at
                <root>/<system>/<mediatype>/[<rom-subdirs>/]<rom-basename>.<ext>
                (ES-DE mirrors the ROM's own subfolder structure, so we recurse).
                Matched to emulation games by ROM filename -> norm_key, within
                the system (mapped to our platform label).
  * steamgrid — local Steam custom artwork (userdata/<id>/config/grid), keyed by
                appid -> our steam games.

Remote providers (steam CDN / IGDB / SteamGridDB) are resolved by media_fetch.py.

The index is keyed by the catalog's stable norm_key (game_id is reassigned every
build_library rebuild). The server / exporters join media-index.sqlite to
game-library.sqlite on norm_key. Each provider is FULLY refreshed per run (local
scans are cheap), so removed assets drop out and the index never drifts.

  python3 ludodex/media_index.py                 # scan all enabled local providers
  python3 ludodex/media_index.py --provider esde # just one provider
"""
import os
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
sys.path.insert(0, DIR)
import config
import media
import platmap
from titlenorm import norm


def _file_platform(folder_platform, name):
    """Filename > folder for media too: a file explicitly named for a platform
    ("Doom 32X (E)") is that platform's art no matter which system folder it sits in,
    so it keys the same as the (re-platformed) catalog entry. Mirrors build_library._emu_ep."""
    lbl = platmap.platform_from_title(name)
    if lbl and (not folder_platform
                or platmap.canon(lbl) != platmap.canon(folder_platform)):
        return lbl
    return folder_platform

INDEX = os.path.join(DATA, "media-index.sqlite")
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
VID_EXTS = (".mp4", ".webm", ".mkv", ".flv")
DOC_EXTS = (".pdf",)


def index_con():
    """THE media-index schema, created and healed in ONE place.

    It was written out three times — here, in media_fetch.con_index() and (as a
    different subset of ALTERs) in media_choose.con_index() — and it worked only by
    accident of ordering: whichever module opened a fresh install first decided what the
    table contained. media_choose's opener assumed the table already existed and raised
    `no such table: media` when it was the first to run. Both now delegate here."""
    con = sqlite3.connect(INDEX)
    con.execute("PRAGMA busy_timeout=30000")   # wait out concurrent media jobs' locks
    con.execute("PRAGMA journal_mode=WAL")     # readers never block the writer
    con.executescript("""
    CREATE TABLE IF NOT EXISTS media(
      id INTEGER PRIMARY KEY,
      norm_key TEXT NOT NULL,
      system TEXT,
      kind TEXT NOT NULL,
      provider TEXT NOT NULL,
      mount TEXT,
      ref_type TEXT NOT NULL,
      ref TEXT NOT NULL,
      ext TEXT,
      sha1 TEXT,
      width INTEGER,
      height INTEGER,
      chosen INTEGER DEFAULT 0,
      matched INTEGER DEFAULT 0,
      meta TEXT,
      indexed_at INTEGER,
      hidden INTEGER DEFAULT 0,
      game_key TEXT,                 -- resolved-identity key (DESIGN §11.9); see media_fetch
      UNIQUE(provider, kind, ref));
    CREATE INDEX IF NOT EXISTS ix_media_nk ON media(norm_key);
    CREATE INDEX IF NOT EXISTS ix_media_nk_kind ON media(norm_key, kind);
    """)
    _cols = {r[1] for r in con.execute("PRAGMA table_info(media)")}
    if "hidden" not in _cols:
        con.execute("ALTER TABLE media ADD COLUMN hidden INTEGER DEFAULT 0")
    # filler: 1 = confirmed letterboxed paste (real content in a central band, blurred
    # padding above/below — Steam auto-generates these for games with no library art).
    # NULL = never measured. Set at materialize time, when the bytes are in hand; the
    # selector demotes a confirmed filler beneath any authored cover. Deliberately
    # tri-state: "not yet measured" must not be treated as "fine", nor as "bad".
    if "filler" not in _cols:
        con.execute("ALTER TABLE media ADD COLUMN filler INTEGER")
    # ai_pick: 1 = a vision model examined this game+kind's candidates and chose this
    # asset. Durable — select() re-ranks deterministically on every pass and zeroes
    # `chosen`, so without this flag a paid judgment would be erased by the next sync
    # and then re-purchased. Ranked below user pins and below shape/filler EVIDENCE
    # (a later measurement may prove the AI's pick is a padded filler) but above
    # provider priority.
    if "ai_pick" not in _cols:
        con.execute("ALTER TABLE media ADD COLUMN ai_pick INTEGER")
    # detail: MEDIAN band edge energy — how much detail the image carries throughout.
    # High for authored art, low for a blurred paste. Only consulted when `filler` is
    # constant across a bucket and has therefore decided nothing; see select(). Median
    # rather than mean so a single bright wordmark cannot move it, which is exactly the
    # mistake the peak-relative filler threshold made. NULL = never measured, and an
    # unmeasured value must never win by being unknown.
    if "detail" not in _cols:
        con.execute("ALTER TABLE media ADD COLUMN detail REAL")
    # frame: a hash of the image's decorated border band (media.frame_sig). Its only
    # use is COMPARISON — one frame shared by TEMPLATE_MIN_GAMES or more distinct
    # norm_keys is a themed community pack's plate, and a plate is not any one of those
    # games' art, however correct its kind and shape. NULL = no measurable frame (not
    # yet materialized, unreadable, or a border with no design in it), and NULL never
    # participates: only a real shared signature can demote anything.
    if "frame" not in _cols:
        con.execute("ALTER TABLE media ADD COLUMN frame TEXT")
    # sil: a hash of the binarised ALPHA OUTLINE, stamped only for the kinds whose
    # shape belongs to the GAME rather than to the kind (media.SILHOUETTE_KINDS).
    # `frame` hashes border COLOURS and so misses a pack that varies its gradient per
    # game; the outline catches those. NULL where the outline is degenerate (a plain
    # rectangle, a near-empty canvas) — those would collide by the thousand.
    if "sil" not in _cols:
        con.execute("ALTER TABLE media ADD COLUMN sil TEXT")
    # probed: when prune_dead last HEAD-checked this URL and it ANSWERED. Only the chosen
    # asset ever gets a sha1, so "sha1 IS NULL" left every non-chosen candidate looking
    # unverified forever and the same handful of Steam URLs per game were re-probed with
    # 16 threads on every single sync. A proven-live ref is remembered instead, and only
    # re-probed once the record goes stale — a URL that worked can still die, so this is
    # a TTL, never a permanent pass.
    if "probed" not in _cols:
        con.execute("ALTER TABLE media ADD COLUMN probed INTEGER")
    # Outside the guard: an index created only on the branch that ADDS the column would
    # never exist on the commoner path.
    #
    # COVERING (frame, norm_key): select()'s template query groups by frame and counts
    # DISTINCT norm_key, so an index on frame alone still had to fetch every framed row.
    con.execute("CREATE INDEX IF NOT EXISTS ix_media_frame ON media(frame, norm_key)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_media_sil ON media(sil, kind, norm_key)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_media_gk ON media(game_key, system, kind)")
    con.commit()
    return con


# --------------------------------------------------------------------------- #
#  One writer for every local scan
#
#  The scanners used INSERT OR REPLACE and main() opened with a blanket per-provider
#  DELETE, so a rescan destroyed and rebuilt rows for files that had not changed.
#  media_fetch.put() has carried the reason not to for as long as it has existed:
#  REPLACE deletes the existing row, which drops its `sha1` — the pointer to bytes
#  already sitting in the media repo — along with the measured width/height, the
#  `filler`/`detail`/`frame`/`sil` image evidence the ranker sorts on, the PAID
#  `ai_pick` verdict and the language `hidden` flag. Every local scan re-copied,
#  re-measured and re-purchased all of it.
#
#  "Each provider is FULLY refreshed per run (local scans are cheap), so removed
#  assets drop out" is still true, and still the point — it is just done by SWEEPING
#  the rows this scan did not see, which reaches the same end state without touching
#  the survivors.
# --------------------------------------------------------------------------- #
_BANNED = None


def _banned():
    """Cached {(norm_key, kind, provider, ref)} the user banned — never re-index these.

    mediaflags says the ban is "Enforced in media_fetch.put()", and that was the whole
    defect: a banned LOCAL asset was put straight back by the next scan, so the ban only
    ever held for art fetched over HTTP."""
    global _BANNED
    if _BANNED is None:
        try:
            import mediaflags
            _BANNED = mediaflags.banned_set()
        except Exception:                    # noqa: BLE001 — a missing flag DB bans nothing
            _BANNED = set()
    return _BANNED


def invalidate_banned():
    """Drop the cached ban set. The server imports this module and lives for days, so a
    ban applied in the UI has to be visible to the next scan without a restart."""
    global _BANNED
    _BANNED = None


def put_local(con, nk, kind, provider, path, ext, now, system=None, mount=None,
              matched=0):
    """Index ONE local file by reference. Returns True when a row was written.

    ON CONFLICT ... DO UPDATE, never INSERT OR REPLACE — see the block comment above.
    Only the facts a rescan can actually re-derive are refreshed; everything measured,
    paid for or chosen downstream is left exactly as it was."""
    if (nk, kind, provider, path) in _banned():
        return False               # banned: a rescan must not resurrect it (then swept)
    con.execute(
        "INSERT INTO media(norm_key,system,kind,provider,mount,ref_type,ref,ext,"
        "matched,indexed_at) VALUES(?,?,?,?,?,'file',?,?,?,?) "
        "ON CONFLICT(provider,kind,ref) DO UPDATE SET "
        "norm_key=excluded.norm_key, system=excluded.system, mount=excluded.mount, "
        "ext=excluded.ext, matched=excluded.matched, indexed_at=excluded.indexed_at",
        (nk, system, kind, provider, mount, path, ext, int(matched), now))
    return True


def sweep(con, provider, now, ref_prefix=None):
    """Drop this provider's rows that the just-finished scan did not touch.

    The "removed assets drop out" half of a full refresh. Every row the scan saw was
    stamped `indexed_at = now`, so anything still carrying an older stamp is a file that
    is gone (or one the user has since banned). `ref_prefix` scopes the sweep to one
    scanned root, exactly as the gamelist DELETE it replaces did."""
    q = "DELETE FROM media WHERE provider=? AND COALESCE(indexed_at,0)!=?"
    args = [provider, now]
    if ref_prefix:
        q += " AND ref LIKE ?"
        args.append(ref_prefix.rstrip("/") + "/%")
    n = con.execute(q, args).rowcount
    con.commit()
    return n


def catalog():
    """Return (owned norm_keys set, {steam appid -> norm_key})."""
    lib = config.get("library_db")
    owned, steam = set(), {}
    if not (lib and os.path.exists(lib)):
        print("media_index: no catalog (%s) — matched flags will be 0" % lib,
              file=sys.stderr)
        return owned, steam
    con = sqlite3.connect(lib)
    owned = {k for (k,) in con.execute("SELECT norm_key FROM games")}
    for nk, sid in con.execute(
            "SELECT g.norm_key, s.source_id FROM games g JOIN sources s "
            "ON s.game_id=g.id WHERE s.source='steam'"):
        if sid and str(sid).isdigit():
            steam[str(sid)] = nk
    con.close()
    return owned, steam


_SEEN_UNKNOWN_ESDE = set()


def ext_kind_ok(ext, kind):
    """Sanity: the file extension must suit the canonical kind."""
    e = ext.lower()
    if kind == "video":
        return e in VID_EXTS
    if kind == "manual":
        return e in DOC_EXTS
    return e in IMG_EXTS


# EmulationStation / RetroArch "gamelist" media roles (the -<role> filename suffix)
# -> canonical ludodex kind. Verified against real files: -image is an in-game
# screenshot, -thumb is the box art (portrait/near-square), -marquee is the
# wheel/clear-logo. Extra Skraper/ES roles mapped generously.
GAMELIST_ROLE_KIND = {
    "thumb": "cover", "boxart": "cover", "box2dfront": "cover", "box": "cover",
    "cover": "cover",
    "image": "screenshot", "screenshot": "screenshot", "snap": "screenshot",
    "marquee": "logo", "wheel": "logo", "logo": "logo",
    "title": "title_screen", "titlescreen": "title_screen",
    "fanart": "background", "background": "background",
    "video": "video",
}


def scan_gamelist(con, owned, now, root):
    """Index EmulationStation/RetroArch 'gamelist' art that lives INSIDE a ROM tree,
    in place (no move): <root>/<system>/.../images/<game>-<role>.<ext>. Matched to
    emulation games by the game portion -> norm_key. Returns (rows, matched)."""
    import subprocess
    root = (root or "").rstrip("/")
    if not root or not os.path.isdir(root):
        return 0, 0
    # re-scannable WITHOUT a delete-then-rebuild: rows are upserted and whatever this
    # pass does not touch is swept at the end (see put_local / sweep).
    # find just the 'images' dirs — don't walk the whole (huge) ROM tree
    try:
        out = subprocess.run(["find", root, "-type", "d", "-name", "images"],
                             capture_output=True, text=True, timeout=600).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    rows = matched = 0
    for imgdir in out.splitlines():
        rel = os.path.relpath(imgdir, root)
        system = rel.split(os.sep, 1)[0] if rel not in (".", "") else ""
        platform = media.norm_system(system) if system else None
        try:
            files = os.listdir(imgdir)
        except OSError:
            continue
        for fn in files:
            base, ext = os.path.splitext(fn)
            if ext.lower() not in IMG_EXTS or "-" not in base:
                continue
            stem, role = base.rsplit("-", 1)        # role suffix has no surrounding spaces
            kind = GAMELIST_ROLE_KIND.get(role.lower())
            if not kind:
                continue
            fplat = _file_platform(platform, stem)  # filename > folder
            nk = norm(stem, fplat)                  # platform-aware: strip baked-in HW tag
            if not nk:
                continue
            is_match = nk in owned
            if not put_local(con, nk, kind, "gamelist",
                             os.path.join(imgdir, fn), ext.lower().lstrip("."), now,
                             system=fplat, mount="gamelist", matched=is_match):
                continue
            rows += 1
            matched += int(is_match)
    con.commit()
    sweep(con, "gamelist", now, ref_prefix=root)
    return rows, matched


def scan_esde(con, owned, now):
    """Walk every enabled ES-DE media mount; return (#rows, #matched)."""
    rows = matched = 0
    for mount in config.media_mounts_list(only_enabled=True, provider="esde"):
        root = mount["path"]
        if not os.path.isdir(root):
            print("media_index: esde mount %r missing (%s) — skipped"
                  % (mount["name"], root), file=sys.stderr)
            continue
        # per-mount kinds filter: empty = index every media kind found
        want_kinds = set(mount.get("kinds") or ())
        for system in sorted(os.listdir(root)):
            sysdir = os.path.join(root, system)
            if not os.path.isdir(sysdir):
                continue
            platform = media.norm_system(system)
            for mtype in sorted(os.listdir(sysdir)):
                tdir = os.path.join(sysdir, mtype)
                if not os.path.isdir(tdir):
                    continue
                kind = media.ESDE_TYPE_KIND.get(mtype)
                if kind is None:            # unknown folder -> classify, never drop
                    kind = "other"
                    if mtype not in _SEEN_UNKNOWN_ESDE:
                        _SEEN_UNKNOWN_ESDE.add(mtype)
                        print("media_index: esde unmapped media folder %r -> "
                              "'other'" % mtype, file=sys.stderr)
                if want_kinds and kind not in want_kinds:
                    continue                # this mount opted out of this kind
                for dirpath, _d, files in os.walk(tdir):
                    for fn in files:
                        base, ext = os.path.splitext(fn)
                        if not ext_kind_ok(ext, kind):
                            continue
                        fplat = _file_platform(platform, base)  # filename > folder
                        nk = norm(base, fplat)      # platform-aware: strip baked-in HW tag
                        if not nk:
                            continue
                        is_match = nk in owned
                        if not put_local(con, nk, kind, "esde",
                                         os.path.join(dirpath, fn),
                                         ext.lower().lstrip("."), now,
                                         system=fplat, mount=mount["name"],
                                         matched=is_match):
                            continue
                        rows += 1
                        matched += int(is_match)
        con.commit()
    # After EVERY mount, never per mount: one mount's rows must not be swept because a
    # later mount is the one being walked.
    sweep(con, "esde", now)
    return rows, matched


def steam_grid_dirs():
    """Configured grid path, else autodetect Steam userdata grid folders."""
    p = config.get("steam_grid_path")
    if p and os.path.isdir(p):
        return [p]
    import glob
    found = []
    for pat in ("~/.steam/steam/userdata/*/config/grid",
                "~/.local/share/Steam/userdata/*/config/grid"):
        found += glob.glob(os.path.expanduser(pat))
    return sorted(set(os.path.realpath(d) for d in found if os.path.isdir(d)))


def scan_steamgrid(con, steam, now):
    """Index local Steam custom artwork keyed by appid -> steam games."""
    rows = matched = 0
    for gdir in steam_grid_dirs():
        for fn in os.listdir(gdir):
            appid, kind = media.steamgrid_kind(fn)
            if not (appid and kind):
                continue
            nk = steam.get(appid)
            if not nk:                      # art for a non-owned/shortcut appid
                continue
            ext = os.path.splitext(fn)[1].lower().lstrip(".")
            if not put_local(con, nk, kind, "steamgrid", os.path.join(gdir, fn),
                             ext, now, mount="steam", matched=1):
                continue
            rows += 1
            matched += 1
    con.commit()
    sweep(con, "steamgrid", now)
    return rows, matched


def main(argv):
    only = argv[argv.index("--provider") + 1] if "--provider" in argv else None
    gl_root = argv[argv.index("--gamelist") + 1] if "--gamelist" in argv else None
    owned, steam = catalog()
    con = index_con()
    now = int(time.time())

    # --gamelist <root>: index in-place ES/RetroArch art under a ROM tree, only.
    if gl_root:
        r, m = scan_gamelist(con, owned, now, gl_root)
        print("media_index: gamelist — %d assets (%d matched) under %s"
              % (r, m, gl_root), file=sys.stderr)
        con.commit()
        con.close()
        return

    if only in (None, "esde") and config.media_enabled("esde"):
        # No blanket DELETE: scan_esde upserts and then sweeps what it did not see, so
        # an unchanged file keeps its sha1 / measurements / ai_pick / hidden.
        r, m = scan_esde(con, owned, now)
        print("media_index: esde — %d assets (%d matched to a catalog game)"
              % (r, m), file=sys.stderr)
    if only in (None, "steamgrid") and config.media_enabled("steamgrid"):
        r, m = scan_steamgrid(con, steam, now)
        print("media_index: steamgrid — %d assets" % r, file=sys.stderr)

    tot = con.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    bykind = con.execute("SELECT provider,kind,COUNT(*) FROM media "
                         "GROUP BY provider,kind ORDER BY 1,3 DESC").fetchall()
    con.commit()
    con.close()
    print("media_index: %d total assets in %s" % (tot, INDEX), file=sys.stderr)
    for p, k, c in bykind:
        print("    %-10s %-14s %d" % (p, k, c), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
