#!/usr/bin/env python3
"""A local rescan must not throw away everything the pipeline learned.

`media_fetch.put()` has carried the reason in a comment for as long as it has existed:

    ON CONFLICT ... DO UPDATE, never INSERT OR REPLACE: the unique key is
    (provider, kind, ref), and REPLACE deletes the existing row — silently dropping its
    `sha1`, which is the pointer to the ALREADY-DOWNLOADED bytes in the media repo.

Every LOCAL scanner did exactly what that comment forbids. `scan_esde`, `scan_gamelist`
and `scan_steamgrid` all used `INSERT OR REPLACE`, and `main()` opened with a blanket
`DELETE FROM media WHERE provider='esde'`. So each rescan destroyed and rebuilt rows for
files that had not changed at all, discarding:

  * `sha1`          — the copy already in the media repo, so it is copied again
  * `width/height`  — so it is measured again
  * `filler`/`detail`/`frame`/`sil` — the image-fitness evidence the ranker sorts on
  * `ai_pick`       — a PAID vision verdict, re-purchased on the next adjudication
  * `hidden`        — the language filter's flag

And none of them consulted `mediaflags.banned_set()`. The module doc says the ban is
"Enforced in media_fetch.put()" — true, and that is the defect: a banned LOCAL asset was
re-indexed by the very next scan, so the ban only held for things fetched over HTTP.

Removed files must still drop out — that is what "each provider is FULLY refreshed per
run" is for — so the delete is replaced by a SWEEP of the rows this scan did not see,
which reaches the same end state without touching the survivors.

Offline. Fixture files only, no network.
"""
import io
import os
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-rescan-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import config                                                  # noqa: E402
import media_index                                             # noqa: E402
import mediaflags                                              # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _png(path, size=(600, 900)):
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, (20, 60, 120)).save(path, "PNG")
    return path


OWNED = {"sonic the hedgehog", "golden axe"}


def row(con, ref):
    r = con.execute("SELECT id,sha1,width,height,filler,ai_pick,hidden,detail "
                    "FROM media WHERE ref=?", (ref,)).fetchone()
    if not r:
        return None
    return dict(zip(("id", "sha1", "width", "height", "filler", "ai_pick",
                     "hidden", "detail"), r))


def learn(con, ref):
    """Everything the rest of the pipeline stamps onto a row after the scan."""
    con.execute("UPDATE media SET sha1='deadbeef', width=600, height=900, filler=0, "
                "ai_pick=1, hidden=1, detail=0.42, chosen=1 WHERE ref=?", (ref,))
    con.commit()


def main():
    con = media_index.index_con()
    now = int(time.time())

    print("1. ES-DE: a rescan of an unchanged file preserves the row")
    root = os.path.join(DATA, "esde")
    sonic = _png(os.path.join(root, "megadrive", "covers",
                              "Sonic the Hedgehog.png"))
    axe = _png(os.path.join(root, "megadrive", "covers", "Golden Axe.png"))
    config.media_mount_set("m1", root, "esde")

    media_index.scan_esde(con, OWNED, now)
    before = row(con, sonic)
    check("the scan indexed the cover", before is not None)
    learn(con, sonic)
    learned = row(con, sonic)

    media_index.scan_esde(con, OWNED, now + 1)
    after = row(con, sonic)
    check("the row is the SAME row, not a fresh one", after["id"] == learned["id"])
    check("sha1 survives — the bytes are not re-copied", after["sha1"] == "deadbeef")
    check("measured dimensions survive", (after["width"], after["height"]) == (600, 900))
    check("the filler verdict survives", after["filler"] == 0)
    check("the PAID ai_pick verdict survives", after["ai_pick"] == 1)
    check("the language hidden flag survives", after["hidden"] == 1)
    check("the detail measurement survives", abs((after["detail"] or 0) - 0.42) < 1e-6)

    print("2. but a file that is GONE still drops out")
    os.remove(axe)
    media_index.scan_esde(con, OWNED, now + 2)
    check("the removed file's row is swept", row(con, axe) is None)
    check("and the surviving row kept its sha1",
          row(con, sonic)["sha1"] == "deadbeef")

    print("3. a BANNED local asset is not resurrected by the next scan")
    mediaflags.ban("sonic the hedgehog", "cover", "esde", sonic)
    media_index.invalidate_banned()
    media_index.scan_esde(con, OWNED, now + 3)
    check("the banned asset is gone from the index", row(con, sonic) is None)
    media_index.scan_esde(con, OWNED, now + 4)
    check("and stays gone on the scan after that", row(con, sonic) is None)
    mediaflags.unban("sonic the hedgehog", "cover", "esde", sonic)
    media_index.invalidate_banned()
    media_index.scan_esde(con, OWNED, now + 5)
    check("un-banning lets it back in", row(con, sonic) is not None)

    print("4. gamelist art in a ROM tree behaves the same way")
    roms = os.path.join(DATA, "roms")
    gl = _png(os.path.join(roms, "megadrive", "images",
                           "Sonic the Hedgehog-thumb.png"))
    media_index.scan_gamelist(con, OWNED, now, roms)
    check("gamelist art indexed", row(con, gl) is not None)
    learn(con, gl)
    media_index.scan_gamelist(con, OWNED, now + 1, roms)
    check("a gamelist rescan keeps sha1 too", row(con, gl)["sha1"] == "deadbeef")
    mediaflags.ban("sonic the hedgehog", "cover", "gamelist", gl)
    media_index.invalidate_banned()
    media_index.scan_gamelist(con, OWNED, now + 2, roms)
    check("a banned gamelist asset stays out", row(con, gl) is None)
    mediaflags.unban("sonic the hedgehog", "cover", "gamelist", gl)
    media_index.invalidate_banned()

    print("5. local Steam grid art behaves the same way")
    grid = os.path.join(DATA, "grid")
    cap = _png(os.path.join(grid, "440p.png"))
    config.set_("steam_grid_path", grid)
    steam = {"440": "team fortress 2"}
    media_index.scan_steamgrid(con, steam, now)
    check("grid art indexed", row(con, cap) is not None)
    learn(con, cap)
    media_index.scan_steamgrid(con, steam, now + 1)
    check("a steamgrid rescan keeps sha1", row(con, cap)["sha1"] == "deadbeef")
    mediaflags.ban("team fortress 2", "cover", "steamgrid", cap)
    media_index.invalidate_banned()
    media_index.scan_steamgrid(con, steam, now + 2)
    check("a banned grid asset stays out", row(con, cap) is None)

    print("6. no scanner reaches for REPLACE any more")
    src = open(os.path.join(DIR, "ludodex", "media_index.py"), encoding="utf-8").read()
    check("INSERT OR REPLACE INTO media is gone",
          "INSERT OR REPLACE INTO media" not in src)
    check("and so is the blanket per-provider DELETE",
          "DELETE FROM media WHERE provider='esde'" not in src
          and "DELETE FROM media WHERE provider='steamgrid'" not in src)
    con.close()

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
