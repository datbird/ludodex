#!/usr/bin/env python3
"""An HTTP 200 is not an image. Verify the BYTES before they are stamped (#29).

`_materialize_row` read the response body and sha1'd it with nothing looking at what it
actually was. A provider that is out of quota, behind a CDN interstitial, or simply
confused answers 200 with an HTML page — and that page was written to the repo as
`<sha>.jpg` and stamped onto the row.

Nothing downstream can catch it afterwards, and that is the point:

  * `_measure()` returns (None, None) for anything Pillow cannot open,
  * `shape_ok()` reads unknown dimensions as acceptable,
  * the blank-media guard returns False ("keep it") for undecodable bytes, and
  * `materialize()` only revisits rows whose `sha1 IS NULL`.

So the error page holds the cover slot permanently and is served as the cover, while
real candidates sit unchosen. Every gate reads a miss as consent — the fail-open shape.
The check has to happen BEFORE the stamp, and a bad body has to be a recorded failure
(the row is dropped and the next-best candidate elected), not a stored asset.

Offline. Every "download" is a file:// URL from this test's own temp dir.
"""
import io
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-badbody-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import media                                                   # noqa: E402
import media_choose                                            # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _png(size=(600, 900), color=(30, 90, 160)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


def _jpg(size=(300, 450)):
    from PIL import Image
    import random
    im = Image.new("RGB", size)
    im.putdata([(random.randint(0, 255), random.randint(0, 255),
                 random.randint(0, 255)) for _ in range(size[0] * size[1])])
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return b.getvalue()


QUOTA_HTML = (b"<!DOCTYPE html><html><head><title>ScreenScraper</title></head>"
              b"<body>Votre quota de scrape est depasse</body></html>")


def _write(name, data):
    p = os.path.join(DATA, name)
    with open(p, "wb") as fh:
        fh.write(data)
    return p


def _url(path):
    return "file://" + path


def main():
    print("1. media.asset_format judges the BYTES, not the URL")
    check("an HTML error page is not an image",
          media.asset_format(QUOTA_HTML) is None)
    check("a JSON error body is not an image",
          media.asset_format(b'{"error":"rate limited"}') is None)
    check("an empty body is not an image", media.asset_format(b"") is None)
    check("a PNG is a png", media.asset_format(_png()) == "png")
    check("a JPEG is a jpg", media.asset_format(_jpg()) == "jpg")
    check("a PDF is a pdf", media.asset_format(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n") == "pdf")
    # a body that starts right and stops early is the other half of the same defect:
    # the magic number alone would pass it, and a half-written cover is served forever.
    truncated = _png()[:400]
    check("a TRUNCATED image is rejected too", media.asset_format(truncated) is None)
    # declared-family disagreement: a manual that came back as a picture, or a cover
    # that came back as a PDF, is the wrong body whatever else it is.
    check("an image body declared as a pdf is refused",
          media.asset_format(_png(), ext="pdf") is None)
    check("a pdf body declared as a jpg is refused",
          media.asset_format(b"%PDF-1.4\n", ext="jpg") is None)
    check("a png body declared jpg is still fine (providers mislabel extensions)",
          media.asset_format(_png(), ext="jpg") == "png")

    print("2. _materialize_row refuses the body and stores NOTHING")
    repo = media_choose.repo_dir()
    before = set(os.listdir(repo))
    bad = _write("quota.html", QUOTA_HTML)
    # a plain provider, deliberately: routing through screenscraper's auth branch
    # could return None for a reason that has nothing to do with the body.
    row = {"id": 1, "ref_type": "url", "ref": _url(bad), "provider": "steam",
           "ext": "jpg", "kind": "cover"}
    check("no sha1 is handed back for an HTML body",
          media_choose._materialize_row(repo, row) is None)
    check("and nothing was written into the repo",
          set(os.listdir(repo)) == before)
    check("not even a leftover .tmp",
          not [f for f in os.listdir(repo) if f.endswith(".tmp")])

    print("   a LOCAL file holding the same rubbish is refused identically")
    row_f = dict(row, ref_type="file", ref=bad)
    check("a local non-image is refused", media_choose._materialize_row(repo, row_f) is None)
    check("still nothing in the repo", set(os.listdir(repo)) == before)

    print("   real art still materializes")
    good = _write("cover.png", _png())
    row_g = {"id": 2, "ref_type": "url", "ref": _url(good), "provider": "igdb",
             "ext": "png", "kind": "cover"}
    sha = media_choose._materialize_row(repo, row_g)
    check("a real image is stored", bool(sha))
    check("the bytes landed in the repo",
          os.path.exists(os.path.join(repo, "%s.png" % sha)))

    print("3. through materialize(): a bad body is a RECORDED FAILURE, not the cover")
    import media_index
    media_index.index_con().close()          # the canonical schema
    con = media_choose.con_index()
    con.execute("DELETE FROM media")
    # the quota page is what a top-priority provider answered; a real cover sits below it
    con.execute("INSERT INTO media(id,norm_key,system,kind,provider,ref_type,ref,ext,"
                "matched,chosen) VALUES(10,'shinobi 3','','cover','steam',"
                "'url',?,'jpg',1,0)", (_url(bad),))
    con.execute("INSERT INTO media(id,norm_key,system,kind,provider,ref_type,ref,ext,"
                "matched,chosen) VALUES(11,'shinobi 3','','cover','igdb',"
                "'url',?,'png',1,0)", (_url(good),))
    con.commit()
    media_choose.select(con, kinds=["cover"])
    chosen = [r[0] for r in con.execute(
        "SELECT id FROM media WHERE norm_key='shinobi 3' AND chosen=1")]
    check("selection picks the higher-priority provider first (id 10)", chosen == [10])

    ok, dead = media_choose.materialize(con)
    check("the HTML body counts as DEAD, never as materialized", (ok, dead) == (0, 1))
    gone = con.execute("SELECT COUNT(*) FROM media WHERE id=10").fetchone()[0]
    check("the row that answered with a web page is dropped", gone == 0)
    stamped = con.execute("SELECT sha1 FROM media WHERE id=10").fetchone()
    check("it was never stamped with a sha1", stamped is None)
    now = [r[0] for r in con.execute(
        "SELECT id FROM media WHERE norm_key='shinobi 3' AND chosen=1")]
    check("the real cover took the slot", now == [11])
    # the re-elected row is materialized by the NEXT pass (drop_dead re-picks after this
    # pass's row set was already read) — main() re-selects, and serve-time pulls it too.
    ok2, dead2 = media_choose.materialize(con)
    check("the second pass pulls the replacement", (ok2, dead2) == (1, 0))
    sha11 = con.execute("SELECT sha1 FROM media WHERE id=11").fetchone()[0]
    check("and it is materialized", bool(sha11))
    check("the repo holds no HTML masquerading as art",
          all(media.asset_format(open(os.path.join(repo, f), "rb").read()) is not None
              for f in os.listdir(repo) if os.path.isfile(os.path.join(repo, f))))
    con.close()

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
