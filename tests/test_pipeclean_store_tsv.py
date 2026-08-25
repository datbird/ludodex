#!/usr/bin/env python3
"""The store-TSV loader read three columns of a four-column file.

  * A CRLF LINE ENDING MUST NOT REACH THE TITLE. This one is a GUARD, not a repair:
    `load_tsv` stripped only "\\n", but it opens the file in text mode and Python's
    universal-newline translation has already turned every "\\r\\n" into "\\n" by then,
    so nothing ever leaked. The check is here so that stays true — a future reader that
    opens with `newline=""` (which any tab/newline-escaping rewrite might reach for)
    would put an invisible "\\r" on the end of the last field on every line, which on a
    two-column row is the title the norm_key is derived from.
  * IT IGNORED A FOURTH COLUMN THAT NOW CARRIES THE ROW'S EVIDENCE. `xbox_owned` emits
    `play-history` there, because titlehub returns TITLE HISTORY, not purchases: it
    includes Game Pass titles the account never bought and misses purchases it never
    launched. docs/SOURCES.md defines a source as asserting OWNERSHIP, so a played row
    is a weaker claim than a bought one — and the loader dropped the only column that
    said so, leaving the catalog unable to tell them apart.

Offline. Drives the real build_library, because `load_tsv` only exists inside one.
"""
import os
import sqlite3
import subprocess
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-pipeclean-tsv-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

LIB = os.path.join(DATA, "game-library.sqlite")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def build():
    env = dict(os.environ, LUDODEX_DATA=DATA)
    r = subprocess.run(
        [sys.executable, os.path.join(DIR, "ludodex", "build_library.py")],
        cwd=DIR, env=env, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        sys.exit("build_library failed (%d):\n%s" % (r.returncode, r.stderr[-4000:]))
    return r


def main():
    print("store TSVs: the whole line, and every column on it")

    # A CRLF file with a two-column row (the \r lands on the title) and a four-column
    # row (the evidence column xbox_owned writes).
    with open(os.path.join(DATA, "steam_games.tsv"), "w", encoding="utf-8",
              newline="") as fh:
        fh.write("440\tTeam Fortress 2\r\n")
    with open(os.path.join(DATA, "xbox_games.tsv"), "w", encoding="utf-8",
              newline="") as fh:
        fh.write("1717790914\tPapers, Please\twindows\tplay-history\n")
        fh.write("1019936697\tBought Outright\txbox 360\n")
    build()

    con = sqlite3.connect(LIB)
    con.row_factory = sqlite3.Row

    print()
    print("1. a CRLF line ending never becomes part of the title")
    rows = [dict(r) for r in con.execute(
        "SELECT g.canonical_title, g.norm_key, s.title_raw FROM games g "
        "JOIN sources s ON s.game_id=g.id WHERE s.source='steam'")]
    check("the steam row loaded", len(rows) == 1)
    check("no CR in the canonical title", "\r" not in rows[0]["canonical_title"])
    check("no CR in the stored source title", "\r" not in (rows[0]["title_raw"] or ""))
    check("no CR in the norm_key", "\r" not in rows[0]["norm_key"])

    print()
    print("2. the fourth column is recorded as the row's evidence")
    cols = {r[1] for r in con.execute("PRAGMA table_info(sources)")}
    check("sources carries an `evidence` column", "evidence" in cols)
    ev = {r[0]: r[1] for r in con.execute(
        "SELECT title_raw, evidence FROM sources WHERE source='xbox'")}
    check("the play-history row keeps its evidence",
          ev.get("Papers, Please") == "play-history")
    check("a row with no fourth column asserts nothing extra",
          not (ev.get("Bought Outright") or ""))
    con.close()

    print()
    print("3. it survives a carry-over rebuild")
    # The store TSVs are gone; the second build re-seeds every store row from the
    # previous catalog. Evidence is part of the row, exactly like state and
    # via_collection — re-seeding without it would launder a played title into a
    # purchase on the next rebuild and there would be nothing left to say otherwise.
    os.remove(os.path.join(DATA, "xbox_games.tsv"))
    os.remove(os.path.join(DATA, "steam_games.tsv"))
    build()
    con = sqlite3.connect(LIB)
    ev = {r[0]: r[1] for r in con.execute(
        "SELECT title_raw, evidence FROM sources WHERE source='xbox'")}
    check("the carried-over row still says play-history",
          ev.get("Papers, Please") == "play-history")
    con.close()

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


main()
