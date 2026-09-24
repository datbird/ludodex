#!/usr/bin/env python3
"""`%` and `_` in a search are text, not SQL wildcards.

Found by the live browser suite (tests/browser/ui_full.py): searching `%` returned the
whole library, and `_` matched any title with at least one character. The search box
and every field of the query language (title:, platform:, source:, tag:, genre: and the
rest) put the user's text straight into `LIKE '%<text>%'`, so LIKE's own metacharacters
kept their meaning. The count that came back was a plausible number that had nothing to
do with what was typed, which is why nothing had noticed.

The fix is ONE helper, `_like_contains`, paired with `LIKE ? ESCAPE '\\'` everywhere a
user's text reaches a LIKE. This drives the real `_query_games` over a small catalog,
and also pins the behaviour that is meant to stay: `platform:switch` is a CONTAINS
match, so it still finds Switch 2.

Offline: an isolated data dir and the empty catalog the server seeds on first run.
"""
import os
import re
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-like-escape-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402

PASS = []


def check(label, cond, detail=None):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: %s%s" % (label, "" if detail is None else "  (%r)" % (detail,)))


# title, platform, source, tag, genre
GAMES = [
    ("100% Orange Juice", "pc", "steam", "party", "Board"),
    ("Snake_Pass", "pc", "steam", "cozy_pick", "Puzzle"),
    ("Back\\Slash", "pc", "gog", "odd", "Action"),
    ("Doom", "pc", "steam", "fps", "Shooter"),
    ("Mario Kart World", "switch 2", "nintendo", "racing", "Racing"),
    ("Zelda", "switch", "nintendo", "adventure", "Adventure"),
]


def seed():
    lc = sqlite3.connect(app.LIBRARY_DB)
    for i, (title, plat, src, tag, genre) in enumerate(GAMES, 1):
        nk = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        lc.execute("INSERT INTO games(id,canonical_title,norm_key,platform,entry_key,"
                   "base_key,game_key,n_sources,n_kinds,sources_summary,wanted) "
                   "VALUES(?,?,?,?,?,?,?,1,0,?,0)",
                   (i, title, nk, plat, "%s@%s" % (nk, plat), nk, "igdb:%d" % i, src))
        lc.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw) "
                   "VALUES(?,?,?,?,?)", (i, src, plat, str(i), title))
        lc.execute("INSERT INTO metadata_links(game_id,provider,provider_id) "
                   "VALUES(?,'igdb',?)", (i, str(i)))
        lc.execute("INSERT INTO game_tags(game_id,tag,origin) VALUES(?,?,'user')",
                   (i, tag))
        lc.execute("INSERT INTO game_attributes(game_id,kind,value) VALUES(?,'genres',?)",
                   (i, genre))
    lc.commit()
    lc.close()


def titles(**kw):
    con = app.lib()
    try:
        res = app._query_games(con, limit=100, **kw)
    finally:
        con.close()
    return sorted(it["title"] for it in res["items"])


def main():
    seed()
    everything = titles()
    check("the fixture is all there", len(everything) == len(GAMES), everything)

    print("1. the search box")
    check("`%` finds only the title containing a percent sign",
          titles(q="%") == ["100% Orange Juice"], titles(q="%"))
    check("`_` finds only the title containing an underscore",
          titles(q="_") == ["Snake_Pass"], titles(q="_"))
    check("`%%` finds nothing (no title has two in a row)", titles(q="%%") == [])
    check("a backslash is literal too, not an escape of its own",
          titles(q="k\\S") == ["Back\\Slash"], titles(q="k\\S"))
    check("plain text still matches anywhere in the title",
          titles(q="ang") == ["100% Orange Juice"])

    print("2. every LIKE field of the query language")
    check("bare word `%`", titles(query="%") == ["100% Orange Juice"], titles(query="%"))
    check("title:_", titles(query="title:_") == ["Snake_Pass"], titles(query="title:_"))
    check("platform:% matches nothing", titles(query="platform:%") == [])
    check("platform:_ matches nothing", titles(query="platform:_") == [])
    check("source:% matches nothing", titles(query="source:%") == [])
    check("tag:_ matches only the tag with an underscore",
          titles(query="tag:_") == ["Snake_Pass"], titles(query="tag:_"))
    check("genre:% matches nothing", titles(query="genre:%") == [])
    check("an unknown field is matched literally in the title",
          titles(query="nope:%") == [])
    check("a negated `-%` excludes only the percent title",
          titles(query="-%") == sorted(t for t in everything if "%" not in t))

    print("3. what is meant to stay a contains match")
    check("platform:switch still finds Switch 2 (confirmed intended)",
          titles(query="platform:switch") == ["Mario Kart World", "Zelda"],
          titles(query="platform:switch"))
    check("source:nin is a partial match", titles(query="source:nin")
          == ["Mario Kart World", "Zelda"])

    print("4. one helper, no hand-built LIKE patterns left")
    src = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read()
    check("no user-fed `LIKE ?` without the ESCAPE clause",
          not re.search(r"LIKE \?(?! ESCAPE)", src))
    check("no hand-built '%%%s%%' pattern", '"%%%s%%"' not in src)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
