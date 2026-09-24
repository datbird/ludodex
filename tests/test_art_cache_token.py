#!/usr/bin/env python3
"""Art URLs carry a version token that names the EXACT asset served, so the browser can
keep them for a year.

`v` used to be the first 12 characters of the chosen cover's sha1, and that was not good
enough to promise a browser the bytes behind a URL never change: a chosen row with no
sha1 yet (an unmaterialized URL ref, the normal state in `ondemand` media mode) was
skipped by the grid's version but not by the serve route, so a newly chosen cover could
keep the old `v` and a long cache would have pinned the old picture.

The token is now a short hash of the served asset's identity (row id plus sha1, or the
ref when there is no sha1 yet), computed for the grid through the SAME selection the
serve route uses (media_choose.serve_pick_sql). The serve route sends
`Cache-Control: public, max-age=31536000, immutable` only when the request's `v` equals
the token of what it is actually serving, and `no-cache` otherwise.

This pins:
  1. the token changes whenever the chosen asset changes, including to or from an
     unmaterialized URL ref and a row with no sha1;
  2. the serve route answers immutable for a matching v and no-cache for a missing or
     stale one;
  3. the grid and the serve route agree on the token for the same game;
  4. the per-asset URLs the detail page uses carry a token the asset route honours.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
DATA = test_support.isolate("ludodex-artv-")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ludodex"))

PASS = []

IMMUTABLE = "public, max-age=31536000, immutable"


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def png(path, rgb):
    from PIL import Image
    Image.new("RGB", (8, 12), rgb).save(path, "PNG")


def main():
    import hashlib
    from server import app as srv

    # ---- one game on one console, in the catalog and media index the server seeds
    lib = sqlite3.connect(srv.LIBRARY_DB)
    lib.execute("INSERT INTO games(id,canonical_title,norm_key,platform,entry_key,base_key,"
                "game_key,sources_summary) VALUES(1,'Klax','klax','snes','klax@snes',"
                "'klax','igdb:70','emulation')")
    lib.execute("INSERT INTO sources(game_id,source,platform) VALUES(1,'emulation','snes')")
    lib.commit()
    lib.close()

    def media_rows(*rows):
        c = sqlite3.connect(srv.INDEX_DB)
        c.execute("DELETE FROM media")
        for (mid, system, ref_type, ref, sha1, chosen) in rows:
            c.execute("INSERT INTO media(id,norm_key,system,kind,provider,ref_type,ref,ext,"
                      "sha1,chosen,game_key) VALUES(?,?,?,'cover','igdb',?,?,'png',?,?,"
                      "'igdb:70')",
                      (mid, "klax", system, ref_type, ref, sha1, chosen))
        c.commit()
        c.close()

    def grid_v():
        con = srv.lib()
        try:
            res = srv._query_games(con, limit=10, identified="all")
        finally:
            con.close()
        return res["items"][0]["cover_v"]

    def spotlight_v():
        con = srv.lib()
        try:
            rows = srv._spotlight_rows(con, "", [], "gs.universal DESC")
        finally:
            con.close()
        return rows[0]["cover_v"] if rows else None

    # the bytes, content-addressed in the repo
    shaA = hashlib.sha1(b"A").hexdigest()
    shaB = hashlib.sha1(b"B").hexdigest()
    png(os.path.join(srv.REPO, shaA + ".png"), (200, 0, 0))
    png(os.path.join(srv.REPO, shaB + ".png"), (0, 0, 200))

    def serve(v, size=None):
        return srv.media_asset("klax@snes", "cover", size=size, v=v)

    print("1. the token names the chosen asset, and moves when the choice moves")
    media_rows((10, "snes", "url", "http://x/a.png", shaA, 1),
               (11, "snes", "url", "http://x/b.png", shaB, 0))
    vA = grid_v()
    check("a chosen, materialized cover has a token", bool(vA))
    check("it is not the old bare sha1 prefix", vA != shaA[:12])

    media_rows((10, "snes", "url", "http://x/a.png", shaA, 0),
               (11, "snes", "url", "http://x/b.png", shaB, 1))
    vB = grid_v()
    check("choosing a different materialized cover changes the token", vB and vB != vA)

    # the case the sha1-prefix version got wrong: the new pick has no sha1 yet
    media_rows((10, "snes", "url", "http://x/a.png", shaA, 0),
               (12, "snes", "url", "http://x/c.png", None, 1))
    vC = grid_v()
    check("an UNMATERIALIZED url ref still gets a token", bool(vC))
    check("and it differs from the previous pick's", vC not in (vA, vB))

    # a sha1-less row whose own-console tier ranks above a materialized neutral one:
    # the old version skipped the sha1-less row and reported the neutral art's hash
    media_rows((13, "", "url", "http://x/n.png", shaA, 1),
               (12, "snes", "url", "http://x/c.png", None, 1))
    check("a sha1-less own-console pick is not reported as the neutral art",
          grid_v() == vC)

    media_rows((12, "snes", "url", "http://x/d.png", None, 1))
    check("the same row pointing at a different ref changes the token", grid_v() != vC)

    print("2. serve: immutable only for the current token, no-cache otherwise")
    media_rows((10, "snes", "url", "http://x/a.png", shaA, 1))
    vA = grid_v()
    r = serve(vA)
    check("matching v -> immutable", r.headers.get("cache-control") == IMMUTABLE)
    r = serve(None)
    check("no v -> no-cache", r.headers.get("cache-control") == "no-cache")
    r = serve(vB)
    check("stale v -> no-cache", r.headers.get("cache-control") == "no-cache")
    r = serve(vA, size="thumb")
    check("the thumbnail with a matching v -> immutable",
          r.headers.get("cache-control") == IMMUTABLE)

    print("3. an unmaterialized pick: the first serve fetches it, and the token holds")
    real = srv.media_choose._materialize_row

    def fake(repo, row):
        return shaB                                    # "downloaded" to the B bytes
    media_rows((12, "snes", "url", "http://x/c.png", None, 1))
    vC = grid_v()
    srv.media_choose._materialize_row = fake
    try:
        r = serve(vC)
    finally:
        srv.media_choose._materialize_row = real
    check("the token the grid handed out is honoured on the fetching serve",
          r.headers.get("cache-control") == IMMUTABLE)
    vC2 = grid_v()
    check("after materializing, the grid's token moves (sha1 now known)", vC2 != vC)
    check("and the old URL is no longer the current one",
          serve(vC).headers.get("cache-control") == "no-cache")
    check("while the new one is", serve(vC2).headers.get("cache-control") == IMMUTABLE)

    print("4. a user upload wins, and carries its own token")
    uc = srv._umedia_con()
    uc.execute("INSERT INTO user_media(norm_key,kind,sha1,ext,created) "
               "VALUES('klax','cover',?,'png',1)", (shaA,))
    uc.commit()
    uc.close()
    vU = grid_v()
    check("an upload changes the grid token", vU not in (vC2, vA))
    check("and the serve route honours it",
          serve(vU).headers.get("cache-control") == IMMUTABLE)
    check("the media row's token is now stale",
          serve(vC2).headers.get("cache-control") == "no-cache")
    uc = srv._umedia_con()
    uc.execute("DELETE FROM user_media")
    uc.commit()
    uc.close()

    print("5. grid and serve agree through the one selection rule")
    # three tiers: own console beats same-title neutral beats cross-title neutral
    c = sqlite3.connect(srv.INDEX_DB)
    c.execute("DELETE FROM media")
    c.execute("INSERT INTO media(id,norm_key,system,kind,provider,ref_type,ref,ext,sha1,"
              "chosen,game_key) VALUES(20,'klaxother','','cover','igdb','url',"
              "'http://x/o.png','png',?,1,'igdb:70')", (shaA,))
    c.execute("INSERT INTO media(id,norm_key,system,kind,provider,ref_type,ref,ext,sha1,"
              "chosen,game_key) VALUES(30,'klax','','cover','igdb','url',"
              "'http://x/n.png','png',?,1,'igdb:70')", (shaB,))
    c.commit()
    ro = sqlite3.connect(srv.INDEX_DB)
    pick = srv.media_choose.serve_pick(ro, "klax", "snes", "igdb:70", "cover")
    ro.close()
    check("serve_pick takes this title's neutral art over another title's", pick == 30)
    check("the grid token is the one serve honours",
          serve(grid_v()).headers.get("cache-control") == IMMUTABLE)
    c.execute("DELETE FROM media WHERE id=30")
    c.commit()
    check("with only cross-title neutral art they still agree",
          serve(grid_v()).headers.get("cache-control") == IMMUTABLE)
    c.close()
    check("Spotlight hands out the grid's token", spotlight_v() == grid_v())

    print("6. the detail page's per-asset URLs carry a token the asset route honours")
    media_rows((10, "snes", "url", "http://x/a.png", shaA, 1))
    gm = srv.game_media("klax@snes", entry=("klax", "snes"))
    a = [x for x in gm["assets"] if x["id"] == 10][0]
    check("the asset url carries v", "?v=" in a["url"])
    check("the thumb url carries size and v",
          "size=thumb" in a["thumb"] and "v=" in a["thumb"])
    v = a["url"].split("v=", 1)[1]
    r = srv.media_asset_by_id(10, size=None, v=v)
    check("the asset route: matching v -> immutable",
          r.headers.get("cache-control") == IMMUTABLE)
    r = srv.media_asset_by_id(10, size=None, v=None)
    check("the asset route: no v -> no-cache", r.headers.get("cache-control") == "no-cache")

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
