#!/usr/bin/env python3
"""A ranking term that is the same for every candidate has decided nothing.

`filler` demotes a letterboxed paste beneath authored art, which is right whenever the
two can be told apart. When they cannot — every candidate in a bucket flagged — the term
is CONSTANT, and a constant term must not silently hand the decision to an unrelated one.
It did: ranking fell through to the resolution band, and Steam's 600x900 auto-portrait
beat a 300x450 authored cover on size alone.

Live cases, both real, both failing this way for different reasons:
  * Arx Fatalis — a bright wordmark inflated a peak-relative threshold (fixed at the
    threshold, 2026-08-07)
  * Insurgency — hazy low-contrast art genuinely carries almost no high-frequency
    detail, so the authored covers ARE flat by every absolute measure:
        paste     [2.6, 8.4, 13.5, 22.7, 2.6, 1.1, 1.4, 1.5, 1.3]
        authored  [6.8, 4.5,  7.9, 49.3, 4.9, 4.1, 4.0, 3.3, 3.6]
        authored  [5.5, 2.8, 18.0, 53.3, 4.2, 2.9, 3.0, 2.2, 2.4]
    No threshold separates those from a genuine paste (Shadowrun Dragonfall's padding
    measures 2.2..4.1). Chasing it with a third heuristic rewrite trades one
    false-positive class for the next.

So the fix is structural, not another heuristic: when the flag is constant across the
bucket it is dropped, and the tie breaks on DETAIL DENSITY — the median band energy,
which is high for art that carries detail throughout and low for a blurred paste —
ranked above the resolution band. Median, not mean, so one bright wordmark cannot move
it, which is the mistake the threshold made.

  detail density   Insurgency          Arx Fatalis
  paste            2.6                 1.6
  authored         4.5  <- wins        6.8  <- wins
  authored         3.0                 7.1

Scoped by construction: it only fires where the answer is already arbitrary, so it
cannot regress a bucket the flag still discriminates.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-consttie-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def add(con, nk, provider, ref, w, h, filler, detail, ext="jpg"):
    con.execute("INSERT INTO media(norm_key,system,kind,provider,ref_type,ref,ext,"
                "sha1,width,height,chosen,matched,filler,detail) "
                "VALUES(?,NULL,'cover',?,'url',?,?,?,?,?,0,1,?,?)",
                (nk, provider, ref, ext, ref, w, h, filler, detail))


def chosen(con, nk):
    r = con.execute("SELECT provider, width, height FROM media WHERE norm_key=? "
                    "AND chosen=1", (nk,)).fetchone()
    return (r[0], r[1], r[2]) if r else None


def main():
    import media_index
    media_index.index_con().close()
    import media_choose

    con = media_choose.con_index()

    # Insurgency: every candidate flagged -> the flag decides nothing
    add(con, "insurgency", "steam", "http://s/portrait.png", 600, 900, 1, 2.6)
    add(con, "insurgency", "steam", "http://s/library_600x900.jpg", 300, 450, 1, 4.5)
    add(con, "insurgency", "igdb", "http://i/co43mw.jpg", 264, 352, 1, 3.0)

    # a bucket where the flag still discriminates -> unchanged behaviour
    add(con, "normal game", "steam", "http://s/pad.png", 600, 900, 1, 2.0)
    add(con, "normal game", "igdb", "http://i/art.jpg", 264, 352, 0, 3.0)

    # nothing flagged: the ordinary case, biggest still wins
    add(con, "clean game", "steam", "http://s/big.jpg", 600, 900, 0, 4.0)
    add(con, "clean game", "igdb", "http://i/small.jpg", 264, 352, 0, 9.0)
    con.commit()

    media_choose.select(con, kinds=["cover"])

    check("a constant flag falls through to detail density, not size",
          chosen(con, "insurgency") == ("steam", 300, 450))
    check("a discriminating flag still demotes the paste",
          chosen(con, "normal game") == ("igdb", 264, 352))
    check("with nothing flagged, resolution still decides",
          chosen(con, "clean game") == ("steam", 600, 900))

    # an unmeasured detail must not win by being unknown
    con.execute("DELETE FROM media")
    add(con, "unmeasured", "steam", "http://s/p.png", 600, 900, 1, None)
    add(con, "unmeasured", "igdb", "http://i/a.jpg", 264, 352, 1, 5.0)
    con.commit()
    media_choose.select(con, kinds=["cover"])
    check("a measured detail beats an unmeasured one when the flag is constant",
          chosen(con, "unmeasured") == ("igdb", 264, 352))
    con.close()

    print("\n  %d/%d passed" % (sum(1 for _, c in PASS if c), len(PASS)))


if __name__ == "__main__":
    main()
