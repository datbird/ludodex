#!/usr/bin/env python3
"""What the STORE says a product is, for every store, in one table.

Live 2026-08-26: "Cyberpunk 2077 Digital Goodies" sat in the library as a game. It has
no provider match, so the add-on filter (which reads IGDB's type) never saw it, and AI
enrichment then gave it Cyberpunk 2077's year, genres, developer and description. A GOG
bonus pack was wearing the game's metadata.

GOG DOES SAY. Probed against the live account: `isGame` is TRUE for it, so that flag is
useless, but `category` is EMPTY for it and set for every real game. Exactly 1 of 60
owned products had an empty category, and it was that one. `gog_owned.py` kept only the
id and the title, discarding 21 other fields including that one.

The table was `steam_type`, which is the right mechanism with the wrong name: the
question is "what does the STORE say this is", and Steam is not the only store. It is
`store_type(norm_key, source, type)` now, so there is ONE answer to that question rather
than one per shop.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond, detail=""):
    PASS.append((label, bool(cond)))
    print("  %s   %s%s" % ("ok " if cond else "FAIL", label,
                           "" if cond else "   <- " + str(detail)[:200]))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import nongame
    import gog_owned

    # --- 1. GOG's own field is captured, not discarded ------------------------------
    rows = gog_owned.rows_from_products([
        {"id": 1207666073, "title": "Akalabeth: World of Doom", "category": "Role-playing"},
        {"id": 1548764757, "title": "Cyberpunk 2077 Digital Goodies", "category": ""},
        {"id": 42, "title": "No Category Field At All"},
    ])
    check("a product keeps its category", rows[0] == (1207666073, "Akalabeth: World of Doom", "Role-playing"), rows[0])
    check("an EMPTY category survives as empty",
          rows[1] == (1548764757, "Cyberpunk 2077 Digital Goodies", ""), rows[1])
    check("a missing field is empty, not a crash", rows[2][2] == "", rows[2])

    check("an empty category means NOT A GAME",
          gog_owned.store_type("") == nongame.STORE_EXTRA)
    check("a real category means nothing is claimed",
          gog_owned.store_type("Role-playing") is None)
    check("whitespace is empty too", gog_owned.store_type("   ") == nongame.STORE_EXTRA)

    # --- 2. one table answers "what does the store say", for every store -------------
    check("the non-game types include the store's extras",
          nongame.STORE_EXTRA in nongame.NON_GAME_TYPES, nongame.NON_GAME_TYPES)
    expr, args = nongame.hidden_sql()
    check("the rule reads store_type", "store_type" in expr, expr[:200])
    check("and no longer reads a Steam-only table", "steam_type" not in expr)

    # --- 3. it actually hides the thing, and only the thing -------------------------
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT, canonical_title TEXT);
    CREATE TABLE game_attributes(game_id INTEGER, kind TEXT, value TEXT);
    INSERT INTO games VALUES(1,'cyberpunk 2077 digital goodies','Cyberpunk 2077 Digital Goodies');
    INSERT INTO games VALUES(2,'cyberpunk 2077','Cyberpunk 2077');
    INSERT INTO games VALUES(3,'wallpaper engine','Wallpaper Engine');
    """)
    con.execute("ATTACH DATABASE ':memory:' AS ov")
    con.execute("ATTACH DATABASE ':memory:' AS sco")
    con.executescript("""
    CREATE TABLE ov.overrides(norm_key TEXT, kind TEXT, value TEXT);
    CREATE TABLE sco.store_type(norm_key TEXT, source TEXT, type TEXT, updated REAL);
    INSERT INTO sco.store_type VALUES('cyberpunk 2077 digital goodies','gog','extra',0);
    INSERT INTO sco.store_type VALUES('wallpaper engine','steam','application',0);
    """)
    con.commit()
    hidden = {r[0] for r in con.execute(
        "SELECT g.norm_key FROM games g WHERE " + expr, args)}
    check("the GOG extra is hidden", "cyberpunk 2077 digital goodies" in hidden, hidden)
    check("Steam's own verdict still works", "wallpaper engine" in hidden, hidden)
    check("THE REAL GAME IS NOT HIDDEN", "cyberpunk 2077" not in hidden, hidden)

    # a manual override still beats the store, in both directions
    con.execute("INSERT INTO ov.overrides VALUES('cyberpunk 2077 digital goodies','content_type','Game')")
    con.commit()
    hidden2 = {r[0] for r in con.execute(
        "SELECT g.norm_key FROM games g WHERE " + expr, args)}
    check("a manual 'Game' rescues it from the store's verdict",
          "cyberpunk 2077 digital goodies" not in hidden2, hidden2)

    # --- 4. a live install carries its rows over, it does not lose them --------------
    # On DISK and REOPENED, because the first version of this passed in memory while the
    # server showed an empty table beside 2114 steam_type rows. The CREATE self-commits;
    # the INSERT does not, so closing without a commit rolled every row back.
    dbp = os.path.join(os.environ["LUDODEX_DATA"], "carryover.sqlite")
    w = sqlite3.connect(dbp)
    w.execute("CREATE TABLE steam_type(norm_key TEXT PRIMARY KEY, type TEXT, updated REAL)")
    w.execute("INSERT INTO steam_type VALUES('3dmark','application',7)")
    w.commit()
    nongame.ensure_store_type(w)
    w.close()                                   # <- where the rows used to disappear
    re = sqlite3.connect(dbp)
    kept = re.execute("SELECT norm_key,source,type FROM store_type").fetchall()
    re.close()
    check("the carried-over rows SURVIVE the connection closing",
          kept == [("3dmark", "steam", "application")], kept)

    live = sqlite3.connect(":memory:")
    live.execute("CREATE TABLE steam_type(norm_key TEXT PRIMARY KEY, type TEXT, updated REAL)")
    live.execute("INSERT INTO steam_type VALUES('3dmark','application',7)")
    nongame.ensure_store_type(live)
    got = live.execute("SELECT norm_key,source,type,updated FROM store_type").fetchall()
    check("the existing Steam verdicts survive the rename",
          got == [("3dmark", "steam", "application", 7.0)], got)
    nongame.ensure_store_type(live)
    check("and running it again changes nothing",
          live.execute("SELECT COUNT(*) FROM store_type").fetchone()[0] == 1)
    fresh = sqlite3.connect(":memory:")
    nongame.ensure_store_type(fresh)
    check("a fresh install with no steam_type is fine, not an error",
          fresh.execute("SELECT COUNT(*) FROM store_type").fetchone()[0] == 0)

    # --- 5. the category did NOT go in the ownership TSV -----------------------------
    # `load_tsv` reads column 3 as the PLATFORM (psn and xbox emit it there). A category
    # written into that column would make every GOG game's platform "Role-playing". This
    # was written, caught before it shipped, and is pinned here so it stays caught.
    src = open(os.path.join(root, "ludodex", "gog_owned.py"), encoding="utf-8").read()
    emit = src[src.index("for gid, title"):]
    check("the ownership TSV stays two columns",
          "config.tsv_row(gid, title)" in emit, emit[:160])
    check("the category goes to the sidecar cache instead",
          "save_meta(" in src and "gog-meta.sqlite" in src)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
