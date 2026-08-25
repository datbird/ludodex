#!/usr/bin/env python3
"""A half-written contact sheet must not be served for the rest of the repo's life.

`contact_sheet` caches its JPEG and guards the cache with nothing but
`os.path.exists(path)`. The WRITE side was made atomic (see test_art_atomic_writes.py),
so this code can no longer CREATE a truncated sheet — but every sheet written before
that fix is still sitting in `_videosheets/`, and existence is still all that is checked.
A sheet cut short by a crash or a full disk is therefore handed to the vision model, and
SCORED, on every call from then on: the model reads whatever frames survived plus a
decode error, and nothing ever regenerates it.

Atomic writing fixes the future. The read has to fix the past: a JPEG that does not end
in its end-of-image marker is not a sheet, so it is dropped and re-sampled instead of
returned.

Offline. No network, no ffmpeg — `available()` is stubbed so nothing is ever executed.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-vsheet-")
DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import media_video                                             # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _jpeg(size=(160, 120)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 90, 150)).save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _put(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def main():
    repo = os.path.join(DATA, "repo")
    whole = _jpeg()

    real_avail = media_video.available
    media_video.available = lambda: False       # any regeneration attempt gives up here
    calls = []
    real_run = media_video.subprocess.run
    media_video.subprocess.run = lambda *a, **k: calls.append(a)
    try:
        print("1. a complete cached sheet is still served, untouched")
        ref = "https://cdn/good.webm"
        p = media_video.sheet_path(repo, ref)
        _put(p, whole)
        got = media_video.contact_sheet("ignored-src", repo, ref)
        check("the cached bytes come back verbatim", got == whole)
        check("the file is left where it was", os.path.exists(p))
        check("nothing was executed", not calls)

        print("2. a TRUNCATED sheet is not served")
        ref = "https://cdn/truncated.webm"
        p = media_video.sheet_path(repo, ref)
        _put(p, whole[:len(whole) // 2])
        got = media_video.contact_sheet("ignored-src", repo, ref)
        check("the half-file is not returned", got is None)
        check("and it is dropped, so a later run can re-sample", not os.path.exists(p))

        print("3. a file that was never a JPEG is not served either")
        ref = "https://cdn/rubbish.webm"
        p = media_video.sheet_path(repo, ref)
        _put(p, b"cached-sheet-bytes")
        check("rubbish is not returned",
              media_video.contact_sheet("ignored-src", repo, ref) is None)
        check("and it is dropped", not os.path.exists(p))

        print("4. an empty file — the classic full-disk leftover — is not served")
        ref = "https://cdn/empty.webm"
        p = media_video.sheet_path(repo, ref)
        _put(p, b"")
        check("zero bytes is not a sheet",
              media_video.contact_sheet("ignored-src", repo, ref) is None)
        check("and it is dropped", not os.path.exists(p))
    finally:
        media_video.available = real_avail
        media_video.subprocess.run = real_run

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
