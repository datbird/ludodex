# Video Vision Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make video candidates reachable by the vision layer, so the AI can judge which
trailer is the right game and the best one to feature — the one media kind that has
never had an AI path.

**Architecture:** A new `media_video.py` produces deterministic evidence from a video —
`ffprobe` metadata plus an `ffmpeg` contact sheet of 5 frames past the logo window. The
sheet is a normal JPEG, so it enters the EXISTING vision payload builder `_thumb_bytes`
unchanged, which means both consumers (`/api/ai/art-pick` and `_ai_adjudicate_game`)
gain video from one integration point. No new pick path, per spec §7.

**Tech Stack:** Python 3.12 stdlib + `subprocess`, Pillow (already a dependency, used
for tiling), `ffmpeg`/`ffprobe` (new image dependency), SQLite media index.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-01-media-wand-design.md` §5. Read it first.
- **One discovery/pick path (§7).** Video integrates by making `_thumb_bytes` return
  bytes for a video row. Do NOT add a video branch to `art_pick` or
  `_ai_adjudicate_game`; they must not know video exists.
- **Sampling window:** 5 frames, evenly spaced from **3s** to `min(duration, 120s)`. A
  video shorter than 3s is sampled from 0.
- **`-ss` BEFORE `-i`.** Fast seek reads only needed byte ranges — a 40 MB trailer costs
  a few hundred KB. Never download the whole file, and never route video through
  `_asset_local_path` (its URL branch materializes the entire asset).
- **Caps:** at most 5 frames per video; `PROBE_TIMEOUT_S = 20`; `SAMPLE_TIMEOUT_S = 60`
  total per video. A pathological or unreachable source must not hang a job.
- **Degradation is explicit.** No ffmpeg → return `None` and log ONCE per process.
  Never score a video on absent evidence, never fabricate a frame.
- **No new media kind.** The model's determination is evidence, not a `KINDS` entry
  (spec §5.2 — the vocabulary is closed).
- **No live AI in tests.** Stub every model call. No spend, ever, from a test run.
- **ffmpeg is NOT installed on the dev VM or in the current image.** Tests needing a
  real binary must SKIP loudly, and the real end-to-end verification runs in-container
  in Task 5.

---

### Task 1: `media_video.py` — availability, frame timing, evidence line

Pure logic and binary detection. Everything here runs on any machine, ffmpeg or not.

**Files:**
- Create: `media_video.py`
- Test: `test_media_video.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `VIDEO_EXTS: set[str]`
  - `available() -> bool`
  - `frame_times(duration: float | None) -> list[float]`
  - `evidence_line(meta: dict | None) -> str`

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""Contract test for media_video's deterministic core (spec §5.1).

