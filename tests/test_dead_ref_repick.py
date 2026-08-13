#!/usr/bin/env python3
"""A dead reference must drop out of contention wherever it is discovered.

`materialize()` already knew this: a ref whose bytes will not come down is deleted and
the next-best is promoted. The SERVE path — which is the only materialization at all in
`ondemand` media mode — did not. It raised 502 and left the dead row `chosen`, so the
entry showed a monogram forever, on every subsequent request, while perfectly good
candidates sat unchosen. Nothing self-healed it short of a manual batch pass.

Same defect shape as the rest: one rule ("a dead ref loses its slot") implemented in one
place and merely assumed in the other. These cases pin the shared behaviour and the
serve path's use of it.
"""
import os
import sqlite3
import sys
import tempfile

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


SCHEMA = """CREATE TABLE IF NOT EXISTS media(
    id INTEGER PRIMARY KEY, norm_key TEXT, system TEXT, game_key TEXT, kind TEXT,
    provider TEXT, ref TEXT, ref_type TEXT, ext TEXT, matched INT, sha1 TEXT,
    width INT, height INT, filler INT, detail REAL, ai_pick INT, meta TEXT, chosen INT DEFAULT 0,
    hidden INT DEFAULT 0)"""


def main():
    d = tempfile.mkdtemp(prefix="ludodex-deadref-")
    os.environ["LUDODEX_DATA"] = d
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import media_choose
    from server import app as srv

    print("1. the shared helper drops the dead ref and promotes the next best")
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(SCHEMA)
    con.execute("INSERT INTO media(id,norm_key,system,game_key,kind,provider,ref,"
                "ref_type,ext,matched,width,height,chosen) VALUES"
                "(1,'g','','title:g','cover','igdb','http://dead/x.jpg','url','jpg',1,"
                "600,900,1)")
    con.execute("INSERT INTO media(id,norm_key,system,game_key,kind,provider,ref,"
                "ref_type,ext,matched,width,height,chosen) VALUES"
                "(2,'g','','title:g','cover','steamgriddb','http://ok/y.jpg','url','jpg',"
                "1,600,900,0)")
    con.commit()
    media_choose.drop_dead(con, con.execute("SELECT * FROM media WHERE id=1").fetchone())
    check("the dead row is gone",
          con.execute("SELECT COUNT(*) FROM media WHERE id=1").fetchone()[0] == 0)
    check("the survivor is chosen",
          con.execute("SELECT chosen FROM media WHERE id=2").fetchone()[0] == 1)

    print("2. serve demotes a ref it could not fetch, instead of 502-ing forever")
    # A real library entry, a chosen cover whose URL will fail, and a live alternative.
    lib = sqlite3.connect(os.path.join(d, "game-library.sqlite"))
    if not lib.execute("SELECT name FROM sqlite_master WHERE name='games'").fetchone():
        lib.execute("CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT, "
                    "platform TEXT, game_key TEXT)")
    cols = [r[1] for r in lib.execute("PRAGMA table_info(games)")]
    lib.execute("INSERT INTO games(%s) VALUES(%s)"
                % (",".join(c for c in ("norm_key", "platform", "game_key") if c in cols),
                   ",".join("?" * len([c for c in ("norm_key", "platform", "game_key")
                                       if c in cols]))),
                tuple(v for c, v in (("norm_key", "g"), ("platform", "pc"),
                                     ("game_key", "title:g")) if c in cols))
    lib.commit()
    lib.close()

    idx = sqlite3.connect(srv.INDEX_DB)
    idx.execute(SCHEMA)
    idx.execute("DELETE FROM media WHERE norm_key='g'")
    idx.execute("INSERT INTO media(id,norm_key,system,game_key,kind,provider,ref,"
                "ref_type,ext,matched,width,height,chosen) VALUES"
                "(9001,'g','','title:g','cover','igdb','http://dead/x.jpg','url','jpg',"
                "1,600,900,1)")
    idx.execute("INSERT INTO media(id,norm_key,system,game_key,kind,provider,ref,"
                "ref_type,ext,matched,width,height,chosen) VALUES"
                "(9002,'g','','title:g','cover','steamgriddb','http://ok/y.jpg','url',"
                "'jpg',1,600,900,0)")
    idx.commit()
    idx.close()

    real = media_choose._materialize_row
    media_choose._materialize_row = lambda repo, r: None      # every fetch fails
    try:
        try:
            srv.media_asset("g@pc", "cover")
            raised = False
        except Exception:
            raised = True                 # 502/404 is fine — the point is the side effect
    finally:
        media_choose._materialize_row = real
    check("serving an unfetchable asset still errors for this request", raised)

    idx = sqlite3.connect(srv.INDEX_DB)
    gone = idx.execute("SELECT COUNT(*) FROM media WHERE id=9001").fetchone()[0]
    promoted = idx.execute("SELECT chosen FROM media WHERE id=9002").fetchone()[0]
    idx.close()
    check("the unfetchable row was dropped from contention", gone == 0)
    check("the next-best candidate is now chosen", promoted == 1)

    print("3. the last candidate dying leaves nothing chosen, not a ghost")
    con2 = sqlite3.connect(":memory:")
    con2.row_factory = sqlite3.Row
    con2.execute(SCHEMA)
    con2.execute("INSERT INTO media(id,norm_key,system,game_key,kind,provider,ref,"
                 "ref_type,ext,matched,width,height,chosen) VALUES"
                 "(1,'h','','title:h','cover','igdb','http://dead/x.jpg','url','jpg',1,"
                 "600,900,1)")
    con2.commit()
    media_choose.drop_dead(con2, con2.execute("SELECT * FROM media WHERE id=1").fetchone())
    check("no rows left and none chosen",
          con2.execute("SELECT COUNT(*) FROM media WHERE norm_key='h'").fetchone()[0] == 0)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
