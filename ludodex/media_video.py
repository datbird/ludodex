#!/usr/bin/env python3
"""Deterministic video sampling — ffprobe metadata + an ffmpeg contact sheet.

A vision model cannot watch a video, and `_thumb_bytes` (PIL) cannot open one, so every
video candidate has always been dropped before the model saw it — a structural
exclusion, not a missing feature. This module turns a video into the two things a model
CAN use: a line of measured facts, and a single image of frames taken from it.
See `docs/superpowers/specs/2026-08-01-media-wand-design.md` §5.

Deterministic tooling produces the evidence; the AI judges it — the same rule that puts
measured W×H in front of the model for stills instead of letting it guess from a
downscaled thumbnail.
"""
import hashlib
import io
import json
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
    silence here would look identical to "this video had no frames"."""
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

    The window is SKIP_HEAD_S .. min(duration, WINDOW_CAP_S). Two degenerate cases are
    handled deliberately rather than left to produce nonsense:
      - a video SHORTER than the skip is all head, so it is sampled from 0 — some
        frames beat no sample;
      - an UNKNOWN duration (ffprobe gave nothing) falls back to a short head window,
        which is valid for any video at least that long, instead of seeking past the
        end of a clip that might be 10 seconds."""
    if duration is None:
        start, end = SKIP_HEAD_S, 30.0
    else:
        d = max(float(duration), 0.0)
        if d <= SKIP_HEAD_S:
            start, end = 0.0, d
        else:
            start, end = SKIP_HEAD_S, min(d, WINDOW_CAP_S)
    if end < start:
        end = start
    step = (end - start) / float(FRAME_COUNT + 1)
    return [round(start + step * (i + 1), 3) for i in range(FRAME_COUNT)]


def evidence_line(meta):
    """One prompt-ready line of MEASURED facts, or an explicit unknown.

    Never invents a value: an unprobeable video says so, exactly as `filler` stays NULL
    rather than being stamped 0 when it cannot be measured. A fabricated 0x0 would be
    read by the model as a real measurement."""
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
    return " · ".join(bits)


def sheet_path(repo, ref):
    """Cache location for a video's contact sheet — content-addressed by the video's
    ref, so a re-run re-samples nothing. Lives beside the media repo so it inherits the
    same volume, and the same regenerable-bulk expectations."""
    h = hashlib.sha1((ref or "").encode("utf-8", "replace")).hexdigest()
    return os.path.join(repo, "_videosheets", h[:2], "%s.jpg" % h)


def probe(src):
    """Measured facts about a video, or None when unavailable/unreadable.

    None means UNMEASURED, never "zero" — callers must degrade, not assume."""
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
    """One JPEG at `when` seconds, or None.

    `-ss` BEFORE `-i` is deliberate: that is an input-level fast seek, so on a seekable
    HTTP source ffmpeg fetches only the byte ranges it needs. Placed after `-i` it would
    decode from the start — turning a few hundred KB into a full 40 MB download per
    frame."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(when), "-i", str(src),
           "-frames:v", "1", "-f", "image2", "-vcodec", "mjpeg", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except Exception:
        return None
    return out.stdout if out.returncode == 0 and out.stdout else None


def _whole_jpeg(data):
    """Is this a COMPLETE JPEG? Truncation is the failure being detected.

    A JPEG starts SOI (FF D8) and ends EOI (FF D9). A file cut short by a crash, a full
    disk or a killed job keeps the start and loses the end, which is precisely the
    half-sheet `os.path.exists` cannot tell apart from a good one. Cheap, and it needs
    no decoder: this runs on every cache hit."""
    return (len(data) > 4 and data[:2] == b"\xff\xd8"
            and data[-2:] == b"\xff\xd9")


def contact_sheet(src, repo, ref, px=320):
    """ONE JPEG tiling FRAME_COUNT frames from the video, cached by `ref`.

    One image per candidate keeps the vision payload the same shape as every other
    kind: N candidates -> N images, comparable side by side in a single call.

    Returns None when ffmpeg is absent or no frame could be read — the caller skips the
    candidate rather than scoring it blind."""
    path = sheet_path(repo, ref)
    # The write below is ATOMIC, so nothing this code writes can be half a sheet. That
    # only covers sheets written from now on: the cache still holds whatever a crash, a
    # full disk or a killed job left there BEFORE the write was fixed, and existence
    # alone cannot tell those apart. So the bytes are checked, and a sheet that is not
    # whole is deleted rather than handed to the vision model and scored — which is what
    # happened on every call, forever, once one was written.
    if os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                cached = fh.read()
            if _whole_jpeg(cached):
                return cached
            os.unlink(path)         # poisoned; the next pass re-samples it
        except OSError:
            pass
    if not available():
        return None
    meta = probe(src)
    per = max(5, int(SAMPLE_TIMEOUT_S / float(FRAME_COUNT)))
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
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        # Write private, then rename: a reader must see a whole sheet or no sheet, never
        # the middle of one. Writing `path` directly is what made a killed job poison the
        # cache permanently.
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(path) + ".",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError:
        pass                        # the cache is an optimization, not a requirement
    return data