Pure logic only — no ffmpeg required, so this runs anywhere.
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

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/gitrepos/ludodex && ./.venv/bin/python test_media_video.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'media_video'`

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
"""Deterministic video sampling — ffprobe metadata + an ffmpeg contact sheet.

A vision model cannot watch a video, and `_thumb_bytes` (PIL) cannot open one, so
every video candidate has always been dropped before the model saw it. This module
turns a video into the two things the model CAN use: a line of measured facts, and a
single image of frames taken from it. See spec §5.

Deterministic tooling produces the evidence; the AI judges it. Same rule as measured
W×H for stills.
"""
import os
import shutil
import subprocess
import sys

VIDEO_EXTS = {"webm", "mp4", "mkv", "mov", "avi", "m4v", "ogv"}

FRAME_COUNT = 5
SKIP_HEAD_S = 3.0          # publisher logos / black frames carry no game information
WINDOW_CAP_S = 120.0       # a 20-minute upload is still judged on its first 2 minutes
PROBE_TIMEOUT_S = 20
SAMPLE_TIMEOUT_S = 60      # total budget per video, across all frames

_WARNED = False


def available():
    """True when BOTH binaries are on PATH. Logs once per process when they are not —
    silence here would look identical to 'this video had no frames'."""
    global _WARNED
    ok = bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))
    if not ok and not _WARNED:
        _WARNED = True
        print("media_video: ffmpeg/ffprobe not found — video frame sampling "
              "unavailable; video candidates will be skipped, not scored",
              file=sys.stderr)
    return ok


def frame_times(duration):
    """FRAME_COUNT timestamps evenly spaced across the sampling window.

    Window is SKIP_HEAD_S .. min(duration, WINDOW_CAP_S). A video shorter than the
    skip is sampled from 0 instead — a 2s clip is all head, and no frames beats
    no sample. Unknown duration (ffprobe gave nothing) falls back to the head
    window, which is valid for any video at least that long."""
    end = WINDOW_CAP_S if duration is None else min(float(duration), WINDOW_CAP_S)
    start = SKIP_HEAD_S
    if duration is not None and float(duration) <= SKIP_HEAD_S:
        start, end = 0.0, max(float(duration), 0.0)
    if end <= start:
        end = start
    if duration is None:
        start, end = SKIP_HEAD_S, min(WINDOW_CAP_S, 30.0)
    step = (end - start) / float(FRAME_COUNT + 1)
    return [round(start + step * (i + 1), 3) for i in range(FRAME_COUNT)]


def evidence_line(meta):
    """One prompt-ready line of MEASURED facts, or an explicit unknown.

    Never invents a value: an unprobeable video says so, exactly as `filler` stays
    NULL rather than being stamped 0 when it cannot be measured."""
    if not meta:
        return "video metadata unknown (probe unavailable)"
    bits = []
    if meta.get("duration"):
        bits.append("%ds" % int(float(meta["duration"])))
    if meta.get("width") and meta.get("height"):
        bits.append("%dx%d" % (int(meta["width"]), int(meta["height"])))
    if meta.get("codec"):
        bits.append(str(meta["codec"]))
    bits.append("audio" if meta.get("has_audio") else "no audio")
    return " · ".join(bits) if bits else "video metadata unknown (probe unavailable)"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/gitrepos/ludodex && ./.venv/bin/python test_media_video.py`
Expected: PASS, `18/18 passed`

- [ ] **Step 5: Commit**

```bash
git add media_video.py test_media_video.py
git commit -m "feat(video): deterministic frame timing + probe evidence line

The sampling window skips the first 3s (publisher logos carry no game
information) and caps at 120s. Unmeasured metadata says so rather than
reporting zeros, mirroring how filler stays NULL when unmeasurable.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: probe + contact sheet with content-addressed caching

**Files:**
- Modify: `media_video.py`
- Test: `test_media_video.py` (extend)

**Interfaces:**
- Consumes: Task 1's `available()`, `frame_times()`, `FRAME_COUNT`, timeouts.
- Produces:
  - `probe(src: str) -> dict | None` — keys `duration, width, height, codec, bitrate, has_audio`
  - `sheet_path(repo: str, ref: str) -> str`
  - `contact_sheet(src: str, repo: str, ref: str) -> bytes | None`

- [ ] **Step 1: Write the failing test**

Append to `test_media_video.py` before the summary print:

```python
    print("7. sheet_path — content-addressed and stable for one ref")
    import tempfile
    repo = tempfile.mkdtemp(prefix="ludodex-video-")
    ref = "https://cdn.akamai.steamstatic.com/steam/apps/2031333/movie_max.webm"
    p1 = media_video.sheet_path(repo, ref)
    p2 = media_video.sheet_path(repo, ref)
    check("stable across calls", p1 == p2)
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
    media_video.subprocess.run = lambda *a, **k: calls.append(a) or real_run(*a, **k)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/gitrepos/ludodex && ./.venv/bin/python test_media_video.py`
Expected: FAIL — `AttributeError: module 'media_video' has no attribute 'sheet_path'`

- [ ] **Step 3: Write minimal implementation**

Append to `media_video.py`:

```python
import hashlib
import io
import json


def sheet_path(repo, ref):
    """Cache location for a video's contact sheet — content-addressed by the video's
    ref, so re-running the wand re-samples nothing. Lives beside the media repo so it
    inherits the same volume (and the same regenerable-bulk expectations)."""
    h = hashlib.sha1((ref or "").encode("utf-8", "replace")).hexdigest()
    return os.path.join(repo, "_videosheets", h[:2], "%s.jpg" % h)


