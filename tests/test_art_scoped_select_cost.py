#!/usr/bin/env python3
"""A one-game re-rank must not read the whole library.

`select(only=[nk])` exists because measurement is LAZY: an asset's dimensions and its
filler verdict are stamped when it is first served, which is AFTER the selection that
ranked it. So the serve path re-ranks that one game, and the FIRST serve of any URL asset
in the library goes down this road.

Two of the queries in that path were deliberately unscoped, and rightly so — whether a
frame is a themed pack's plate is a property of the WHOLE corpus, and scoping the COUNT
would let a one-game re-rank see its frame once, conclude "not a template", and hand the
pack back the slot it just lost. That reasoning is about the COUNT.

It is not about the LIST. Asking "which of the frames MY candidates hold are templates?"
counts over the whole table exactly as before and answers exactly the same question — it
simply stops grouping the entire index to produce answers about assets nobody asked
about. A game with four candidates has at most four frames; the old query built a group
for every frame in the library, twice, on every first serve.

Measured in SQLite VM steps rather than asserted from the source, because the claim IS
about cost.

Offline. No network.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-scopedcost-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import media                                                   # noqa: E402
import media_choose                                            # noqa: E402
import media_index                                             # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


GAMES = 1500


def seed(con):
    con.execute("DELETE FROM media")
    rows = []
    for i in range(GAMES):
        nk = "game %04d" % i
        # every game holds two covers with a frame of its own — no packs among these
        rows.append((nk, "cover", "igdb", "https://x/%d-a.jpg" % i, "f%05d" % i, 600, 900))
        rows.append((nk, "cover", "steam", "https://x/%d-b.jpg" % i,
                     "f%05d" % (i + GAMES), 300, 450))
    # a real themed pack: one frame shared by three DIFFERENT games, which must still be
    # detected from inside a re-rank scoped to just one of them
    for j, nk in enumerate(("packed one", "packed two", "packed three")):
        rows.append((nk, "cover", "steamgriddb", "https://x/pack-%d.jpg" % j,
                     "PACKFRAME", 600, 900))
        rows.append((nk, "cover", "igdb", "https://x/own-%d.jpg" % j,
                     "own%d" % j, 300, 450))
    con.executemany(
        "INSERT INTO media(norm_key,system,kind,provider,ref_type,ref,ext,frame,"
        "width,height,matched,indexed_at) VALUES(?,'',?,?,'url',?,'jpg',?,?,?,1,0)",
        [(nk, k, p, r, f, w, h) for nk, k, p, r, f, w, h in rows])
    con.commit()


def steps(con, fn):
    """SQLite VM steps burned by `fn`, in thousands."""
    n = [0]

    def tick():
        n[0] += 1
        return 0
    con.set_progress_handler(tick, 1000)
    try:
        fn()
    finally:
        con.set_progress_handler(None, 0)
    return n[0]


def main():
    con = media_index.index_con()
    import sqlite3
    con.row_factory = sqlite3.Row
    seed(con)
    check("the pack really is one (%d games share the frame)" % media.TEMPLATE_MIN_GAMES,
          media.TEMPLATE_MIN_GAMES <= 3)

    print("1. a scoped re-rank still sees the pack for what it is")
    media_choose.select(con, only=["packed one"], kinds=["cover"])
    won = con.execute("SELECT ref FROM media WHERE norm_key='packed one' AND chosen=1"
                      ).fetchone()[0]
    check("the pack plate is demoted even though only one game was re-ranked",
          "own-" in won)

    print("2. and it does not pay for the whole library to find out")
    full = steps(con, lambda: media_choose.select(con, kinds=["cover"]))
    one = steps(con, lambda: media_choose.select(con, only=["packed one"],
                                                 kinds=["cover"]))
    print("      full pass %dk steps   one game %dk steps" % (full, one))
    check("a full pass is real work", full > 20)
    check("one game costs a small fraction of it (%dk vs %dk)" % (one, full),
          one * 5 < full)

    print("3. the answer is identical either way")
    media_choose.select(con, kinds=["cover"])
    a = {r[0]: r[1] for r in con.execute(
        "SELECT norm_key, ref FROM media WHERE chosen=1")}
    for nk in ("packed one", "packed two", "packed three", "game 0007"):
        media_choose.select(con, only=[nk], kinds=["cover"])
    b = {r[0]: r[1] for r in con.execute(
        "SELECT norm_key, ref FROM media WHERE chosen=1")}
    check("scoped re-ranks reproduce the full pass exactly", a == b)
    con.close()

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
