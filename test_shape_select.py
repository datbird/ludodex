#!/usr/bin/env python3
"""Shape verification in media_choose.select() — offline, no creds, no network.

Guards the defect that selection never examined the image: it ranked on provider
priority then row id, so a landscape header could win a `cover` slot by being indexed
first, and a correct pick was luck rather than judgment.

The live Ys entry is deliberately NOT a fixture here — it passes today by accident, so
asserting on it would green-light a still-blind selector. These use ringers instead.

Run:  LUDODEX_DATA=$(mktemp -d) python3 test_shape_select.py
"""
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)

# MUST come before any ludodex import: they resolve their paths at import time. This
# used to be `os.environ.setdefault(...)`, which KEEPS an inherited value — so inside
# the container, where LUDODEX_DATA=/data is already set, the temp dir was ignored and
# `_con()`'s `DELETE FROM media` below ran against the live 66,280-row media index. It
# did, on 2026-08-02. isolate() assigns, and refuses outright if the result is live.
import test_support                             # noqa: E402
test_support.isolate("ludodex-shape-")

import media                                    # noqa: E402
import media_index                              # noqa: E402
import media_choose                             # noqa: E402

FAIL = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAIL.append(label)


def _con():
    con = media_index.index_con()
    con.row_factory = __import__("sqlite3").Row   # select() reads rows by column name
    con.execute("DELETE FROM media")
    return con


def _put(con, nk, kind, provider, ref, width=None, height=None, matched=1, filler=None):
    con.execute(
        "INSERT INTO media(norm_key,system,kind,provider,ref_type,ref,ext,matched,"
        "width,height,filler,chosen) VALUES(?,?,?,?,'url',?,'jpg',?,?,?,?,0)",
        (nk, "", kind, provider, ref, matched, width, height, filler))
    return con.execute("SELECT last_insert_rowid()").fetchone()[0]


def _chosen(con, nk, kind):
    r = con.execute("SELECT ref FROM media WHERE norm_key=? AND kind=? AND chosen=1",
                    (nk, kind)).fetchone()
    return r[0] if r else None


print("shape helpers")
check(media.shape_ok("cover", 460, 215) is False, "landscape rejected for cover")
check(media.shape_ok("cover", 600, 900) is True, "portrait accepted for cover")
check(media.shape_ok("cover", None, None) is True, "unknown never penalised")
check(media.shape_ok("logo", 460, 215) is True, "kinds without a fixed shape untouched")
check(media.shape_ok("hero", 600, 900) is False, "portrait rejected for hero")
check(media.shape_ok("cover", 500, 500) is True, "square tolerated")
check(media.derived_dims("https://x/library_600x900.jpg") == (600, 900), "dims from name")
check(media.derived_dims("https://x/header.jpg") == (460, 215), "dims from known leaf")
check(media.derived_dims("https://x/whatever.png") == (None, None), "unknown -> none")

print("\nselect(): landscape ringer must never win a cover slot")
con = _con()
# The ringer is from the HIGHEST-priority provider and has the LOWEST id — it wins on
# every pre-existing tie-break. Only a shape test can reject it.
top = media.priority("cover")[0]
_put(con, "ringer", "cover", top, "https://x/header.jpg", 460, 215)
_put(con, "ringer", "cover", media.priority("cover")[-1], "https://x/good.jpg", 600, 900)
media_choose.select(con)
check(_chosen(con, "ringer", "cover") == "https://x/good.jpg",
      "correctly-oriented candidate chosen over top-priority landscape")

print("\nselect(): shape derived from the URL alone (no measured dims)")
con = _con()
_put(con, "derived", "cover", top, "https://x/header.jpg")          # no width/height
_put(con, "derived", "cover", top, "https://x/library_600x900.jpg")
media_choose.select(con)
check(_chosen(con, "derived", "cover") == "https://x/library_600x900.jpg",
      "URL-derived orientation rejects the header without measuring")

print("\nselect(): provider priority still governs among valid shapes")
con = _con()
_put(con, "prio", "cover", media.priority("cover")[-1], "https://x/low.jpg", 600, 900)
_put(con, "prio", "cover", top, "https://x/high.jpg", 600, 900)
media_choose.select(con)
check(_chosen(con, "prio", "cover") == "https://x/high.jpg",
      "higher-priority provider wins when both shapes are valid")

