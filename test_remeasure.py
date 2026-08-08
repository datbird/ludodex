#!/usr/bin/env python3
"""A verdict computed by a rule that has since been corrected must be recomputable.

`stamp_measured` is the single write-back for width/height/filler, and `materialize()`
only revisits rows whose sha1 is NULL — deliberately, so a re-run costs no network. The
consequence is that a row measured once keeps its verdict forever. That is right while
the rule is right, and wrong the moment the rule is fixed: correcting `looks_padded`
changed nothing for the 5,245 covers already stamped, because no path ever looks at
them again.

So the bytes on disk are the source of truth and `remeasure()` re-derives from them.
No network: it only touches rows whose file is already in the repo.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-remeasure-")

try:
    from PIL import Image, ImageDraw                  # noqa: F401
except Exception:                                     # noqa: BLE001
    sys.exit("SKIPPED: Pillow not installed")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    os.environ["LUDODEX_MEDIA"] = os.path.join(D, "repo")
    os.makedirs(os.environ["LUDODEX_MEDIA"], exist_ok=True)
    import media_index
    import media_choose
    media_index.index_con().close()          # create the schema

    repo = media_choose.repo_dir()
    # a real portrait image with detail throughout — not padded by any threshold
    im = Image.new("L", (300, 450), 12)
    dr = ImageDraw.Draw(im)
    for y in range(450):
        dr.line([(0, y), (300, y)], fill=(12 if (y // 3) % 2 == 0 else 22))
    sha = "a" * 40
    im.save(os.path.join(repo, "%s.png" % sha))

    con = media_choose.con_index()
    con.execute("INSERT INTO media(norm_key,system,kind,provider,ref_type,ref,ext,"
                "sha1,width,height,chosen,matched,filler) VALUES"
                "('g',NULL,'cover','steam','url','http://x/a.png','png',?,"
                "300,450,1,1,1)", (sha,))
    # a row whose bytes are NOT in the repo — nothing to re-derive from, leave it alone
    con.execute("INSERT INTO media(norm_key,system,kind,provider,ref_type,ref,ext,"
                "sha1,width,height,chosen,matched,filler) VALUES"
                "('g',NULL,'cover','igdb','url','http://x/b.png','png','\"+\"b'||'b',"
                "264,352,0,1,1)")
    con.commit()

    n = media_choose.remeasure(con)
    check("only rows whose bytes are present are re-derived", n == 1)
    got = con.execute("SELECT filler FROM media WHERE sha1=?", (sha,)).fetchone()[0]
    check("a stale filler=1 is corrected to 0 from the bytes", got == 0)
    absent = con.execute("SELECT filler FROM media WHERE provider='igdb'").fetchone()[0]
    check("a row with no local bytes keeps its existing verdict", absent == 1)

    # idempotent — a second pass changes nothing
    check("re-running is idempotent", media_choose.remeasure(con) == 1)
    check("and the verdict is stable",
          con.execute("SELECT filler FROM media WHERE sha1=?", (sha,)).fetchone()[0] == 0)
    con.close()

    print("\n  %d/%d passed" % (sum(1 for _, c in PASS if c), len(PASS)))


if __name__ == "__main__":
    main()
