#!/usr/bin/env python3
"""The pipeline should say when it had to guess, not guess silently.

A cover bucket where EVERY candidate is flagged as a letterboxed paste is the one case
the deterministic rules cannot settle: the flag is constant, so it ranks nothing, and
whatever wins does so on a tiebreak rather than on evidence. select() now breaks that
tie on detail density, which is better than size — but it is still a guess, and a guess
is exactly what a person should get the chance to overrule.

Live there are only ~14 of these, so they are reviewable by hand. Surfacing them turns
a silent wrong pick into a one-click decision that then survives every re-select, since
pins outrank everything in the sort key.

This pins the FILTER: which games qualify, and — as much — which do not, because a
"needs attention" list that cries wolf gets ignored.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-undecided-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    from server import app as srv
    import media_index

    lib = sqlite3.connect(srv.LIBRARY_DB)
    names = ["all flagged", "one clean", "single flagged", "nothing flagged"]
    for i, nk in enumerate(names, start=1):
        lib.execute("INSERT INTO games(canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,n_sources,n_kinds,sources_summary,wanted) "
                    "VALUES(?,?,'pc',?,?,?,1,0,'steam',0)",
                    (nk.title(), nk, nk + "@pc", nk, "title:" + nk))
    lib.commit()
    lib.close()

    mi = media_index.index_con()

    def add(nk, prov, filler):
        mi.execute("INSERT INTO media(norm_key,kind,provider,ref_type,ref,matched,"
                   "chosen,filler) VALUES(?,'cover',?,'url',?,1,0,?)",
                   (nk, prov, "http://x/%s-%s" % (nk, prov), filler))

    add("all flagged", "steam", 1)          # every candidate a paste -> undecidable
    add("all flagged", "igdb", 1)
    add("one clean", "steam", 1)            # the flag still discriminates
    add("one clean", "igdb", 0)
    add("single flagged", "steam", 1)       # nothing to choose BETWEEN
    add("nothing flagged", "steam", 0)
    add("nothing flagged", "igdb", 0)
    mi.commit()
    mi.close()

    con = srv.lib()
    try:
        sql = srv.FLAG_SQL["cover_undecided"]
        got = {r[0] for r in con.execute(
            "SELECT g.norm_key FROM games g WHERE " + sql)}
    finally:
        con.close()

    check("a bucket where every candidate is flagged qualifies",
          "all flagged" in got)
    check("a bucket with one clean candidate does not", "one clean" not in got)
    check("a lone flagged candidate does not — there is no choice to make",
          "single flagged" not in got)
    check("a bucket with nothing flagged does not", "nothing flagged" not in got)
    check("nothing else crept in", got == {"all flagged"})

    print("\n  %d/%d passed" % (sum(1 for _, c in PASS if c), len(PASS)))


if __name__ == "__main__":
    main()