print("\nselect(): measured resolution breaks ties within one provider")
con = _con()
_put(con, "res", "cover", top, "https://x/small.jpg", 300, 450)
_put(con, "res", "cover", top, "https://x/big.jpg", 600, 900)
media_choose.select(con)
check(_chosen(con, "res", "cover") == "https://x/big.jpg",
      "larger measured image wins (the Ys case, by judgment not luck)")

print("\nselect(): a MEASURED wrong shape is disqualified, not merely ranked last")
# This case used to assert the opposite — that a sole 460x215 header still filled the
# cover slot rather than starve it. That was wrong in the way the user actually sees:
# an EMPTY slot falls back cleanly to the monogram card, whereas a wrong-shaped one is
# displayed stretched, as if it were correct. Ranking it last still elected it whenever
# nothing better existed, which is exactly how a landscape grid became a cover.
# shape_ok() returns True for UNKNOWN dimensions, so this only excludes assets we have
# actually looked at and know are wrong.
con = _con()
_put(con, "allbad", "cover", top, "https://x/header.jpg", 460, 215)
media_choose.select(con)
check(_chosen(con, "allbad", "cover") is None,
      "a known-wrong shape is left unchosen rather than stretched into the slot")

print("\nselect(): the IMAGE outranks the provider, with unknown size in the middle")
# Also superseded deliberately. The user's ranking policy is tier 1 = deterministic
# image facts (ratio, resolution), tier 2 = provider. So a measured 600x900 beats an
# unmeasured candidate from a higher-priority provider — the live case that forced this
# had IGDB's 264x352 thumbnail outranking a SteamGridDB cover five times its area purely
# on provider order. Resolution is BANDED rather than raw, so unmeasured lands in the
# middle: it loses to a known-large and still beats a known-small.
con = _con()
_put(con, "unk", "cover", top, "https://x/unknown.png")             # no dims at all
_put(con, "unk", "cover", media.priority("cover")[-1], "https://x/known.jpg", 600, 900)
media_choose.select(con)
check(_chosen(con, "unk", "cover") == "https://x/known.jpg",
      "a measured large cover beats an unmeasured one from a better provider")

con = _con()
_put(con, "unk2", "cover", media.priority("cover")[-1], "https://x/unknown.png")
_put(con, "unk2", "cover", top, "https://x/small.jpg", 264, 352)    # measured, small
media_choose.select(con)
check(_chosen(con, "unk2", "cover") == "https://x/unknown.png",
      "but an unmeasured candidate still beats a measured SMALL one")

print("\nselect(): provider priority decides INSIDE a resolution band")
con = _con()
_put(con, "band", "cover", media.priority("cover")[-1], "https://x/lowprio.jpg", 600, 900)
_put(con, "band", "cover", top, "https://x/topprio.jpg", 620, 880)  # same band, smaller
media_choose.select(con)
check(_chosen(con, "band", "cover") == "https://x/topprio.jpg",
      "the tier-2 signal still governs once the images are comparable")

print("\nselect(): a confirmed letterboxed paste loses to authored art")
con = _con()
# The filler is Steam's own (top priority) AND larger — it wins on every other term.
_put(con, "filler", "cover", top, "https://x/portrait.png", 600, 900, filler=1)
_put(con, "filler", "cover", media.priority("cover")[-1], "https://x/real.jpg", 264, 352)
media_choose.select(con)
check(_chosen(con, "filler", "cover") == "https://x/real.jpg",
      "authored cover beats a bigger, higher-priority filler")

print("\nselect(): an UNMEASURED candidate is never assumed to be filler")
con = _con()
_put(con, "unmeas", "cover", top, "https://x/portrait.png", 600, 900)      # filler NULL
_put(con, "unmeas", "cover", media.priority("cover")[-1], "https://x/real.jpg", 264, 352)
media_choose.select(con)
check(_chosen(con, "unmeas", "cover") == "https://x/portrait.png",
      "NULL filler stays neutral; provider priority still decides")

print("\nselect(): a sole filler is still served (no blank cards)")
con = _con()
_put(con, "onlyfill", "cover", top, "https://x/portrait.png", 600, 900, filler=1)
media_choose.select(con)
check(_chosen(con, "onlyfill", "cover") == "https://x/portrait.png",
      "demotion never starves a game of its only cover")

print("\nlooks_padded(): tri-state discipline")
check(media.looks_padded("/nonexistent/nope.png") is False,
      "unreadable file is False, never an exception")

print("\n%s" % ("ALL PASS" if not FAIL else "FAILURES: %d" % len(FAIL)))
for f in FAIL:
    print("  - " + f)
sys.exit(1 if FAIL else 0)
