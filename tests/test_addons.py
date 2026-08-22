#!/usr/bin/env python3
"""An add-on is content for a game, not a game you own.

datbird, 2026-08-22:

  "Dlc/extention/addons are/should be an array attribute of an owner."
  "those entries should be clickable, show its release date description meta data/media."

Those two together decide the storage. A clickable add-on with its own year, description
and art needs identity resolution, provider matching, attribute merge, media fetch, shape
measurement, selection and a detail page. A separate store would mean a SECOND copy of
every one of those, which is the duplication the 2026-08-21 audit was about.

So an add-on stays a full row in `games` and simply leaves the grid, listed under the game
it extends. `parent_key` does the hiding; `content_kind` records what it is.

THE CASE THAT SHAPES THE RULE, measured on the live library. Of 37 owned add-on-ish
entries, 22 are IGDB `standalone_expansion` (game_type 4). A standalone expansion runs
WITHOUT the base game, and both of datbird's Quake II Mission Packs are owned while
Quake II is NOT. Filing those under a parent would take them out of the grid and put them
nowhere. So only types 1 (dlc_addon) and 2 (expansion) are ever filed, and even then only
when the parent is owned.

This test drives the real `build_library` selection logic over a fixture catalog rather
than restating it, so it fails if the rule moves.
"""
import ast
import os
import re
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))
import test_support                              # noqa: E402
test_support.isolate("ludodex-addons-")

BL = open(os.path.join(DIR, "ludodex", "build_library.py"), encoding="utf-8").read()
APP = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read()


def check(label, cond):
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def body_of(src, name):
    m = re.search(r"^def %s\(.*?(?=^def |\Z)" % re.escape(name), src, re.S | re.M)
    return m.group(0) if m else ""


def main():
    print("1. only true add-on types are harvested")
    fn = body_of(BL, "_igdb_addon_parents")
    check("_igdb_addon_parents exists", bool(fn))
    check("requires a parent_game", "parent_game" in fn)
    # Which TYPES are accepted is proven behaviourally in 1b, not by grepping for a
    # literal. The first version of this test matched a Python tuple "(1, 2)"; the
    # harvester now expresses the same rule as SQL "IN (1,2)", and a text check that
    # breaks on a rewrite of correct code is a check that teaches you to delete it.
    # THE BUG THIS EXISTS TO CATCH. The first version read igdb_meta from
    # metadata-cache.sqlite. Those payloads come from igdb.GAME_FIELDS, which did not
    # request parent_game, so every cached record had a type and no parent and the
    # harvest found ZERO add-ons in a library holding fifteen. The mirror stores
    # parent_game as a COLUMN. A text assertion said "reads the cached records" and
    # passed while the feature did nothing, so this is now behavioural.
    # Check what the code EXECUTES, not the whole function: the docstring names
    # igdb_meta on purpose, to explain the bug it must never repeat.
    _stmts = fn.split('"""')[-1]        # everything after the docstring
    _stmts = "\n".join(l for l in _stmts.splitlines()
                       if not l.strip().startswith("#"))
    check("opens the MIRROR, which is where parent_game lives",
          "igdb-catalog.sqlite" in _stmts)
    check("does not query igdb_meta, which never carries a parent",
          "igdb_meta" not in _stmts)
    check("igdb.GAME_FIELDS asks for parent_game too, for freshly fetched records",
          "parent_game" in open(os.path.join(DIR, "ludodex", "igdb.py"),
                                encoding="utf-8").read())

    print("1b. and it actually finds them, against a fixture mirror")
    import sqlite3
    fix = os.path.join(os.environ["LUDODEX_DATA"], "igdb-catalog.sqlite")
    c = sqlite3.connect(fix)
    c.execute("CREATE TABLE games(id INTEGER PRIMARY KEY, name TEXT, game_type INT, "
              "parent_game INT)")
    c.executemany("INSERT INTO games(id,name,game_type,parent_game) VALUES(?,?,?,?)", [
        (10, "Doom 3", 0, None),                       # a game
        (11, "Resurrection of Evil", 2, 10),           # expansion    -> filed
        (12, "Some Cosmetic Pack", 1, 10),             # dlc_addon    -> filed
        (13, "Quake II Mission Pack", 4, 20),          # STANDALONE   -> never filed
        (14, "Orphan Expansion", 2, None),             # no parent    -> not harvested
    ])
    c.commit()
    c.close()
    ns = {"os": os, "sqlite3": sqlite3, "DATA": os.environ["LUDODEX_DATA"]}
    exec(compile(fn, "<harvester>", "exec"), ns)       # noqa: S102 — the real function
    got = ns["_igdb_addon_parents"]()
    check("expansion harvested", got.get(11) == ("expansion", 10))
    check("dlc_addon harvested", got.get(12) == ("dlc", 10))
    check("standalone_expansion NEVER harvested", 13 not in got)
    check("no parent means nothing to file", 14 not in got)
    check("a plain game is not add-on content", 10 not in got)

    print("2. the parent must be OWNED, or the add-on keeps its place in the grid")
    check("_ADDON is built by inverting the owned id map", "_by_igdb" in BL)
    check("parent_key is None when the parent is not owned",
          re.search(r"_ADDON\[_nk\]\s*=\s*\(_kind,\s*_pnk if _pnk and _pnk != _nk else None\)",
                    BL) is not None)
    check("an entry is never its own parent", "_pnk != _nk" in BL)

    print("3. the columns exist and are written")
    check("games.parent_key declared", re.search(r"parent_key TEXT", BL) is not None)
    check("games.content_kind declared", re.search(r"content_kind TEXT", BL) is not None)
    ins = re.search(r"INSERT INTO games\(canonical_title.*?content_kind,parent_key\)", BL, re.S)
    check("the owned-games insert writes both", ins is not None)
    check("content_kind is set even with no owned parent",
          "(_ADDON.get(base) or (None, None))[0]" in BL)

    print("4. add-ons leave the grid, and only when they have a parent")
    check("the list query filters parent_key IS NULL",
          "g.parent_key IS NULL" in APP)
    check("guarded on the column existing, for an older catalog",
          re.search(r'_has_col\(con, "games", "parent_key"\)', APP) is not None)
    check("status='all' still shows them",
          re.search(r'if status != "all" and _has_col\(con, "games", "parent_key"\)', APP)
          is not None)

    print("5. the detail page can link both ways")
    det = body_of(APP, "game_detail")
    check("detail returns an addons array", '"addons": addons' in det)
    check("each add-on carries an entry_key, so it is clickable",
          '"entry_key": row["entry_key"]' in det)
    check("detail says what THIS entry is", '"content_kind"' in det)
    check("an add-on links back to the game it extends", '"extends": parent_of' in det)
    check("the parent lookup skips add-ons", "parent_key IS NULL LIMIT 1" in det)

    print("test_addons: all checks passed")


if __name__ == "__main__":
    main()
