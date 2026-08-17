"""TheGamesDB's grain is finer than ours, and the walk has to keep it that way.

A row there is one per (title, platform, REGION) — Sonic 2 has separate NTSC-U and PAL
Genesis rows with different dates. Nothing else we mirror models region as part of
identity, so the walk stores the columns as they arrive and lets `pick_release` decide
against a filename later. Collapsing them here would discard the distinction at the only
moment it is free.

The rest is the ss_mirror discipline: walk the ID SPACE in batches of 20 (their page size,
measured), a durable cursor because 6,900 requests is long enough for something to happen,
and a finished walk that does not re-declare itself finished on the next run.
"""
import os, sys, json
PASS = []
def check(l, c):
    PASS.append(c); print("  %s   %s" % ("ok " if c else "FAIL", l))
    if not c: sys.exit("FAILED: " + l)

GEN_US = {"id": 142, "game_name": "Sonic the Hedgehog 2", "platform": 18,
          "region_id": 2, "country_id": 50, "release_date": "1992-11-24",
          "players": 2, "coop": "Yes", "rating": "E - Everyone",
          "genres": [15], "developers": [7574], "publishers": [346],
          "youtube": "abc"}
GEN_PAL = dict(GEN_US, id=124507, region_id=6, country_id=20)
CRYSIS = {"id": 2, "game_name": "Crysis", "platform": 1, "region_id": 0,
          "release_date": "2007-11-13", "os": "Windows XP/Vista",
          "processor": "2.8GHz", "ram": "1GB", "hdd": "12GB", "video": "256MB",
          "sound": "DirectX 9.0c", "genres": [], "developers": [], "publishers": []}
VOCAB = {"genres": {"15": "Platform"}, "developers": {"7574": "Sonic Team"},
         "publishers": {"346": "Sega"}, "regions": {}, "countries": {}}