def probe(src):
    """Measured facts about a video, or None when unavailable/unreadable.

    None means UNMEASURED, never 'zero' — callers must degrade, not assume."""
    if not available():
        return None
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(src)]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT_S,
                             check=False)
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout or b"{}")
    except Exception:
        return None
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        return None
    fmt = data.get("format") or {}
    dur = fmt.get("duration") or v.get("duration")
    try:
        dur = float(dur) if dur is not None else None
    except (TypeError, ValueError):
        dur = None
    return {
        "duration": dur,
        "width": v.get("width"),
        "height": v.get("height"),
        "codec": v.get("codec_name"),
        "bitrate": fmt.get("bit_rate"),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def _grab_frame(src, when, timeout):
    """One JPEG at `when` seconds. `-ss` BEFORE `-i` = fast seek: on a seekable HTTP
    source ffmpeg fetches only the byte ranges it needs instead of the whole file."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(when), "-i", str(src),
           "-frames:v", "1", "-f", "image2", "-vcodec", "mjpeg", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except Exception:
        return None
    return out.stdout if out.returncode == 0 and out.stdout else None


def contact_sheet(src, repo, ref, px=320):
    """ONE JPEG tiling FRAME_COUNT frames from the video, cached by `ref`.

    One image per candidate keeps the vision payload the same shape as every other
    kind: N candidates -> N images, comparable side by side in a single call.

    Returns None when ffmpeg is absent or no frame could be read — the caller skips
    the candidate rather than scoring it blind."""
    path = sheet_path(repo, ref)
    if os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            pass
    if not available():
        return None
    meta = probe(src)
    budget = SAMPLE_TIMEOUT_S
    per = max(5, int(budget / float(FRAME_COUNT)))
    frames = []
    for t in frame_times(meta.get("duration") if meta else None):
        raw = _grab_frame(src, t, per)
        if raw:
            frames.append(raw)
    if not frames:
        return None
    try:
        from PIL import Image
        ims = []
        for raw in frames:
            im = Image.open(io.BytesIO(raw))
            im.thumbnail((px, px))
            ims.append(im.convert("RGB"))
        w = sum(i.width for i in ims)
        h = max(i.height for i in ims)
        sheet = Image.new("RGB", (w, h), (0, 0, 0))
        x = 0
        for i in ims:
            sheet.paste(i, (x, 0))
            x += i.width
        buf = io.BytesIO()
        sheet.save(buf, "JPEG", quality=80)
        data = buf.getvalue()
    except Exception:
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    except OSError:
        pass                        # cache is an optimization, not a requirement
    return data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/gitrepos/ludodex && ./.venv/bin/python test_media_video.py`
Expected: PASS, `26/26 passed`

- [ ] **Step 5: Commit**

```bash
git add media_video.py test_media_video.py
git commit -m "feat(video): ffprobe metadata + cached ffmpeg contact sheet

-ss before -i so a 40MB trailer costs a few hundred KB to sample rather
than a full download. Sheets are content-addressed by ref, so a re-run
re-samples nothing. No ffmpeg or no readable frame returns None — the
candidate is skipped, never scored blind.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: route video through `_thumb_bytes` — the single integration point

Both vision consumers build their payload with `_thumb_bytes`. Teaching it video gives
`/api/ai/art-pick` and `_ai_adjudicate_game` video support simultaneously, with neither
knowing video exists (spec §7).

**Files:**
- Modify: `server/app.py` (`_thumb_bytes`, ~line 9431)
- Test: `test_video_thumb_dispatch.py`

**Interfaces:**
- Consumes: `media_video.VIDEO_EXTS`, `media_video.contact_sheet`, `REPO`.
- Produces: `_thumb_bytes` returns `("image/jpeg", bytes)` for a video row.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""_thumb_bytes must build a vision payload for VIDEO rows (spec §5).

Video was structurally excluded: _thumb_bytes opens the asset with PIL, which throws
on a container file, so every video candidate was dropped before the model saw it.

No ffmpeg needed — media_video.contact_sheet is stubbed. Asserts the DISPATCH, which
is the thing that was missing.
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
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/gitrepos/ludodex && ./.venv/bin/python test_video_thumb_dispatch.py`
Expected: FAIL — "returned a payload" (PIL raises on the container, `_thumb_bytes`
returns `None`)

- [ ] **Step 3: Write minimal implementation**

In `server/app.py`, replace the body of `_thumb_bytes` with:

```python
def _thumb_bytes(r, px=256):
    """Downscaled JPEG bytes for a media row (for vision). (mime, bytes) or None.

    VIDEO takes a different route on purpose. PIL cannot open a container, which is
    why video candidates were silently dropped before the model ever saw them; and
    `_asset_local_path`'s URL branch materializes the WHOLE asset, which for a 40 MB
    trailer is a download we don't need. `media_video.contact_sheet` samples frames
    straight off the URL with fast seek and caches one tiled JPEG per video."""
    ext = (r["ext"] or "jpg").split("?")[0].lower()
    if ext in media_video.VIDEO_EXTS:
        src = r["ref"]
        if r["ref_type"] == "file" and os.path.exists(r["ref"]):
            src = r["ref"]
        sheet = media_video.contact_sheet(src, REPO, r["ref"], px=px)
        return ("image/jpeg", sheet) if sheet else None
    p = _asset_local_path(r)
    if not p:
        return None
    try:
        from PIL import Image
        im = Image.open(p)
        im.thumbnail((px, px))
        if im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return ("image/jpeg", buf.getvalue())
    except Exception:
        return None
```

Add the import beside the other pipeline imports near the top of `server/app.py`:

```python
import media_video    # noqa: E402  video frame sampling (vision payload for trailers)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/gitrepos/ludodex && ./.venv/bin/python test_video_thumb_dispatch.py`
Expected: PASS, `6/6 passed`

Then confirm nothing regressed for stills:

Run: `cd ~/gitrepos/ludodex && ./.venv/bin/python -m py_compile server/app.py && ./.venv/bin/python test_collection_apply_guard.py`
Expected: `8/8 passed`

- [ ] **Step 5: Commit**

```bash
git add server/app.py test_video_thumb_dispatch.py
git commit -m "feat(video): build a vision payload for video rows

_thumb_bytes opened every candidate with PIL, which throws on a container,
so video was dropped before the model saw it — a structural exclusion, not
a missing button. Video now routes to a sampled contact sheet, and because
_thumb_bytes is the single payload builder, /api/ai/art-pick and
_ai_adjudicate_game both gain video without either knowing it exists.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: hand the probe evidence to the model

The sheet shows the model what the video LOOKS like; the probe tells it what the model
cannot see from five stills — length, true resolution, whether it even has audio. Per
the settled principle, deterministic data feeds the prompt.

**Files:**
- Modify: `server/ai.py` (`pick_art`)
- Modify: `server/app.py` (`art_pick`, ~line 9466)
- Test: `test_video_evidence_prompt.py`

**Interfaces:**
- Consumes: `media_video.probe`, `media_video.evidence_line`.
- Produces: `ai.pick_art(title, kind, thumbs, notes=None, ...)` — `notes` is an
  optional list of per-candidate strings, same length/order as `thumbs`.

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
"""Measured facts must reach the prompt, not just the pixels (spec §2①, §5.2).

A vision model sees only downscaled frames, so length/true-resolution/audio are
invisible to it — exactly the properties the current prompt asks it to judge. The
model call is stubbed; this asserts the EVIDENCE reaches it.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from server import ai

    print("1. notes are rendered into the prompt, per candidate")
    captured = {}

    # `_complete_vision(provider, key, model, system, user, images)` — six positional
    # args, no **kwargs. `pick_art` has NO `key` parameter; it derives one via
    # `_resolve(provider, model)`, which would go looking for a real API key, so stub
    # that too. Getting this wrong means the test fails on plumbing, not on behaviour.
    def fake_vision(provider, key, model, system, user, images):
        captured["system"] = system
        captured["user"] = user
        return '{"index": 1, "reason": "stub"}'

    real_v, real_r = ai._complete_vision, ai._resolve
    ai._complete_vision = fake_vision
    ai._resolve = lambda p, m: ("stub", "stub-key", "stub-model")
    try:
        res = ai.pick_art("Doom", "video",
                          [("image/jpeg", b"a"), ("image/jpeg", b"b")],
                          notes=["142s · 1920x1080 · vp9 · audio",
                                 "12s · 640x360 · h264 · no audio"])
    finally:
        ai._complete_vision, ai._resolve = real_v, real_r
    blob = (captured.get("system") or "") + (captured.get("user") or "")
    check("first candidate's measured facts present", "1920x1080" in blob)
    check("second candidate's measured facts present", "640x360" in blob)
    check("duration present", "142s" in blob)
    check("still returns the model's pick", res["index"] == 0)

    print("2. a video is asked what it IS, not just which looks nicest (spec §5.2)")
    check("asked to identify the video's type",
          any(w in blob.lower() for w in ("trailer", "gameplay", "teaser")))
    check("asked to confirm it is the right game", "right game" in blob.lower()
          or "correct game" in blob.lower())

    print("3. notes=None on a still leaves the existing prompt unchanged")
    captured.clear()
    real_v, real_r = ai._complete_vision, ai._resolve
    ai._complete_vision = fake_vision
    ai._resolve = lambda p, m: ("stub", "stub-key", "stub-model")
    try:
        ai.pick_art("Doom", "cover", [("image/jpeg", b"a"), ("image/jpeg", b"b")])
    finally:
        ai._complete_vision, ai._resolve = real_v, real_r
    blob = (captured.get("system") or "") + (captured.get("user") or "")
    check("no evidence block when there is no evidence",
          "measured facts" not in blob.lower())
    check("no video clause on a cover", "trailer" not in blob.lower())

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/gitrepos/ludodex && ./.venv/bin/python test_video_evidence_prompt.py`
Expected: FAIL — `TypeError: pick_art() got an unexpected keyword argument 'notes'`
(If it instead fails inside `_resolve`, the stub above is not in place — fix that first;
a test that fails on plumbing proves nothing about the behaviour.)

- [ ] **Step 3: Write minimal implementation**

In `server/ai.py`, change the signature to
`def pick_art(title, kind, images, provider=None, model=None, language=None, notes=None):`
and extend `instr` — the USER message, built a few lines above the existing
`_complete_vision` call — immediately before that call:

```python
    # A video candidate is a CONTACT SHEET of frames, not a poster, so the stock
    # "right shape for this kind" scoring is meaningless for it. Ask the questions
    # that actually apply (spec §5.2): is it this game, what IS it, which to feature.
    if kind == "video":
        instr = ("Each image is a CONTACT SHEET: five frames sampled across one video, "
                 "left to right. Judge: (1) is it the RIGHT GAME; (2) what IS the video "
                 "— trailer, gameplay, teaser, or cutscene; (3) which is best to FEATURE "
                 "for this game. Prefer real gameplay or an official trailer over a "
                 "logo-only teaser. Ignore that the frames are tiled — that is how they "
                 "were sampled, not how the video looks.")
        if language:
            instr += (" Prefer a video whose visible text is %s when quality is "
                      "comparable." % language)
    # MEASURED facts the model cannot see. It is shown downscaled thumbnails, so
    # resolution — which the prompt asks it to rank on — is invisible to it, and for a
    # video, duration and audio are invisible entirely. Deterministic data feeds the
    # judgment instead of the model guessing at it.
    if notes:
        instr += ("\n\nMeasured facts per candidate (trust these over your impression "
                  "of the image):\n"
                  + "\n".join("Image %d: %s" % (i + 1, n) for i, n in enumerate(notes)))
```

In `server/app.py`'s `art_pick`, build `notes` for video candidates and pass them:

```python
    notes = None
    if kind == "video":
        notes = []
        for c in cands:
            row = next((r for r in rows if r["id"] == c["id"]), None)
            notes.append(media_video.evidence_line(
                media_video.probe(row["ref"]) if row else None))
    try:
        res = ai.pick_art(title, kind, [c["thumb"] for c in cands],
                          notes=notes,
                          provider=ai.provider_for_area("art"),
                          model=ai.model_for_area("art"),
                          language=config.get("media_language") or None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/gitrepos/ludodex && ./.venv/bin/python test_video_evidence_prompt.py`
Expected: PASS, `8/8 passed`

- [ ] **Step 5: Commit**

```bash
git add server/ai.py server/app.py test_video_evidence_prompt.py
git commit -m "feat(video): feed measured facts to the vision prompt

The model sees only downscaled frames, so resolution — which the prompt
already asks it to rank on — has never been observable to it, and for video
neither is duration or audio. notes= carries per-candidate measured facts;
omitted, the prompt is byte-identical to before.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: ship ffmpeg and verify against real trailers

Everything above is inert until the binaries exist. This task is where the feature
actually becomes true, and it is verified against live Steam trailers, not fixtures.

**Files:**
- Modify: `Dockerfile` (~line 37, the existing `apt-get install` list)

- [ ] **Step 1: Add the binaries**

Add `ffmpeg` to the existing `apt-get install -y --no-install-recommends` list in
`Dockerfile`. `ffmpeg` provides `ffprobe`; do not add it separately.

- [ ] **Step 2: Build and record the size delta**

```bash
docker images ludodex:latest --format '{{.Size}}'   # BEFORE, record it
cd ~/gitrepos/ludodex/web && pnpm build
rsync -a --delete --exclude .git --exclude web/node_modules --exclude web/dist \
  --exclude __pycache__ --exclude .venv --exclude media --exclude '*.sqlite' \
  ./ <docker-host>:<build-dir>/
ssh <docker-host> 'cd <build-dir> && docker build -t ludodex:latest .; echo "RC=$?"'
```

Expected: `RC=0`. Do NOT pipe the build — a pipeline's exit code is the last command's,
so `&&` would fire a redeploy on a stale image. Record the new size; state the delta
rather than letting a 368 MB image quietly grow.

- [ ] **Step 3: Gate on the busy check, then redeploy**

```bash
ssh <docker-host> 'BUSY=$(docker exec ludodex sh -c "cat /proc/*/cmdline 2>/dev/null | tr \"\\0\" \" \"" | tr "|" "\n" | grep -E "build_library|media_choose|_owned|igdb_enrich" | grep -v grep); if [ -n "$BUSY" ]; then echo "BUSY - ABORT"; exit 9; fi; bash <redeploy-script>'
```

The check must EXIT on busy, not print and continue — redeploying mid-rebuild has
corrupted `game-library.sqlite` before.

- [ ] **Step 4: Verify against a real trailer**

```bash
ssh <docker-host> 'docker exec ludodex sh -c "which ffmpeg ffprobe"'
ssh <docker-host> 'docker inspect ludodex --format "{{range .HostConfig.Binds}}{{.}}
{{end}}"'   # /biggins MUST still be listed
```

Then sample a live Steam trailer in-container:

```bash
ssh <docker-host> 'docker exec -i ludodex python3 -' <<'PY'
import sqlite3, sys, time
sys.path.insert(0, "/app")
import media_video, media_choose
c = sqlite3.connect("file:/data/media-index.sqlite?mode=ro", uri=True)
r = c.execute("SELECT ref FROM media WHERE kind='video' LIMIT 1").fetchone()
print("available:", media_video.available())
t0 = time.time()
meta = media_video.probe(r[0])
print("probe:", meta, "in %.1fs" % (time.time() - t0))
t0 = time.time()
sheet = media_video.contact_sheet(r[0], media_choose.repo_dir(), r[0])
print("sheet bytes:", len(sheet) if sheet else None, "in %.1fs" % (time.time() - t0))
t0 = time.time()
media_video.contact_sheet(r[0], media_choose.repo_dir(), r[0])
print("cached re-run in %.2fs (must be ~0)" % (time.time() - t0))
PY
```

Expected: `available: True`; probe returns duration/resolution/codec; a sheet of
non-trivial size; the cached re-run near-instant. If sampling a 40 MB trailer takes
more than a few seconds, fast seek is not working — check `-ss` precedes `-i`.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile
git commit -m "build(video): add ffmpeg for frame sampling

ffprobe ships with it. Records the image size delta in the PR/commit body:
BEFORE <x> MB -> AFTER <y> MB.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Verification of the whole feature

Run every test that touches this surface:

```bash
cd ~/gitrepos/ludodex
for t in test_media_video test_video_thumb_dispatch test_video_evidence_prompt \
         test_collection_apply_guard test_member_title_collapse \
         test_materialize_members verify_collection_detect; do
  printf "%-34s " "$t"; ./.venv/bin/python $t.py >/dev/null 2>&1 && echo PASS || echo FAIL
done
```

All must PASS. Then, in the UI, open a game with a trailer, open the `video` category,
and run the AI pick — it should now return a recommendation with a reason instead of
"Only one candidate reachable on this host."

## Known limitations, stated not hidden

- **IGDB videos are YouTube ids**, not direct files. They cannot be probed or sampled
  here. They must be excluded from the candidate set rather than scored on absent
  evidence — currently moot (the live catalog holds none), but true the moment IGDB
  video import is added.
- **Sampling is bandwidth, not tokens.** The AI-spend rules are unaffected; the caps in
  Task 2 exist so an unreachable source cannot hang a job.
- The wand UI (spec §1-§4) is NOT part of this plan. This makes video reach parity in
  the EXISTING pick paths; the wand can then treat video like any other kind.
