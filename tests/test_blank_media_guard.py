#!/usr/bin/env python3
"""The blank-image guard must cover every kind that OCCUPIES A SLOT.

Shinobi III (Genesis) showed a pure-black 745x745 ScreenScraper `mix` as its #1 USED
asset. The guard that exists for exactly this — providers, ScreenScraper especially,
return placeholder art that passes a bare HTTP check and is useless — never looked at it.

`_prune_blank_media` guarded a hand-written tuple of eight kinds:

    cover, hero, background, header, logo, icon, box_back, box_3d

`mix` is not in it. Neither are marquee, title_screen, box_spine, bezel, arcade_cabinet,
arcade_controls or pcb. Every one of those is a SCALAR kind — exactly one asset is
displayed — so a blank one takes the slot and there is no second chance to be right.
A list maintained by hand against a vocabulary that grows is the same defect as every
other one today: derived truth in two places, drifting quietly.

The rule the guard actually encodes is "a blank asset is harmful when it occupies a
slot", and the set of slot kinds already has a name: media.SCALAR_KINDS. Multi-kinds
(screenshot, flyer, map, physical_media) are deliberately NOT guarded — a blank
screenshot among twelve costs nothing and checking them means downloading every
screenshot in the library.
"""
import io
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-blank-")

import media                                     # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _png(color, size=(64, 64)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


def main():
    from server import app as srv

    print("1. the guarded set is DERIVED, not hand-listed")
    import inspect
    default = inspect.signature(srv._prune_blank_media).parameters["kinds"].default
    guarded = set(default)
    check("mix is guarded (the Shinobi III case)", "mix" in guarded)
    for k in ("marquee", "title_screen", "box_spine", "bezel"):
        check("%s is guarded" % k, k in guarded)
    check("every scalar kind is guarded — no slot left unchecked",
          set(media.SCALAR_KINDS) <= guarded)

    print("2. multi-kinds stay out (a blank one takes no slot, and costs downloads)")
    for k in ("screenshot", "flyer", "map", "video", "manual"):
        check("%s is not swept" % k, k not in guarded)

    print("3. a solid-black image is judged blank; real art is not")
    check("pure black is degenerate", srv._is_degenerate_image(_png((0, 0, 0))))
    check("pure white is degenerate", srv._is_degenerate_image(_png((255, 255, 255))))
    from PIL import Image
    import random
    rnd = Image.new("RGB", (64, 64))
    rnd.putdata([(random.randint(0, 255), random.randint(0, 255),
                  random.randint(0, 255)) for _ in range(64 * 64)])
    b = io.BytesIO()
    rnd.save(b, "PNG")
    check("noisy art is kept", not srv._is_degenerate_image(b.getvalue()))

    print("4. a blank scalar asset is deleted, and a real one takes the slot")
    repo = os.path.join(D, "files")
    os.makedirs(repo, exist_ok=True)
    blank_p = os.path.join(repo, "blank.png")
    real_p = os.path.join(repo, "real.png")
    open(blank_p, "wb").write(_png((0, 0, 0), (745, 745)))
    open(real_p, "wb").write(b.getvalue())

    con = sqlite3.connect(srv.INDEX_DB)
    con.execute("DELETE FROM media WHERE norm_key='shinobi 3'")
    for p in (blank_p, real_p):
        con.execute("INSERT INTO media(norm_key,system,game_key,kind,provider,ref_type,"
                    "ref,ext,matched,chosen) VALUES('shinobi 3','genesis',"
                    "'igdb:1','mix','screenscraper','file',?,'png',1,0)", (p,))
    con.commit()
    con.close()

    dropped = srv._prune_blank_media(["shinobi 3"])
    check("the blank mix was dropped", dropped == 1)

    con = sqlite3.connect(srv.INDEX_DB)
    left = [r[0] for r in con.execute("SELECT ref FROM media WHERE norm_key='shinobi 3' "
                                      "AND kind='mix'")]
    con.close()
    check("the real one survived", left == [real_p])

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
