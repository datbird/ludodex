#!/usr/bin/env python3
"""The AI must not be spent on entries the library itself hides as NON-games.

`_non_game_hidden_sql()` decides what is not a game — a manual `content_type`
override first, then Steam's own type, then Steam's language-independent genre id,
then the genre name. It was applied at three READ sites in `server/app.py` and
nowhere else, so `aimeta.targets()` handed the scan every hidden row anyway.

Live cost of the gap, 2026-08-07: 3DMark and The Jackbox Megapicker — both Steam
genre `Utilities`, both already hidden from the library — were analyzed by the paid
metadata area, which duly wrote 3DMark a release year and a description as though it
were a game. The model's own judgment was inconsistent about it (it refused EVGA
Precision X1, same genre, on the same run), which is exactly why the deterministic
filter has to run first rather than being left to the model.

One definition, both callers: the rule lives in `nongame.py` and the server imports
the same names it used to define.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-nongame-scan-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    import aimeta
    import overrides

    lib = sqlite3.connect(aimeta.LIBRARY_DB)
    lib.executescript("""
        CREATE TABLE IF NOT EXISTS games(
          id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT, platform TEXT,
          entry_key TEXT, base_key TEXT, game_key TEXT, n_sources INTEGER,
          n_kinds INTEGER, sources_summary TEXT, wanted INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS game_attributes(
          game_id INTEGER, kind TEXT, value TEXT);
        CREATE TABLE IF NOT EXISTS metadata_links(
          game_id INTEGER, provider TEXT, provider_id TEXT, slug TEXT);
    """)
    rows = [("a real game", "A Real Game"),
            ("benchmark by genre name", "Benchmark By Genre Name"),
            ("benchmark by genre id", "Benchmark By Genre Id"),
            ("tool by steam type", "Tool By Steam Type"),
            ("game steam calls a tool", "Game Steam Calls A Tool")]
    for i, (nk, title) in enumerate(rows, start=1):
        lib.execute("INSERT INTO games(id,canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,n_sources,n_kinds,sources_summary,wanted) "
                    "VALUES(?,?,?,'pc',?,?,?,1,0,'steam',0)",
                    (i, title, nk, nk + "@pc", nk, "title:" + nk))
    lib.execute("INSERT INTO game_attributes VALUES(2,'genres','Utilities')")
    lib.execute("INSERT INTO game_attributes VALUES(3,'genre_ids','57')")
    lib.commit()
    lib.close()

    sco = sqlite3.connect(os.path.join(D, "scores.sqlite"))
    sco.execute("CREATE TABLE IF NOT EXISTS steam_type("
                "norm_key TEXT PRIMARY KEY, type TEXT, updated REAL)")
    sco.execute("INSERT INTO steam_type VALUES('tool by steam type','application',0)")
    sco.execute("INSERT INTO steam_type VALUES('game steam calls a tool','tool',0)")
    sco.commit()
    sco.close()

    # the manual override is the user's word and outranks Steam in BOTH directions
    overrides.set_override("game steam calls a tool", "content_type", "Game")

    got = set(aimeta.targets("all", limit=100))
    check("a real game is still scanned", "a real game" in got)
    check("genre NAME 'Utilities' is not scanned",
          "benchmark by genre name" not in got)
    check("genre ID 57 is not scanned (survives a localised catalog)",
          "benchmark by genre id" not in got)
    check("steam type 'application' is not scanned",
          "tool by steam type" not in got)
    check("a manual content_type='Game' override rescues it",
          "game steam calls a tool" in got)

    # every target mode, not just 'all' — the Lite import scans 'unmatched'
    unm = set(aimeta.targets("unmatched", limit=100))
    check("'unmatched' excludes non-games too",
          "a real game" in unm and "benchmark by genre id" not in unm)
    mis = set(aimeta.targets("missing", limit=100))
    check("'missing' excludes non-games too",
          "a real game" in mis and "benchmark by genre name" not in mis)

    # the count drives the pre-run estimate and the progress denominator, so it has to
    # agree with the selection — a count that includes rows the scan will skip reads as
    # a scan that stalled
    check("target_count agrees with targets()",
          aimeta.target_count("all") == len(aimeta.targets("all", limit=100)))

    # Algo's refusals are the other way in. A non-game refused an identity is still a
    # non-game; nothing is bought by asking the model about it.
    lib = sqlite3.connect(aimeta.LIBRARY_DB)
    lib.execute("CREATE TABLE IF NOT EXISTS identity_review("
                "norm_key TEXT, reason TEXT, detail TEXT)")
    lib.execute("INSERT INTO identity_review VALUES('a real game','x','d')")
    lib.execute("INSERT INTO identity_review VALUES('benchmark by genre id','x','d')")
    lib.commit()
    lib.close()
    rev = set(aimeta.review_targets(limit=100))
    check("review_targets keeps a real refusal", "a real game" in rev)
    check("review_targets excludes non-games", "benchmark by genre id" not in rev)

    print("\n  %d/%d passed" % (sum(1 for _, c in PASS if c), len(PASS)))


if __name__ == "__main__":
    main()