ART = {"142": [{"kind": "cover", "url": "http://x/1.jpg", "width": 800, "height": 1000}]}

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root); sys.path.insert(0, os.path.join(root, "tests"))
    import test_support; test_support.isolate("ludodex-tgdbmirror-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import config, thegamesdb as T, tgdb_mirror as TM, matchindex as M

    print("1. REGION IS PART OF THE ROW, and both rows survive")
    con = TM.con_db()
    for r in (GEN_US, GEN_PAL, CRYSIS):
        TM.store(con, r, ART, VOCAB, 1000)
    con.commit()
    rows = con.execute("SELECT id,region_id,year FROM tgdb_games WHERE platform=18 "
                       "ORDER BY id").fetchall()
    check("two Genesis rows, not one collapsed", len(rows) == 2)
    check("NTSC-U and PAL kept apart", {r["region_id"] for r in rows} == {2, 6})
    check("year derived from the release date", rows[0]["year"] == 1992)
    g = con.execute("SELECT * FROM tgdb_games WHERE id=142").fetchone()
    check("genre ids resolved to names", g["genres"] == "Platform")
    check("developers and publishers too",
          g["developers"] == "Sonic Team" and g["publishers"] == "Sega")
    check("esrb kept as its readable string", g["esrb"] == "E - Everyone")
    check("art refs stored with their measured size", con.execute(
        "SELECT width FROM tgdb_art WHERE game_id=142").fetchone()[0] == 800)

    print()
    print("2. the PC minimum spec, which nothing else here carries")
    c = con.execute("SELECT os,min_spec,region_id FROM tgdb_games WHERE id=2").fetchone()
    check("os", c["os"] == "Windows XP/Vista")
    check("spec is structured, not a blob of prose",
          set(json.loads(c["min_spec"])) == {"processor", "ram", "hdd", "video", "sound"})
    check("region 0 is stored as 0, not invented", c["region_id"] == 0)
    con.close()

    print()
    print("3. the walk batches 20 and keeps a durable cursor")
    calls = []
    def fake_by_ids(ids, key=None):
        calls.append(list(ids))
        out = [dict(GEN_US, id=i, game_name="G%d" % i) for i in ids if i <= 45]
        return out, {}
    T.by_ids = fake_by_ids
    T.vocabulary = lambda force=False, key=None: VOCAB
    T.budget = lambda key=None: 100
    T.api_key = lambda: "k"
    r = TM.walk(progress=False, until_id=60)
    check("blocks of 20", all(len(c) <= 20 for c in calls) and len(calls[0]) == 20)
    check("45 games found", r["games"] == 45)
    check("it walked to the ceiling given", r["cursor"] == 60)
    check("and reports why it stopped: %s" % r["stopped"], r["stopped"] == "complete")

    print()
    print("4. a FINISHED walk does not re-declare itself finished")
    # ss_mirror's bug: dead_run persists past the stop line, so a re-run walks one block
    # and stops again, creeping forward forever without finding what was added since.
    calls.clear()
    # Enough budget to actually prove the end: DEAD_RUN_STOP is now 500 blocks, so
    # "the catalogue has ended" costs 500 requests to establish. That is the price of
    # the threshold being large enough to clear a real 3,000-id gap.
    T.budget = lambda key=None: 10000
    r3 = TM.walk(progress=False)          # no until_id: it must probe ABOVE the cursor
    check("a fresh run probes above the old cursor rather than exiting immediately",
          len(calls) > 1 and calls[0][0] == 61)
    check("and stops again once the dead run proves the end", r3["stopped"] == "complete")
    check("having spent DEAD_RUN_STOP requests to prove it, not one",
          r3["requests"] >= TM.DEAD_RUN_STOP)
    T.budget = lambda key=None: 100

    print()
    print("5. a budget stop resumes from the cursor")
    con = TM.con_db(); con.execute("DELETE FROM state"); con.commit(); con.close()
    calls.clear()
    r2 = TM.walk(max_requests=1, progress=False, until_id=200)
    check("stopped on budget", r2["stopped"] == "budget" and r2["cursor"] == 20)
    calls.clear()
    TM.walk(max_requests=1, progress=False, until_id=200)
    check("the next run starts AFTER the cursor, not at zero", calls[0][0] == 21)

    print()
    print("6. the index attaches both regional rows to one identity")
    src = open(os.path.join(root, "ludodex", "matchindex.py"), encoding="utf-8").read()
    step = src[src.index("def _merge_tgdb_catalog"):src.index("def _match_tgdb")]
    check("already-anchored ids are skipped", "if gid in known" in step)
    check("the finer grain is explained where it matters", "REGION" in step)
    check("a miss mints its own identity", "TGDB_CAT_ID_BASE + int" in step)
    check("in its own range", M.TGDB_CAT_ID_BASE > M.MOBY_ID_BASE)
    gate = src[src.index("def _match_tgdb"):src.index("def _merge_moby")]
    check("and it uses the same acceptance gate", "matchgate.score" in gate)

    print()
    print("7. THE DEAD-RUN THRESHOLD MUST SURVIVE A REAL GAP")
    # Shipped at 40 blocks (800 ids) on an unmeasured claim. Live, TheGamesDB has a
    # ~3,000-id dead run from ~56,980 to 60,000 and then resumes at 20/20 alive; the
    # walk stopped inside it, said COMPLETE, and left 73,000 ids unwalked.
    check("the threshold clears the largest gap actually observed, with margin",
          TM.DEAD_RUN_STOP * TM.BLOCK >= 9000)
    check("and the incident is recorded so it is not tuned back down",
          "56,980" in open(os.path.join(root, "ludodex", "tgdb_mirror.py"),
                           encoding="utf-8").read())
    calls.clear()
    con = TM.con_db(); con.execute("DELETE FROM state"); con.commit(); con.close()
    def gapped(ids, key=None):
        calls.append(list(ids))
        # alive, then a 2,000-id hole, then alive again — the real shape
        return ([dict(GEN_US, id=i, game_name="G%d" % i)
                 for i in ids if i <= 100 or i > 2100], {})
    T.by_ids = gapped
    T.budget = lambda key=None: 10000     # enough to cross the hole and out the far side
    r = TM.walk(progress=False, until_id=2200)
    check("it walked THROUGH the hole rather than stopping in it: %d games"
          % r["games"], r["games"] > 100)
    check("and reached the far side", r["cursor"] == 2200)

    print()
    print("%d checks, all passed" % len(PASS))

main()
