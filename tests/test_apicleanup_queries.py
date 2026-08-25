#!/usr/bin/env python3
"""Query rules that had drifted into several hand-written copies (audit cleanup tier).

Three separate drifts, one theme — a rule written out more than once and nothing keeping
the copies in step:

  * The Apicalypse escape. `_igdb_search` stripped only `"` while `_igdb_by_name` and
    `_igdb_raw_hits` stripped `"`, `\\` and `*`. A title ending in a backslash escaped its
    own closing quote in the one that under-stripped, so the rest of the query became
    part of the string literal.
  * has_cover. `_has_cover_sql` exists to be the ONE definition, and its docstring says
    Spotlight is one of the places it serves — but `_spotlight_rows` still carried a
    sixth inline copy. They agreed by hand, not by construction, and Spotlight RANKS its
    representative row by has_cover, so a drift puts a placeholder at the front.
  * /api/stats. `no_media` looked at the media index only while the filter it links to
    (FLAG_SQL["has_media"]) also counts user uploads, so the number and the list it
    opened disagreed; and `by_source` was the only count on the card with no `wanted=0`
    filter, so wishlist-only titles inflated the per-source totals.

Offline throughout: sqlite fixtures in an isolated data dir, and a stub in place of
IGDB's HTTP client so the query TEXT is what gets asserted.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-apicleanup-q-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


# --------------------------------------------------------------------------- #
#  1. one Apicalypse escape, used by all three IGDB query builders
# --------------------------------------------------------------------------- #
HOSTILE = 'Half-Life 2: "Lost Coast" \\ Mega*Man'


def igdb_bodies():
    """Every Apicalypse body the three builders emit for the hostile title."""
    import igdb
    seen = []

    def fake_query(endpoint, body, cid, tok):
        seen.append(body)
        return []

    real_q, real_tok = igdb.query, app._igdb_token
    igdb.query = fake_query
    app._igdb_token = lambda: ("cid", "tok")
    try:
        app._igdb_search(HOSTILE)
        app._igdb_by_name(HOSTILE)
        app._igdb_raw_hits(HOSTILE)
    finally:
        igdb.query, app._igdb_token = real_q, real_tok
    return seen


def literal_of(body):
    """The text between the FIRST pair of double quotes in an Apicalypse body."""
    a = body.index('"')
    b = body.index('"', a + 1)
    return body[a + 1:b]


def part1():
    print("1. one Apicalypse escape for every IGDB query builder")
    bodies = igdb_bodies()
    check("all three builders were exercised", len(bodies) == 4)   # raw_hits emits two
    for i, body in enumerate(bodies):
        lit = literal_of(body)
        check("body %d: no bare quote inside the literal" % i, '"' not in lit)
        check("body %d: no backslash to escape the closing quote" % i, "\\" not in lit)
        check("body %d: no wildcard smuggled into the literal" % i, "*" not in lit)
        # the literal must actually END where the builder thinks it does
        check("body %d: the clause after the literal survives" % i,
              body[body.index('"', body.index('"') + 1) + 1:].lstrip().startswith(";")
              or body[body.index('"', body.index('"') + 1) + 1:].lstrip().startswith(";"))
    check("every builder produced the SAME escaped title",
          len({literal_of(b) for b in bodies}) == 1)
    check("and it is what _apicalypse_str returns",
          literal_of(bodies[0]) == app._apicalypse_str(HOSTILE))


# --------------------------------------------------------------------------- #
#  2. Spotlight's has_cover IS the shared rule
# --------------------------------------------------------------------------- #
GAMES = [
    # norm_key, platform, game_key      what art it has
    ("own", "genesis", "igdb:1"),       # its own console's cover
    ("foreign", "genesis", "igdb:2"),   # ONLY another console's cover
    ("neutral", "genesis", "igdb:3"),   # neutral art, identity matches
    ("mismatch", "genesis", "igdb:4"),  # neutral art, identity does NOT match
    ("upload", "genesis", "igdb:5"),    # a user upload only
    ("bare", "genesis", "igdb:6"),      # nothing at all
]
ART = [("own", "genesis", "igdb:1"), ("foreign", "snes", "igdb:2"),
       ("neutral", "", "igdb:3"), ("mismatch", "", "title:mismatch")]


def seed_catalog():
    lc = sqlite3.connect(app.LIBRARY_DB)
    lc.execute("DELETE FROM games")
    lc.execute("DELETE FROM sources")
    for nk, plat, gk in GAMES:
        lc.execute("INSERT INTO games(canonical_title,norm_key,platform,entry_key,"
                   "base_key,game_key,n_sources,n_kinds,sources_summary,wanted,"
                   "has_emulation,has_steam) VALUES(?,?,?,?,?,?,1,0,'steam',0,0,1)",
                   (nk.title(), nk, plat, "%s@%s" % (nk, plat), nk, gk))
    lc.commit()
    lc.close()
    mc = sqlite3.connect(app.INDEX_DB)
    mc.execute("DELETE FROM media")
    for i, (nk, system, gk) in enumerate(ART):
        mc.execute("INSERT INTO media(norm_key,system,game_key,kind,provider,ref,"
                   "ref_type,ext,chosen) VALUES(?,?,?,'cover','igdb',?,'url','jpg',1)",
                   (nk, system, gk, "ref-%d" % i))
    mc.commit()
    mc.close()
    uc = sqlite3.connect(app.UMEDIA_DB)
    uc.execute("CREATE TABLE IF NOT EXISTS user_media(norm_key TEXT, kind TEXT, "
               "sha1 TEXT, created REAL)")
    uc.execute("DELETE FROM user_media")
    uc.execute("INSERT INTO user_media(norm_key,kind,sha1,created) "
               "VALUES('upload','cover','abc',1)")
    uc.commit()
    uc.close()
    sc = sqlite3.connect(app.SCORES_DB)
    sc.execute("CREATE TABLE IF NOT EXISTS game_scores(norm_key TEXT PRIMARY KEY, "
               "universal REAL, critic REAL, user REAL)")
    sc.execute("CREATE TABLE IF NOT EXISTS ratings(norm_key TEXT, source TEXT, "
               "kind TEXT, score REAL, votes INT, raw TEXT)")
    sc.commit()
    sc.close()


COVERED = {"own", "neutral", "upload"}


def part2():
    print("\n2. Spotlight decides has_cover with the shared rule, not a copy")
    seed_catalog()
    con = app.lib()
    try:
        rows = app._spotlight_rows(con, None, [], order="gs.universal DESC", limit=50)
        spot = {r["norm_key"]: r["has_cover"] for r in rows}
        expr = app._has_cover_sql(True, True)
        grid = {r[0]: bool(r[1]) for r in con.execute(
            "SELECT norm_key, " + expr + " FROM games g")}
    finally:
        con.close()
    check("spotlight returned every seeded game", set(spot) == {g[0] for g in GAMES})
    check("its has_cover matches the shared rule for every game", spot == grid)
    check("and the shared rule is the one we mean", {k for k, v in grid.items() if v} == COVERED)
    check("a game with only ANOTHER console's cover is not covered", not spot["foreign"])
    check("neutral art whose identity does not match is not covered", not spot["mismatch"])
    check("a user upload alone IS covered", spot["upload"])


# --------------------------------------------------------------------------- #
#  3. /api/stats agrees with the filters its numbers link to
# --------------------------------------------------------------------------- #
def part3():
    print("\n3. /api/stats counts what its own filters count")
    seed_catalog()
    # a wishlist-only Steam title: owned counts must not see it
    lc = sqlite3.connect(app.LIBRARY_DB)
    lc.execute("INSERT INTO games(canonical_title,norm_key,platform,entry_key,base_key,"
               "game_key,n_sources,n_kinds,sources_summary,wanted,has_emulation,has_steam)"
               " VALUES('Wished','wished','pc','wished@pc','wished','title:wished',1,0,"
               "'steam',1,0,1)")
    gid = lc.execute("SELECT id FROM games WHERE norm_key='wished'").fetchone()[0]
    lc.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw,state) "
               "VALUES(?,'steam','pc','99','Wished','want')", (gid,))
    for nk in [g[0] for g in GAMES]:
        _g = lc.execute("SELECT id FROM games WHERE norm_key=?", (nk,)).fetchone()[0]
        lc.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw,state)"
                   " VALUES(?,'steam','pc','1','x','have')", (_g,))
    lc.commit()
    lc.close()

    st = app.stats()
    check("the wishlist title is counted as wanted", st["wanted"] == 1)
    check("by_source counts OWNED steam entries only, not the wishlist one",
          st["by_source"]["steam"] == len(GAMES))
    con = app.lib()
    try:
        owned_dyn = con.execute(
            "SELECT COUNT(DISTINCT s.game_id) FROM sources s JOIN games g "
            "ON g.id=s.game_id WHERE s.source='steam' AND g.wanted=0").fetchone()[0]
        # the number the card links to: NOT has_media, over owned games
        filtered = con.execute(
            "SELECT COUNT(*) FROM games g WHERE NOT " + app.FLAG_SQL["has_media"]
            + " AND g.wanted=0").fetchone()[0]
    finally:
        con.close()
    check("the dynamic-source pass agrees with the column pass",
          owned_dyn == st["by_source"]["steam"])
    check("no_media is exactly the negation of the has_media filter",
          st["no_media"] == filtered)
    # 'upload' has ONLY a user upload; the old no_media (media index only) counted it
    check("a game whose only media is a user upload is not counted as media-less",
          st["no_media"] == len([g for g in GAMES if g[0] not in
                                 {"own", "foreign", "neutral", "mismatch", "upload"}]))


def main():
    print("audit cleanup: one query rule per rule")
    part1()
    part2()
    part3()
    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
