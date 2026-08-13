#!/usr/bin/env python3
"""_thumb_bytes must build a vision payload for VIDEO rows (spec §5).

Video was structurally excluded: _thumb_bytes opens the asset with PIL, which throws on
a container file, so every video candidate was dropped before the model saw it.

_thumb_bytes is the SINGLE payload builder for both vision consumers, so teaching it
video gives /api/ai/art-pick and _ai_adjudicate_game video support at once, with neither
knowing video exists (spec §7).

No ffmpeg needed — media_video.contact_sheet is stubbed. This asserts the DISPATCH,
which is the thing that was missing.
"""
import os
import sys
import tempfile

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    d = tempfile.mkdtemp(prefix="ludodex-vthumb-")
    os.environ["LUDODEX_DATA"] = d
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import media_video
    from server import app as srv

    row = {"id": 1, "provider": "steam", "ref_type": "url", "sha1": None,
           "ext": "webm",
           "ref": "https://cdn.akamai.steamstatic.com/steam/apps/1/movie_max.webm"}

    print("1. a video row yields a vision payload from its contact sheet")
    seen = {}
    real = media_video.contact_sheet
    media_video.contact_sheet = lambda src, repo, ref, **k: (
        seen.update({"src": src, "ref": ref}) or b"sheet-bytes")
    try:
        got = srv._thumb_bytes(row)
    finally:
        media_video.contact_sheet = real
    check("returned a payload", got is not None)
    check("declared as jpeg", got and got[0] == "image/jpeg")
    check("carries the sheet bytes", got and got[1] == b"sheet-bytes")
    check("sampled the URL directly, not a materialized copy",
          seen.get("src") == row["ref"])

    print("2. no sheet (no ffmpeg) -> None, candidate skipped not faked")
    real = media_video.contact_sheet
    media_video.contact_sheet = lambda src, repo, ref, **k: None
    try:
        got = srv._thumb_bytes(row)
    finally:
        media_video.contact_sheet = real
    check("returns None", got is None)

    print("3. still images are untouched by the video path")
    still = {"id": 2, "provider": "igdb", "ref_type": "url", "sha1": None,
             "ext": "jpg", "ref": "https://example.invalid/cover.jpg"}
    called = []
    real = media_video.contact_sheet
    media_video.contact_sheet = lambda *a, **k: called.append(1) or b"x"
    try:
        srv._thumb_bytes(still)
    finally:
        media_video.contact_sheet = real
    check("contact_sheet never invoked for a jpg", not called)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
