#!/usr/bin/env python3
"""Members created during a run must JOIN that run, not trail it (#20).

A member comes into existence mid-apply — after the working set was decided — so it
used to miss every phase that followed. The fix bolted on afterwards was a parallel
deterministic pass, which is exactly how it got its own (wrong) ordering: it did
pull -> select with no measure, and a 460x215 grid won a `cover` slot for the Halo MCC
members.

The architecture instead: materialize, resolve identity, then merge the keys into
`touched`. From there members ride the SAME scoped media reconcile as every other
touched game — same fetch, same select, same AI art pass — so they inherit the tier of
the run that created them with no tier parameter threaded anywhere.

This pins the seam: the wrapper reports what it created, and does NOT fire the
standalone ingest when a run is going to take the keys.
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


def main():
    d = tempfile.mkdtemp(prefix="ludodex-joinrun-")
    os.environ["LUDODEX_DATA"] = d
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import catalog_patch
    import compilations
    from titlenorm import norm
    from server import app as srv

    # A catalog with an owned bundle whose member does not exist yet. The tables
    # already exist — importing server.app seeds an empty catalog on first run — so
    # this inserts into the real schema rather than inventing one.
    lib = sqlite3.connect(os.path.join(d, "game-library.sqlite"))
    # The first-run seed omits via_collection (materialize_members correctly no-ops
    # without it, treating the catalog as pre-column); build_library creates it.
    if "via_collection" not in [r[1] for r in lib.execute("PRAGMA table_info(sources)")]:
        lib.execute("ALTER TABLE sources ADD COLUMN via_collection TEXT")
    cur = lib.execute(
        "INSERT INTO games(canonical_title,norm_key,platform,entry_key,base_key,"
        "game_key,n_sources,n_kinds,sources_summary,wanted) "
        "VALUES('Some Bundle','some bundle','pc','some bundle@pc','some bundle',"
        "'title:some bundle',1,0,'steam',0)")
    lib.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw,state) "
                "VALUES(?,'steam','pc','10','Some Bundle','have')", (cur.lastrowid,))
    lib.commit()
    lib.close()

    compilations.set_collection(d, "some bundle", "Some Bundle",
                                [{"title": "Brand New Member", "platform": "PC"}],
                                origin="ai")

    print("1. the wrapper reports what it created")
    created = []
    fired = []
    real = srv._ingest_new_members
    srv._ingest_new_members = lambda c: fired.append(list(c))
    try:
        srv._materialize_collection_members(created_out=created, ingest=False)
    finally:
        srv._ingest_new_members = real
    keys = [k for k, _p in created]
    check("the new member is reported to the caller", norm("Brand New Member") in keys)
    check("it carries a resolved platform", all(p for _k, p in created))

    print("2. with a run to join, the standalone pass does NOT fire")
    check("no parallel deterministic ingest was started", fired == [])

    print("3. with NO run to join, it DOES fire (manual record keeps its ingest)")
    # delete the member entry so materialize creates it again
    lib = sqlite3.connect(os.path.join(d, "game-library.sqlite"))
    lib.execute("DELETE FROM sources WHERE via_collection IS NOT NULL")
    lib.execute("DELETE FROM games WHERE base_key=?", (norm("Brand New Member"),))
    lib.commit()
    lib.close()
    fired2 = []
    real = srv._ingest_new_members
    srv._ingest_new_members = lambda c: fired2.append(list(c))
    try:
        srv._materialize_collection_members()          # defaults: ingest=True
    finally:
        srv._ingest_new_members = real
    check("standalone ingest fires when nothing will take the keys", len(fired2) == 1)
    check("and it receives the created member",
          norm("Brand New Member") in [k for k, _p in fired2[0]])

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
