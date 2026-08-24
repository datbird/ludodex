#!/usr/bin/env python3
"""A row the server adds at runtime has to be as addressable as one the build wrote.

`build_library` gives every catalog row four keys: `platform`, `entry_key`
(`<base>@<platform>`), `base_key` and `game_key`. They are not decoration. `entry_key` is
the id the frontend uses to open a card, `base_key` is what collection credit, add-ons
and "also owned on" join through, and `game_key` is what the neutral-art gate matches.

Two endpoints insert into `games` directly, and neither wrote any of them:

  * `_insert_source_row` (manually adding a game). Its docstring says the game "appears
    immediately"; it appears with `entry_key: null`, so the card cannot be opened and the
    neutral-art join `md.game_key = g.game_key` can never match. It stays that way until
    someone happens to run a full rebuild.
  * `_apply_ownership_live`'s want-only branch (marking a game wanted before it is in the
    catalog), the same way.

The keys are cheap and derivable, so the honest fix is to write them rather than to leave
a half-formed row and hope for a rebuild.

Separately, `_provider_match_state` compared a provider id to an integer
(`(r[0] or 0) > 0`). Three providers store slugs, not numbers, so for any game with a
MobyGames, ArcadeDB or ZXInfo row that raised TypeError, which the caller swallowed into
a review card with no facts on it at all.

Offline. No network.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-live-rows-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402
import ownership                                               # noqa: E402
import provider_ids                                            # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def row(nk):
    con = sqlite3.connect(app.LIBRARY_DB)
    con.row_factory = sqlite3.Row
    try:
        r = con.execute("SELECT * FROM games WHERE norm_key=?", (nk,)).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def addressable(r, nk, plat):
    return (r and r.get("platform") == plat
            and r.get("entry_key") == "%s@%s" % (nk, plat)
            and r.get("base_key") == nk
            and (r.get("game_key") or "").startswith("title:"))


def main():
    print("a runtime row carries the same keys the build writes")

    # ---- manually adding a game ---------------------------------------------- #
    app._insert_source_row("sonic mania", "Sonic Mania", "emulation", "switch")
    r = row("sonic mania")
    check("the manually added game exists", r is not None)
    check("and it can be addressed like any other row",
          addressable(r, "sonic mania", "switch"))

    # ---- marking a not-yet-cataloged game as wanted --------------------------- #
    ownership.set_fact(DATA, "celeste", "Celeste", "rom", "switch", "want")
    app._apply_ownership_live("celeste", title="Celeste")
    r = row("celeste")
    check("the want-only row exists", r is not None and r.get("wanted") == 1)
    check("and it is addressable too", addressable(r, "celeste", "switch"))

    # ---- a provider that ids by slug, not by number --------------------------- #
    cache = os.path.join(DATA, "metadata-cache.sqlite")
    con = sqlite3.connect(cache)
    provider_ids.ensure_tables(con)
    con.close()
    con = sqlite3.connect(cache)
    provider_ids.record(con, "mobygames", "sonic mania", "sonic-mania",
                        name="Sonic Mania")
    con.commit()
    con.close()

    state = app._provider_match_state("sonic mania")
    check("the review page still reports provider state",
          any(state.get(k) for k in ("matched", "missed", "unattempted")))
    check("and the slug-id provider is reported as matched, not as a crash",
          any(m.get("provider") == "mobygames" for m in state["matched"]))

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
