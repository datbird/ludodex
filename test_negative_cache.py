#!/usr/bin/env python3
"""A "we looked and found nothing" row must not read as "already identified" (#25).

`igdb_resolution` doubles as a NEGATIVE cache: a row with `igdb_id=0, matched_by='none'`
records that a previous pass searched and came up empty, so the next run doesn't pay for
the same miss. That is correct and worth keeping.

What was wrong is the read. `_member_identity` asked `SELECT 1 FROM igdb_resolution
WHERE norm_key=?` and treated ANY row as "already identified — never re-decide". So the
40 live rows carrying id 0 were permanently locked out of ever getting an identity, and
not all of them are non-games: crash bandicoot 3 warped, ys i ancient ys vanished and
three Space Quest chapters sit in that list. A miss became permanent by being recorded.

The distinction these pin: a REAL id is a decision to respect; a falsy id is an absence
of one.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-negcache-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    from server import app as srv

    lib = sqlite3.connect(srv.LIBRARY_DB)
    for nk, title in (("real miss", "Real Miss"), ("already known", "Already Known"),
                      ("hand picked", "Hand Picked")):
        lib.execute("INSERT INTO games(canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,n_sources,n_kinds,sources_summary,wanted) "
                    "VALUES(?,?,'pc',?,?,?,1,0,'steam',0)",
                    (title, nk, nk + "@pc", nk, "title:" + nk))
    lib.commit()
    lib.close()

    mc = sqlite3.connect(os.path.join(D, "metadata-cache.sqlite"))
    mc.execute("CREATE TABLE IF NOT EXISTS igdb_resolution(norm_key TEXT PRIMARY KEY, "
               "igdb_id INTEGER, slug TEXT, matched_by TEXT, resolved_at INTEGER)")
    # exactly the three shapes that exist live
    mc.execute("INSERT INTO igdb_resolution VALUES('real miss',0,NULL,'none',1)")
    mc.execute("INSERT INTO igdb_resolution VALUES('already known',1234,'ak','name',1)")
    mc.execute("INSERT INTO igdb_resolution VALUES('hand picked',0,NULL,'manual',1)")
    mc.commit()
    mc.close()

    looked_up = []

    def fake_by_name(title):
        looked_up.append(title)
        # the shape _igdb_hits() produces: igdb_id (not id), platforms as abbreviations
        return [{"igdb_id": 999, "name": title, "year": 1996, "slug": "found",
                 "platforms": ["PC"], "cover": None}]

    real = srv._igdb_by_name
    srv._igdb_by_name = fake_by_name
    try:
        print("1. a negative-cache row does not block a fresh attempt")
        rc = srv._member_identity("real miss", "pc")
        check("the lookup actually ran", "Real Miss" in looked_up)
        check("and it resolved", rc == 1)
        mc = sqlite3.connect(os.path.join(D, "metadata-cache.sqlite"))
        got = mc.execute("SELECT igdb_id FROM igdb_resolution WHERE norm_key='real miss'"
                         ).fetchone()[0]
        mc.close()
        check("the miss was replaced by the real id", got == 999)

        print("2. an existing REAL match is still never re-decided")
        looked_up.clear()
        srv._member_identity("already known", "pc")
        check("no lookup was made", looked_up == [])
        mc = sqlite3.connect(os.path.join(D, "metadata-cache.sqlite"))
        got = mc.execute("SELECT igdb_id FROM igdb_resolution "
                         "WHERE norm_key='already known'").fetchone()[0]
        mc.close()
        check("the existing id is untouched", got == 1234)

        print("3. a MANUAL decision is respected even with a falsy id")
        # The user explicitly saying "this matches nothing" is a decision, not a gap.
        looked_up.clear()
        srv._member_identity("hand picked", "pc")
        check("no lookup was made", looked_up == [])
        mc = sqlite3.connect(os.path.join(D, "metadata-cache.sqlite"))
        got = mc.execute("SELECT igdb_id, matched_by FROM igdb_resolution "
                         "WHERE norm_key='hand picked'").fetchone()
        mc.close()
        check("the manual row is untouched", tuple(got) == (0, "manual"))
    finally:
        srv._igdb_by_name = real

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
