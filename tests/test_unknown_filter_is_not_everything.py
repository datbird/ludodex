#!/usr/bin/env python3
"""A filter token nobody recognises must never quietly mean "everything".

`_fexpr` turns one filter token into SQL and returned `(None, None)` for anything it did
not recognise. Both loops that consume it do `if e:`, so an unknown token was DROPPED and
the query ran with one fewer condition than the caller asked for.

In the library grid that shows too many games, which is at least visible. In publish it
is not: `_rule_entries` runs the same query path, so a device rule saved as `sytem:snes`
matched every entry in the catalog, up to the 20,000 cap, and `publish_effective` /
`publish_plan` then read that as "this device should hold all of it". The user typed a
selection and got a whole-library copy.

An empty expression is the same hazard with no typo needed: no tokens means no WHERE
clause means every game.

So: an unknown token is an error, not a silent widening; and a rule is refused at save
time as well as at evaluation time, because a rule that cannot be evaluated safely should
never reach the ledger in the first place. A token that is understood but matches nothing
(a device with no marks) still legitimately returns no rows.

Offline. No network.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-filter-guard-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def seed():
    """A catalog just big enough to tell "all of it" from "some of it"."""
    p = os.path.join(DATA, "game-library.sqlite")
    if os.path.exists(p):
        os.remove(p)                 # importing the app creates a real one; start clean
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE games(id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
          platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, n_sources INT,
          n_kinds INT, sources_summary TEXT, has_emulation INT DEFAULT 0,
          has_steam INT DEFAULT 0, has_gog INT DEFAULT 0, has_epic INT DEFAULT 0,
          has_itch INT DEFAULT 0, has_archive INT DEFAULT 0, in_playnite INT DEFAULT 0,
          in_launchbox INT DEFAULT 0, wanted INT DEFAULT 0);
        CREATE TABLE sources(game_id INT, source TEXT, platform TEXT, source_id TEXT,
          title_raw TEXT, detail TEXT, state TEXT DEFAULT 'have', via_collection TEXT);
        CREATE TABLE metadata_links(game_id INT, provider TEXT, provider_id TEXT, url TEXT);
        CREATE TABLE game_attributes(game_id INT, kind TEXT, value TEXT);
        CREATE TABLE game_tags(game_id INT, tag TEXT, origin TEXT);
        CREATE TABLE wanted(game_id INT, store TEXT, store_id TEXT, title_raw TEXT);
    """)
    for i, (title, plat, src) in enumerate([
            ("Sonic the Hedgehog", "genesis", "emulation"),
            ("Super Mario World", "snes", "emulation"),
            ("Chrono Trigger", "snes", "emulation"),
            ("Celeste", "pc", "steam")], start=1):
        nk = title.lower()
        con.execute("INSERT INTO games(id,canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,n_sources,n_kinds,sources_summary,has_emulation) "
                    "VALUES(?,?,?,?,?,?,?,1,1,?,?)",
                    (i, title, nk, plat, "%s@%s" % (nk, plat), nk, "title:" + nk, src,
                     1 if src == "emulation" else 0))
        con.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw) "
                    "VALUES(?,?,?,?,?)", (i, src, plat, str(i), title))
        # identified, because _rule_entries queries with the grid's default
        con.execute("INSERT INTO metadata_links(game_id,provider,provider_id,url) "
                    "VALUES(?,'igdb',?,'')", (i, str(1000 + i)))
    con.commit()
    con.close()
    return p


def main():
    print("an unrecognised filter is an error, not everything")
    lib = seed()
    app.LIBRARY_DB = lib

    con = app.lib()
    try:
        total = _count(con, [])
        check("the fixture catalog has four games", total == 4)
        check("a real filter narrows it", _count(con, ["system:snes"]) == 2)

        # ---- the typo ---------------------------------------------------------- #
        raised = None
        try:
            app._query_games(con, include=["sytem:snes"], limit=20000)
        except Exception as e:                                  # noqa: BLE001
            raised = e
        check("a misspelt token is refused", raised is not None)
        check("and the message names the token it could not read",
              raised is not None and "sytem:snes" in str(raised))

        # ---- what publish would have copied ------------------------------------ #
        raised = None
        try:
            app._rule_entries(con, "sytem:snes")
        except Exception as e:                                  # noqa: BLE001
            raised = e
        check("a device rule with that typo does not resolve to the whole library",
              raised is not None)

        raised = None
        try:
            app._rule_entries(con, "   ")
        except Exception as e:                                  # noqa: BLE001
            raised = e
        check("nor does an empty rule", raised is not None)

        # ---- a rule that IS understood still works ----------------------------- #
        keys = app._rule_entries(con, "system:snes")
        check("a good rule still selects exactly its games", len(keys) == 2)

        # ---- understood but matching nothing is not an error -------------------- #
        # 'this device has no marks' is a real answer, and must stay one.
        check("a token that legitimately matches nothing returns no rows",
              _count(con, ["wanted:999"]) == 0)
    finally:
        con.close()

    print("\nRESULT: %d checks, all passed" % len(PASS))


def _count(con, include):
    return app._query_games(con, include=include, limit=1000,
                            identified="all")["total"]


if __name__ == "__main__":
    main()
