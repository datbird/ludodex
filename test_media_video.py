#!/usr/bin/env python3
"""Contract test for media_video's deterministic core (spec §5.1).

Pure logic only — no ffmpeg required, so this runs anywhere. The parts that DO need
the binaries are verified in-container (see the plan's Task 5); a test that silently
passes because ffmpeg is missing would be worse than no test.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import media_video                                      # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    print("1. frame_times — 5 frames inside the window, past the logo head")
    t = media_video.frame_times(300.0)
    check("five frames", len(t) == 5)
    check("starts at or after the 3s skip", t[0] >= 3.0)
    check("never past the 120s cap", t[-1] <= 120.0)
    check("strictly increasing", all(b > a for a, b in zip(t, t[1:])))

    print("2. frame_times — short video is sampled from 0, never negative")
    t = media_video.frame_times(2.0)
    check("still returns frames for a 2s video", len(t) == 5)
    check("no negative timestamps", all(x >= 0 for x in t))
    check("never past the video's end", all(x <= 2.0 for x in t))

    print("3. frame_times — unknown duration degrades to the head window")
    t = media_video.frame_times(None)
    check("returns frames without a duration", len(t) == 5)
    check("no negative timestamps", all(x >= 0 for x in t))

    print("4. evidence_line — probe data becomes one prompt-ready string")
    line = media_video.evidence_line(
        {"duration": 142.0, "width": 1920, "height": 1080,
         "codec": "vp9", "has_audio": True})
    check("states duration", "142" in line)
    check("states resolution", "1920x1080" in line)
    check("states codec", "vp9" in line)
    check("states audio", "audio" in line)

    print("5. evidence_line — unmeasured stays unmeasured, never invented")
    line = media_video.evidence_line(None)
    check("says unknown rather than zero", "unknown" in line.lower())
    check("no fabricated resolution", "0x0" not in line)

    print("6. VIDEO_EXTS covers what the live catalog actually holds")
    check("webm (Steam movie_max.webm)", "webm" in media_video.VIDEO_EXTS)
    check("mp4", "mp4" in media_video.VIDEO_EXTS)
    check("images are not video", "jpg" not in media_video.VIDEO_EXTS)

    print("7. sheet_path — content-addressed and stable for one ref")
    import tempfile
    repo = tempfile.mkdtemp(prefix="ludodex-video-")
    ref = "https://cdn.akamai.steamstatic.com/steam/apps/2031333/movie_max.webm"
    p1 = media_video.sheet_path(repo, ref)
    check("stable across calls", p1 == media_video.sheet_path(repo, ref))
    check("differs for a different ref",
          media_video.sheet_path(repo, ref + "x") != p1)
    check("lands under the media repo", p1.startswith(repo))
    check("is a jpeg", p1.endswith(".jpg"))

    print("8. cache hit returns bytes WITHOUT invoking ffmpeg")
    os.makedirs(os.path.dirname(p1), exist_ok=True)
    with open(p1, "wb") as fh:
        fh.write(b"cached-sheet-bytes")
    calls = []
    real_run = media_video.subprocess.run
    media_video.subprocess.run = lambda *a, **k: calls.append(a)
    try:
        got = media_video.contact_sheet("ignored-src", repo, ref)
    finally:
        media_video.subprocess.run = real_run
    check("returned the cached bytes", got == b"cached-sheet-bytes")
    check("no subprocess was launched", not calls)

    print("9. no ffmpeg -> None, never a fabricated sheet")
    real_avail = media_video.available
    media_video.available = lambda: False
    try:
        check("probe returns None", media_video.probe("anything") is None)
        check("contact_sheet returns None",
              media_video.contact_sheet("anything", repo, "uncached-ref") is None)
    finally:
        media_video.available = real_avail

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
