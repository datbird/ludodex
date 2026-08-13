#!/usr/bin/env python3
"""Art shared by many DIFFERENT games is not any one of their art.

A community themed pack ships one decorated plate and drops each game's name inside
it. Every member passes every geometric test — right kind, right shape, real image —
and is still the wrong picture for the game it is serving. Live: 43 games shared one
plate, and they won purely on provider order, because `logo` had no image-fitness
evidence of any kind (KIND_ORIENT omits it, so shape never applied; band_energy is
undefined for a landscape canvas, so `filler` and `detail` were NULL on all 2,251
logo rows).

The rule is a statement about the corpus, not a judgement about decoration: a frame
seen on N distinct games belongs to none of them. These cases pin the three things
that make it safe — it demotes rather than excludes, it does not convict a kind whose
SILHOUETTE is legitimately shared (every 3D box render is box-shaped), and it counts
across the whole index even when the pass being ranked is one game.
"""
import os
import sqlite3
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def make_image(path, frame_rgb, body_rgb, size=(240, 120)):
    """A decorated border with a distinct middle — a stand-in for a themed plate.

    The border must carry real variety: `frame_sig` deliberately refuses to sign a
    flat one, so a fixture painted in a single colour would return None and prove
    nothing about sharing."""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", size, frame_rgb + (255,))
    d = ImageDraw.Draw(im)
    w, h = size
    # a grid over the whole canvas, like the packs this exists for
    for i in range(0, w, 9):
        d.line([(i, 0), (i, h)], fill=(230, 120, 240, 255))
    for j in range(0, h, 9):
        d.line([(0, j), (w, j)], fill=(40, 60, 200, 255))
    d.rectangle([2, 2, w - 3, h - 3], outline=(250, 250, 250, 255), width=3)
    d.rectangle([w // 5, h // 5, w - w // 5, h - h // 5], fill=body_rgb + (255,))
    im.save(path)


def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "ludodex"))
    import test_support
    d = test_support.isolate("ludodex-frames-")   # BEFORE importing any ludodex module
    import media
    import media_choose

    try:
        from PIL import Image  # noqa: F401
    except Exception:
        print("SKIP: Pillow not installed")
        return

    print("1. a frame is a signature only when the border carries a design")
    plain = os.path.join(d, "plain.png")
    from PIL import Image as _I
    _I.new("RGBA", (240, 120), (0, 0, 0, 0)).save(plain)
    check("a fully transparent border yields NO signature",
          media.frame_sig(plain) is None)
    check("an unreadable path yields None, never a sentinel",
          media.frame_sig(os.path.join(d, "nope.png")) is None)

    print()
    print("2. the same plate hashes the same; a different plate does not")
    pack = []
    for i, body in enumerate([(200, 10, 10), (10, 200, 10), (10, 10, 200), (240, 240, 0)]):
        p = os.path.join(d, "pack%d.png" % i)
        make_image(p, (90, 20, 140), body)      # ONE frame, four different middles
        pack.append(media.frame_sig(p))
    other = os.path.join(d, "other.png")
    make_image(other, (10, 90, 30), (200, 10, 10))   # different frame, same middle
    check("four pack members share one signature", len(set(pack)) == 1 and pack[0])
    check("art with a different frame does not join them",
          media.frame_sig(other) != pack[0])

    print()
    print("3. a shared frame is DEMOTED, never excluded")
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    # DELIBERATELY built without `frame`, the way every pre-existing fixture and every
    # index older than the column is. select() must heal it rather than abort — it
    # aborts AFTER zeroing `chosen`, so a raised error here means no art at all.
    con.execute("""CREATE TABLE media(
        id INTEGER PRIMARY KEY, norm_key TEXT, system TEXT, game_key TEXT, kind TEXT,
        provider TEXT, ref TEXT, ref_type TEXT, matched INT, sha1 TEXT,
        width INT, height INT, filler INT, detail REAL, ai_pick INT, meta TEXT,
        chosen INT DEFAULT 0, hidden INT DEFAULT 0)""")
    media_choose.select(con)                       # heals the column
    check("select() adds a missing `frame` column instead of aborting",
          "frame" in {r[1] for r in con.execute("PRAGMA table_info(media)")})

    def add(nk, provider, kind, frame, w=600, h=300):
        con.execute(
            "INSERT INTO media(norm_key,system,game_key,kind,provider,ref,ref_type,"
            "matched,width,height,frame) VALUES(?,'','title:x',?,?,?,'url',1,?,?,?)",
            (nk, kind, provider, "http://x/%s-%s-%s.png" % (nk, provider, kind),
             w, h, frame))

    def chosen(nk, kind="logo"):
        r = con.execute("SELECT provider FROM media WHERE norm_key=? AND kind=? "
                        "AND chosen=1", (nk, kind)).fetchone()
        return r["provider"] if r else None

    # three games carry the pack plate; screenscraper outranks steam for `logo`, so
    # without the rule the plate wins every time.
    for g in ("game a", "game b", "game c"):
        add(g, "screenscraper", "logo", "PLATE")
    add("game a", "steam", "logo", "own-frame-a")
    add("game b", "steam", "logo", "own-frame-b")
    # game c has ONLY the pack member — the case that must not lose its art
    media_choose.select(con)
    check("a game with an alternative drops the shared plate", chosen("game a") == "steam")
    check("...and so does the next one", chosen("game b") == "steam")
    check("a game whose ONLY asset is a pack member keeps it",
          chosen("game c") == "screenscraper")

    print()
    print("4. provider order is what it overrides, not what it depends on")
    check("screenscraper really does outrank steam for logo — the rule had to beat it",
          list(media.priority("logo")).index("screenscraper")
          < list(media.priority("logo")).index("steam"))

    print()
    print("5. a silhouette shared by a KIND is not a template")
    # Two games' 3D boxes: same outline, but each carries its own art to the edge, so
    # the frame signatures differ and neither is demoted. This is the false positive
    # that killed the alpha-silhouette version of this rule (64 good box_3d renders).
    con.execute("DELETE FROM media")
    for g in ("box a", "box b", "box c"):
        add(g, "screenscraper", "box_3d", "own-box-" + g)
        add(g, "steam", "box_3d", "own-steam-" + g)
    media_choose.select(con)
    check("every 3D box keeps its higher-priority provider",
          all(chosen(g, "box_3d") == "screenscraper" for g in ("box a", "box b", "box c")))

    print()
    print("6. two games is not a pack")
    con.execute("DELETE FROM media")
    for g in ("pair a", "pair b"):
        add(g, "screenscraper", "logo", "SHARED-BY-TWO")
        add(g, "steam", "logo", "own-" + g)
    media_choose.select(con)
    check("a frame on exactly two games (a game and its director's cut) still wins",
          chosen("pair a") == "screenscraper" and chosen("pair b") == "screenscraper")

    print()
    print("6b. a pack that varies its border COLOUR per game is caught by its OUTLINE")
    # Comix Zone kept its plate because the pack ships per-game gradients on its
    # "world" variants: that asset's frame hash was shared by exactly ONE game while
    # its own Japanese siblings clustered at 11 and 12. The plates are all the same
    # oval, so the SILHOUETTE clusters them even when the colours do not. Restricted
    # to kinds whose shape belongs to the GAME (a logo is a wordmark cut out of
    # transparency); a box_3d is box-shaped by definition, which is why the outline
    # cannot be used everywhere.
    con.execute("DELETE FROM media")
    for g in ("comix zone", "golden axe 2", "gunstar heroes"):
        con.execute(
            "INSERT INTO media(norm_key,system,game_key,kind,provider,ref,ref_type,"
            "matched,width,height,frame,sil) VALUES(?,'genesis','',?,?,?,'url',1,?,?,?,?)",
            (g, "logo", "screenscraper", "http://x/%s-plate.png" % g, 600, 300,
             "own-colour-" + g, "SAME-OVAL"))
        con.execute(
            "INSERT INTO media(norm_key,system,game_key,kind,provider,ref,ref_type,"
            "matched,width,height,frame,sil) VALUES(?,'genesis','',?,?,?,'url',1,?,?,?,?)",
            (g, "logo", "steam", "http://x/%s-word.png" % g, 640, 360,
             "own-word-" + g, "wordmark-" + g))
    media_choose.select(con)
    check("the per-game-coloured plate loses to the real wordmark",
          chosen("comix zone") == "steam")
    check("...for every member of the pack",
          chosen("golden axe 2") == "steam" and chosen("gunstar heroes") == "steam")
    check("the outline rule is scoped to kinds whose shape is the GAME's",
          media.SILHOUETTE_KINDS == ("logo",))

    print()
    print("7. the template set is GLOBAL, not scoped to the rows being ranked")
    con.execute("DELETE FROM media")
    for g in ("s a", "s b", "s c"):
        add(g, "screenscraper", "logo", "PLATE")
        add(g, "steam", "logo", "own-" + g)
    # Re-rank ONE game. If the count were taken from the scoped rows it would see the
    # plate once, call it unique, and hand the slot straight back.
    media_choose.select(con, only=["s a"])
    check("a one-game re-rank still knows the plate is shared", chosen("s a") == "steam")

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
