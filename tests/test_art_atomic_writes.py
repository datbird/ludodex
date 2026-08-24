#!/usr/bin/env python3
"""A half-written file must never be served, and must never be left behind.

Two places wrote a cache entry in a way that can leave rubbish where good art is
expected, and both are guarded by nothing more than "does the path exist":

  * `media_choose._materialize_row` wrote to `dest + ".tmp"` — ONE shared name per sha.
    Two concurrent materializations of the same asset (the batch pass and a serve-time
    fetch, two devices exporting at once) both open the same temp path and interleave
    their writes, then both `shutil.move` it into place. The winner is whatever bytes
    happened to be in the file. The orphans are worse: the size summary already skips
    `.tmp` files, so they were expected AND never cleaned, and they accumulate for the
    life of the repo.

  * `media_video.contact_sheet` writes its JPEG directly to the cache path, then trusts
    `os.path.exists(path)` on the next call. A crash, a full disk or a killed job leaves
    a truncated sheet that is returned to the vision model — and the model scores it —
    for as long as the file sits there.

The fix in both places is the same one `_materialize_row` was half-doing: write to a
PRIVATE temp name in the destination directory, then rename. A rename is atomic, so a
reader sees either the old state or the complete file and never a partial one.

Offline. No network.
"""
import io
import os
import sys
import threading

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-atomic-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import media                                                   # noqa: E402
import media_choose                                            # noqa: E402
import media_video                                             # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _png(path, size=(600, 900), color=(10, 120, 200)):
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, color).save(path, "PNG")
    return path


def main():
    repo = media_choose.repo_dir()
    src = _png(os.path.join(DATA, "src.png"))
    row = {"id": 1, "ref_type": "url", "ref": "file://" + src, "provider": "igdb",
           "ext": "png", "kind": "cover"}

    print("1. concurrent materializations of the same asset do not collide")
    results = []
    errs = []

    def go():
        try:
            results.append(media_choose._materialize_row(repo, row))
        except Exception as e:                          # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=go) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("no thread raised", not errs)
    check("all eight agree on the sha", len(set(results)) == 1 and results[0])
    dest = os.path.join(repo, "%s.png" % results[0])
    check("the file is complete and decodable",
          media.asset_format(open(dest, "rb").read()) == "png")

    print("2. no orphan temp files are left in the repo")
    leftovers = [f for f in os.listdir(repo) if ".tmp" in f]
    check("nothing named .tmp survives (%r)" % leftovers, not leftovers)

    print("3. an orphan from an older run is swept, and real art is not")
    orphan = os.path.join(repo, "deadbeef.jpg.tmp")
    open(orphan, "wb").write(b"half a cover")
    check("a temp file that might still be being written is left alone",
          media_choose.sweep_temp(repo) == 0 and os.path.exists(orphan))
    swept = media_choose.sweep_temp(repo, older_than=0)
    check("an abandoned one is removed", swept >= 1 and not os.path.exists(orphan))
    check("the real asset is untouched", os.path.exists(dest))

    print("4. a video contact sheet is renamed into place, never written in place")
    # The `os.path.exists` guard on the cache is only sound if the file cannot exist
    # half-finished, so the write is what has to be atomic: no partial sheet is ever
    # given the real name, and nothing is left behind when a pass dies mid-write.
    ref = "https://cdn/trailer.webm"
    path = media_video.sheet_path(repo, ref)
    got = media_video.contact_sheet("/nonexistent/clip.webm", repo, ref)
    check("an unreadable source produces no sheet", got is None)
    check("and leaves no file at the cache path", not os.path.exists(path))
    check("nor a temp file beside it",
          not os.path.isdir(os.path.dirname(path))
          or not [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")])

    src_txt = open(os.path.join(DIR, "ludodex", "media_video.py"),
                   encoding="utf-8").read()
    check("contact_sheet renames rather than writing the cache path directly",
          "os.replace(" in src_txt)
    mc = open(os.path.join(DIR, "ludodex", "media_choose.py"), encoding="utf-8").read()
    check("_materialize_row no longer moves a shared temp name into place",
          "shutil.move(tmp, dest)" not in mc)
    check("it uses a private temp in the repo instead",
          "tempfile.mkstemp(dir=repo" in mc)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
