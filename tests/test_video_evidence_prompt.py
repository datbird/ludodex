#!/usr/bin/env python3
"""Measured facts must reach the prompt, not just the pixels (spec §2①, §5.2).

A vision model sees only downscaled frames, so resolution — which the art prompt
already asks it to rank on — has never been observable to it, and for a video neither
is duration or audio. And a video candidate is a CONTACT SHEET, so the stock
"right shape for this kind" scoring is meaningless for it; §5.2 asks different
questions entirely.

The model call is stubbed. This asserts the EVIDENCE and the QUESTIONS reach it.
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
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from server import ai

    captured = {}

    # `_complete_vision(provider, key, model, system, user, images)` — six positional
    # args, no **kwargs. `pick_art` has NO `key` parameter; it derives one via
    # `_resolve(provider, model)`, which would go looking for a real API key, so stub
    # that too. Getting this wrong means the test fails on plumbing, not behaviour.
    def fake_vision(provider, key, model, system, user, images):
        captured["system"] = system
        captured["user"] = user
        return '{"index": 1, "reason": "stub"}'

    def run(kind, **kw):
        captured.clear()
        real_v, real_r = ai._complete_vision, ai._resolve
        ai._complete_vision = fake_vision
        ai._resolve = lambda p, m: ("stub", "stub-key", "stub-model")
        try:
            return ai.pick_art("Doom", kind,
                               [("image/jpeg", b"a"), ("image/jpeg", b"b")], **kw)
        finally:
            ai._complete_vision, ai._resolve = real_v, real_r

    print("1. notes are rendered into the prompt, per candidate")
    res = run("video", notes=["142s · 1920x1080 · vp9 · audio",
                              "12s · 640x360 · h264 · no audio"])
    blob = (captured.get("system") or "") + (captured.get("user") or "")
    check("first candidate's measured facts present", "1920x1080" in blob)
    check("second candidate's measured facts present", "640x360" in blob)
    check("duration present", "142s" in blob)
    check("still returns the model's pick", res["index"] == 0)

    print("2. a video is asked what it IS, not just which looks nicest (spec §5.2)")
    check("asked to identify the video's type",
          any(w in blob.lower() for w in ("trailer", "gameplay", "teaser")))
    check("asked to confirm it is the right game",
          "right game" in blob.lower() or "correct game" in blob.lower())
    check("told the frames are tiled, so it doesn't judge the tiling",
          "contact sheet" in blob.lower())

    print("3. a still with no notes leaves the existing prompt unchanged")
    run("cover")
    blob = (captured.get("system") or "") + (captured.get("user") or "")
    check("no evidence block when there is no evidence",
          "measured facts" not in blob.lower())
    check("no video clause on a cover", "trailer" not in blob.lower())

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
