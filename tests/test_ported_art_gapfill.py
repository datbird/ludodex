#!/usr/bin/env python3
"""Two rules that decide whether the art step is proportional, and whether it is right.

`media_fetch.art_less_keys` is the gap-fill SET. The sync's art pass used to be
`fetch_igdb(con, now)` with no scope at all: a whole-catalog refetch on every sync, which
combined with put()'s upsert reset every sha1 and re-downloaded bytes already sitting in
the media repo. Scoping the pass to games that currently have NO art is what makes a
routine sync cost API calls only for newly-imported art-less games. The set has to be
exactly that — a game WITH a cover must not appear in it, or the whole-catalog refetch is
back under a different name.

`_RESMAP` is the identity map media rows are STAMPED with. It is cached because it is
read once per asset, and the cache is real — which is precisely the hazard. As a CLI
script the process ran one pass and exited. The server imports this module and lives for
days, so after a wand pin/detach/re-identify, art fetched for the NEW identity was being
stamped with the STALE game_key, and the media serve-gate then hid the very cover the
wand had just fetched. "The wand says done and the cover doesn't appear" is the failure
the one-operation contract forbids, and `invalidate_resmap()` is the whole fix: it must
be a real cache (so the staleness is reproducible) AND it must actually drop.

The bundle refusal rides in the same map: build_library refuses a bundle/pack record as
an identity, so its games row says `title:<nk>`. Media for it must be stamped the same
way, or the serve-time game_key match fails and the entry loses art it did fetch.

Offline. Real sqlite in a fixture data dir; no network, no provider calls.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-ported-artgap-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import config                                                  # noqa: E402
import media_fetch                                             # noqa: E402
import media_index                                             # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def meta_cache():
    con = sqlite3.connect(media_fetch.META_CACHE)
    con.executescript(
        "CREATE TABLE IF NOT EXISTS igdb_resolution ("
        "  norm_key TEXT PRIMARY KEY, igdb_id INTEGER, slug TEXT,"
        "  matched_by TEXT, resolved_at INTEGER);"
        "CREATE TABLE IF NOT EXISTS igdb_meta ("
        "  igdb_id INTEGER PRIMARY KEY, payload_json TEXT);")
    return con


def library(rows_games, rows_sources, rows_links=()):
    lib = os.path.join(DATA, "game-library.sqlite")
    if os.path.exists(lib):
        os.remove(lib)
    con = sqlite3.connect(lib)
    con.executescript(
        "CREATE TABLE games (id INTEGER PRIMARY KEY, norm_key TEXT,"
        "  canonical_title TEXT);"
        "CREATE TABLE sources (game_id INTEGER, source TEXT, source_id TEXT);"
        "CREATE TABLE metadata_links (game_id INTEGER, provider TEXT);")
    con.executemany("INSERT INTO games VALUES (?,?,?)", rows_games)
    con.executemany("INSERT INTO sources VALUES (?,?,?)", rows_sources)
    con.executemany("INSERT INTO metadata_links VALUES (?,?)", rows_links)
    con.commit()
    con.close()
    config.set_("library_db", lib)
    return lib


def main():
    print("the art gap-fill set, and the identity it stamps")
    test_support.assert_isolated()
    check("media_fetch resolved its metadata cache into the fixture dir",
          os.path.abspath(media_fetch.META_CACHE).startswith(os.path.abspath(DATA)))

    mc = meta_cache()
    mc.execute("INSERT INTO igdb_resolution VALUES ('portal', 71, NULL, 'name', 0)")
    mc.commit()
    media_fetch.invalidate_resmap()

    print()
    print("1. an identified game is stamped with its IGDB identity")
    check("portal -> igdb:71", media_fetch.game_key("portal") == "igdb:71")
    check("an UNresolved game falls back to its title identity",
          media_fetch.game_key("doom") == "title:doom")

    print()
    print("2. the cache is REAL — this is the hazard, not an implementation detail")
    mc.execute("UPDATE igdb_resolution SET igdb_id=14546, matched_by='manual' "
               "WHERE norm_key='portal'")
    mc.commit()
    check("an identity changed underneath a long-running process is NOT seen",
          media_fetch.game_key("portal") == "igdb:71")

    print()
    print("3. invalidate_resmap() is what makes the wand's pin visible")
    media_fetch.invalidate_resmap()
    check("after invalidation the NEW identity is used: %s"
          % media_fetch.game_key("portal"),
          media_fetch.game_key("portal") == "igdb:14546")
    check("and a game that never resolved is unaffected",
          media_fetch.game_key("doom") == "title:doom")

    print()
    print("4. a bundle record is refused as an identity, here as in build_library")
    mc.execute("INSERT INTO igdb_resolution VALUES ('someBundle', 9001, NULL, 'name', 0)")
    mc.execute("INSERT INTO igdb_resolution VALUES ('somePack', 9002, NULL, 'name', 0)")
    mc.execute("INSERT INTO igdb_resolution VALUES ('realGame', 9003, NULL, 'name', 0)")
    mc.executemany("INSERT INTO igdb_meta VALUES (?,?)",
                   [(9001, '{"game_type": 3}'),      # bundle
                    (9002, '{"game_type": 13}'),     # pack
                    (9003, '{"game_type": 0}')])     # an ordinary game
    mc.commit()
    media_fetch.invalidate_resmap()
    check("a bundle keeps the TITLE identity: %s" % media_fetch.game_key("someBundle"),
          media_fetch.game_key("someBundle") == "title:someBundle")
    check("so does a pack", media_fetch.game_key("somePack") == "title:somePack")
    check("but an ordinary game is stamped with its IGDB id",
          media_fetch.game_key("realGame") == "igdb:9003")
    mc.close()

    print()
    print("5. art_less_keys targets the games with NO art, and only those")
    library(rows_games=[(1, "portal", "Portal"), (2, "doom", "Doom"),
                        (3, "hades", "Hades")],
            rows_sources=[(1, "steam", "400"), (2, "steam", "379720"),
                          (3, "gog", "hades")])
    idx = media_index.index_con()
    media_fetch.invalidate_resmap()
    media_fetch.put(idx, "portal", "cover", "igdb", "http://x/portal-cover.jpg", 1)
    idx.commit()
    targets = {t[0] for t in media_fetch.art_less_keys(idx)}
    check("the game with a cover is NOT a target: %s" % sorted(targets),
          "portal" not in targets)
    check("the two art-less games are", targets == {"doom", "hades"})
    check("and each target carries its title and store id, for the lookup",
          sorted((t[0], t[1], t[2]) for t in media_fetch.art_less_keys(idx))
          == [("doom", "Doom", "379720"), ("hades", "Hades", None)])

    print()
    print("6. art means art — a video is not a cover")
    media_fetch.put(idx, "doom", "video", "igdb", "http://x/doom.mp4", 1, ext="mp4")
    idx.commit()
    check("a game with only a video is still art-less",
          "doom" in {t[0] for t in media_fetch.art_less_keys(idx)})
    for kind in ("hero", "logo", "background"):
        media_fetch.put(idx, "hades", kind, "igdb", "http://x/hades-%s.jpg" % kind, 1)
    idx.commit()
    check("any one of cover/hero/logo/background counts as having art",
          "hades" not in {t[0] for t in media_fetch.art_less_keys(idx)})

    print()
    print("7. the caller can narrow what counts as art")
    only_cover = {t[0] for t in media_fetch.art_less_keys(idx, kinds=("cover",))}
    check("asked about covers alone, the hero-only game is art-less again",
          "hades" in only_cover)
    check("while the one with a cover still is not", "portal" not in only_cover)

    print()
    print("8. the set is the IDENTIFIED games, not the whole catalog")
    # An emulation-only row with no metadata link is not something a provider can be
    # asked about by name; including it would make the 'gap-fill' pass unbounded again.
    library(rows_games=[(1, "portal", "Portal"), (2, "unknownrom", "SMW_U"),
                        (3, "linked", "Linked Game")],
            rows_sources=[(1, "steam", "400"), (2, "emulation", "snes"),
                          (3, "emulation", "snes")],
            rows_links=[(3, "igdb")])
    keys = {t[0] for t in media_fetch.art_less_keys(idx)}
    check("an unidentified emulation entry is not a target: %s" % sorted(keys),
          "unknownrom" not in keys)
    check("but an emulation entry WITH a metadata link is", "linked" in keys)
    check("and the store game with a cover is still excluded", "portal" not in keys)

    print()
    print("9. an empty library is an empty target set, not an error")
    library(rows_games=[], rows_sources=[])
    check("nothing to gap-fill", media_fetch.art_less_keys(idx) == [])
    idx.close()

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
