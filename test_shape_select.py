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
import tempfile

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
os.environ.setdefault("LUDODEX_DATA", tempfile.mkdtemp(prefix="ludodex-shape-"))

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


def _put(con, nk, kind, provider, ref, width=None, height=None, matched=1):
    con.execute(
        "INSERT INTO media(norm_key,system,kind,provider,ref_type,ref,ext,matched,"
        "width,height,chosen) VALUES(?,?,?,?,'url',?,'jpg',?,?,?,0)",
        (nk, "", kind, provider, ref, matched, width, height))
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

print("\nselect(): an all-wrong set still yields a pick (no starvation)")
con = _con()
_put(con, "allbad", "cover", top, "https://x/header.jpg", 460, 215)
media_choose.select(con)
check(_chosen(con, "allbad", "cover") == "https://x/header.jpg",
      "sole candidate still chosen when nothing better exists")

print("\nselect(): unmeasured must not lose to measured on that basis alone")
con = _con()
_put(con, "unk", "cover", top, "https://x/unknown.png")             # no dims at all
_put(con, "unk", "cover", media.priority("cover")[-1], "https://x/known.jpg", 600, 900)
media_choose.select(con)
check(_chosen(con, "unk", "cover") == "https://x/unknown.png",
      "unknown dims stay neutral; provider priority still decides")

print("\n%s" % ("ALL PASS" if not FAIL else "FAILURES: %d" % len(FAIL)))
for f in FAIL:
    print("  - " + f)
sys.exit(1 if FAIL else 0)
