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

  python3 media_index.py                 # scan all enabled local providers
  python3 media_index.py --provider esde # just one provider
"""
import os
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
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
    return con


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
    # re-scannable: drop this root's prior gamelist rows first
    con.execute("DELETE FROM media WHERE provider='gamelist' AND ref LIKE ?", (root + "/%",))
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
            con.execute(
                "INSERT OR REPLACE INTO media(norm_key,system,kind,provider,mount,"
                "ref_type,ref,ext,matched,indexed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (nk, fplat, kind, "gamelist", "gamelist", "file",
                 os.path.join(imgdir, fn), ext.lower().lstrip("."), int(is_match), now))
            rows += 1
            matched += int(is_match)
    con.commit()
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
                        con.execute(
                            "INSERT OR REPLACE INTO media(norm_key,system,kind,"
                            "provider,mount,ref_type,ref,ext,matched,indexed_at)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (nk, fplat, kind, "esde", mount["name"], "file",
                             os.path.join(dirpath, fn), ext.lower().lstrip("."),
                             int(is_match), now))
                        rows += 1
                        matched += int(is_match)
        con.commit()
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
            con.execute(
                "INSERT OR REPLACE INTO media(norm_key,system,kind,provider,"
                "mount,ref_type,ref,ext,matched,indexed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (nk, None, kind, "steamgrid", "steam", "file",
                 os.path.join(gdir, fn), ext, 1, now))
            rows += 1
            matched += 1
    con.commit()
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
        con.execute("DELETE FROM media WHERE provider='esde'")
        r, m = scan_esde(con, owned, now)
        print("media_index: esde — %d assets (%d matched to a catalog game)"
              % (r, m), file=sys.stderr)
    if only in (None, "steamgrid") and config.media_enabled("steamgrid"):
        con.execute("DELETE FROM media WHERE provider='steamgrid'")
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
