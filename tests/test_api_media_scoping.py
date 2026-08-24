#!/usr/bin/env python3
"""Pinning art is per bucket, and a thumbnail belongs to ONE file (#28, #30).

#28 — `chosen` is per (norm_key, SYSTEM, game_key, kind); the comment in `game_media`
says so, and says an entry legitimately has SEVERAL chosen rows, one per bucket. Both
writers ignored that: `set_pins` and `art_apply` ran
`UPDATE media SET chosen=0 WHERE norm_key=? AND kind=?`, so pinning the Genesis cover of
"Doom" blanked the chosen cover of every OTHER console's entry for that title, and of the
platform-neutral store art. `art_apply` additionally never checked that the id it was
about to mark chosen belonged to the norm_key/kind it was told about, so a stray id marked
an unrelated row as this game's cover.

#30 — `_serve` keyed its thumbnail cache on `os.path.splitext(os.path.basename(path))[0]`.
That is a sha1 only for repo-materialized files. A `ref_type == "file"` row is whatever
the producing frontend named it, so two games whose local art is both `boxart.png` under
different system folders shared `THUMBS/boxart_thumb.png` and the grid showed one game's
cover for the other. The same code also wrote JPEG bytes into a `.webp`/`.gif` path.

Offline: local sqlite fixtures in an isolated data dir, and PIL on temp images.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-api-scope-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


ROWS = [
    # id, norm_key, system, game_key, kind, chosen   — one title on three consoles
    (1, "doom", "genesis", "title:doom", "cover", 1),
    (2, "doom", "genesis", "title:doom", "cover", 0),
    (3, "doom", "snes", "title:doom", "cover", 1),
    (4, "doom", "", "igdb:2600", "cover", 1),          # platform-neutral store art
    (5, "doom", "genesis", "title:doom", "logo", 1),
    (6, "quake", "", "igdb:99", "cover", 1),           # a different game entirely
]


def seed():
    for f in ("media-index.sqlite", "game-library.sqlite", "pins.sqlite"):
        p = os.path.join(DATA, f)
        if os.path.exists(p):
            os.remove(p)
    con = sqlite3.connect(app.INDEX_DB)
    con.execute("CREATE TABLE media(id INTEGER PRIMARY KEY, norm_key TEXT, system TEXT, "
                "game_key TEXT, kind TEXT, provider TEXT, ref TEXT, ref_type TEXT, "
                "ext TEXT, sha1 TEXT, width INT, height INT, chosen INTEGER DEFAULT 0)")
    for r in ROWS:
        con.execute("INSERT INTO media(id,norm_key,system,game_key,kind,provider,ref,"
                    "ref_type,ext,chosen) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (r[0], r[1], r[2], r[3], r[4], "igdb", "ref-%d" % r[0], "url",
                     "jpg", r[5]))
    con.commit()
    con.close()
    # the empty databases lib() attaches (read-only; nothing here reads them)
    for f in ("game-library.sqlite", "tags.sqlite", "user-media.sqlite", "scores.sqlite",
              "attr-overrides.sqlite"):
        sqlite3.connect(os.path.join(DATA, f)).close()


def chosen_map():
    con = sqlite3.connect(app.INDEX_DB)
    try:
        return {r[0]: r[1] for r in con.execute("SELECT id, chosen FROM media")}
    finally:
        con.close()


def main():
    print("art pins stay in their bucket; thumbnails stay with their file")

    saved_gm = app.game_media
    app.game_media = lambda key: {"stubbed": key}       # the write is what's under test

    # ---- #28, set_pins -------------------------------------------------------- #
    seed()
    app.set_pins("doom@genesis", {"kind": "cover", "ids": [2]})
    ch = chosen_map()
    check("the pinned asset is chosen", ch[2] == 1)
    check("the one it replaced in its own bucket is cleared", ch[1] == 0)
    check("the OTHER console keeps its cover", ch[3] == 1)
    check("the platform-neutral store cover survives", ch[4] == 1)
    check("another kind is untouched", ch[5] == 1)
    check("another game is untouched", ch[6] == 1)

    # ---- #28, art_apply ------------------------------------------------------- #
    seed()
    app.art_apply({"id": 2, "norm_key": "doom", "kind": "cover"})
    ch = chosen_map()
    check("art-apply chooses the asset", ch[2] == 1)
    check("art-apply clears only its own bucket", ch[1] == 0 and ch[3] == 1 and ch[4] == 1)

    seed()
    err = None
    try:
        app.art_apply({"id": 6, "norm_key": "doom", "kind": "cover"})
    except Exception as e:                                     # noqa: BLE001
        err = e
    check("art-apply refuses an id that is not this game's", err is not None)
    check("and changed nothing", chosen_map() == {r[0]: r[5] for r in ROWS})

    err = None
    try:
        app.art_apply({"id": 5, "norm_key": "doom", "kind": "cover"})
    except Exception as e:                                     # noqa: BLE001
        err = e
    check("art-apply refuses an id of another KIND", err is not None)

    app.game_media = saved_gm

    # ---- #30: the thumbnail cache key ----------------------------------------- #
    from PIL import Image
    a_dir = os.path.join(DATA, "roms", "genesis", "Sonic 2")
    b_dir = os.path.join(DATA, "roms", "snes", "Sonic 2")
    os.makedirs(a_dir, exist_ok=True)
    os.makedirs(b_dir, exist_ok=True)
    a = os.path.join(a_dir, "boxart.png")
    b = os.path.join(b_dir, "boxart.png")
    Image.new("RGB", (600, 800), (200, 10, 10)).save(a)
    Image.new("RGB", (600, 800), (10, 10, 200)).save(b)

    ra = app._serve(a, "png", "thumb")
    rb = app._serve(b, "png", "thumb")
    check("two files with the same name get different thumbnails", ra.path != rb.path)
    check("and each thumbnail is of its own image",
          Image.open(ra.path).getpixel((5, 5))[0] > 100
          and Image.open(rb.path).getpixel((5, 5))[2] > 100)

    # a re-serve still HITS the cache (the key has to be stable, not just unique)
    check("the same file resolves to the same thumbnail",
          app._serve(a, "png", "thumb").path == ra.path)

    # an edited file must not keep serving the old thumbnail
    Image.new("RGB", (600, 800), (10, 200, 10)).save(a)
    check("an edited file gets a fresh thumbnail",
          app._serve(a, "png", "thumb").path != ra.path)

    # the extension has to name the bytes
    w = os.path.join(a_dir, "art.webp")
    Image.new("RGB", (600, 800), (5, 5, 5)).save(w)
    rw = app._serve(w, "webp", "thumb")
    with open(rw.path, "rb") as f:
        head = f.read(3)
    check("a webp thumbnail saved as JPEG is not called .webp",
          not rw.path.endswith(".webp"))
    check("and the file really is a JPEG", head == b"\xff\xd8\xff")

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
