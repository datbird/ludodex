#!/usr/bin/env python3
"""A materialized member over the ingest cap was never offered again.

`materialize_members` creates a catalog ROW for a bundle member and stops — no identity,
no media, no attributes. The ingest that finishes the job is CAPPED (one apply must never
sweep the catalog), and the log says the remainder is "deferred to the next run". It was
not. `created_out` reports what THIS call CREATED, and on the next call those rows already
exist, so materialize skips them before it ever reaches the report. A 200-member bundle
therefore ingested 60 members and left 140 permanent stubs, and every subsequent run
agreed there was nothing to do.

The deferral has to be DURABLE, because that is the only thing the next run can read.
So materialization enqueues what it creates, and the queue is drained by the caller that
actually ran the ingest — not by the act of creating the row.

`created_out` KEEPS ITS MEANING: what this call created, nothing else. It is what the
apply path merges into its own working set, and widening it would put already-ingested
members back through every phase on every apply.

Offline. Synthetic catalog + collections store, no network, no AI.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-pipeclean-backlog-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import catalog_patch                                           # noqa: E402
import compilations                                            # noqa: E402
from titlenorm import norm                                     # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def fresh_catalog():
    con = sqlite3.connect(":memory:")
    con.executescript("""
    CREATE TABLE games(
      id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT, platform TEXT,
      entry_key TEXT, base_key TEXT, game_key TEXT, n_sources INT, n_kinds INT,
      sources_summary TEXT, has_emulation INT, has_steam INT, has_gog INT,
      has_epic INT, has_itch INT, has_archive INT, in_playnite INT,
      in_launchbox INT, wanted INT);
    CREATE TABLE sources(
      id INTEGER PRIMARY KEY, game_id INT, source TEXT, platform TEXT,
      source_id TEXT, title_raw TEXT, detail TEXT, state TEXT,
      via_collection TEXT);
    CREATE TABLE game_attributes(game_id INT, kind TEXT, value TEXT, origin TEXT);
    """)
    return con


def add_entry(con, title, appid="", plat="pc"):
    nk = norm(title)
    cur = con.execute(
        "INSERT INTO games(canonical_title,norm_key,platform,entry_key,base_key,"
        "game_key,n_sources,n_kinds,sources_summary,has_emulation,has_steam,has_gog,"
        "has_epic,has_itch,has_archive,in_playnite,in_launchbox,wanted) "
        "VALUES(?,?,?,?,?,?,1,0,'steam',0,1,0,0,0,0,0,0,0)",
        (title, nk, plat, "%s@%s" % (nk, plat), nk, "title:%s" % nk))
    gid = cur.lastrowid
    con.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw,"
                "state) VALUES(?,'steam',?,?,?,'have')", (gid, plat, appid, title))
    return gid


MEMBERS = ["Backlog Member One", "Backlog Member Two", "Backlog Member Three",
           "Backlog Member Four", "Backlog Member Five"]
CAP = 2                       # stands in for server.app.MEMBER_INGEST_CAP


def seed():
    con = fresh_catalog()
    add_entry(con, "Big Bundle", "10")
    compilations.set_collection(
        DATA, norm("Big Bundle"), "Big Bundle",
        [{"title": t, "platform": "PC"} for t in MEMBERS], origin="ai")
    return con


def main():
    print("materialized members over the ingest cap")

    con = seed()

    print()
    print("1. the first run creates every member and reports them")
    created = []
    catalog_patch.materialize_members(con, DATA, created_out=created)
    check("all five members created", len(created) == 5)

    print()
    print("2. a capped caller ingests some and defers the rest")
    # This is exactly what server/app.py's _ingest_new_members does: take
    # MEMBER_INGEST_CAP of them, run the deterministic ingest, and print "deferred".
    pend = catalog_patch.pending_members(con)
    check("materialization queued every member it created", len(pend) == 5)
    took = catalog_patch.pending_members(con, limit=CAP)
    check("the caller can take a capped slice", len(took) == CAP)
    check("the slice carries the resolved platform", all(p for _k, p in took))
    catalog_patch.member_ingested(con, took)

    print()
    print("3. the NEXT run still offers the deferred remainder")
    # The bug: those three rows exist now, so materialize skips them, `created_out` is
    # empty, and the ingest never hears about them again.
    created2 = []
    made = catalog_patch.materialize_members(con, DATA, created_out=created2)
    check("a second run creates nothing", made == 0)
    check("and reports nothing as CREATED — created_out keeps its meaning",
          created2 == [])
    still = catalog_patch.pending_members(con)
    check("the three deferred members are still pending", len(still) == 3)
    check("and none of the already-ingested ones came back",
          not ({k for k, _p in still} & {k for k, _p in took}))

    print()
    print("4. draining the queue ends it")
    catalog_patch.member_ingested(con, still)
    catalog_patch.materialize_members(con, DATA)
    check("nothing is pending once every member has been ingested",
          catalog_patch.pending_members(con) == [])

    print()
    print("5. a member the reconcile pass deletes leaves no ghost in the queue")
    # A queue entry naming a row that no longer exists would hand the ingest a key with
    # no catalog entry behind it, once per run, forever.
    con2 = seed()
    catalog_patch.materialize_members(con2, DATA)
    check("queued after materialize", len(catalog_patch.pending_members(con2)) == 5)
    compilations.clear_collection(DATA, norm("Big Bundle"), "test")
    catalog_patch.materialize_members(con2, DATA)
    live = {r[0] for r in con2.execute("SELECT base_key FROM games")}
    check("the phantom entries are gone", not ({norm(t) for t in MEMBERS} & live))
    check("and so are their queue rows",
          catalog_patch.pending_members(con2) == [])

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


main()
