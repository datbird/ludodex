#!/usr/bin/env python3
"""Language must never decide whether a rule fires (#26).

Steam's appdetails localises by the requesting IP unless `l=` is pinned, and live this
instance was being served Brazilian Portuguese: 3DMark arrived with a pt-BR description
and the genre "Utilitários". The description being localised is cosmetic. The GENRE
being localised is not — `NON_GAME_GENRES` is an English vocabulary, so "Utilitários"
matched nothing and every benchmark, wallpaper tool and video player stayed visible no
matter how `hide_non_games` was set. A rule silently stopped firing because of where the
server's packets came out.

Pinning `l=english` fixes the symptom and creates a second bug: it hardcodes a language
beside a user-defined language preference that already exists (`media_languages`), which
is the same "two places decide one thing" failure this codebase keeps paying for.

The real fix is that rules must not match localised text at all. Steam hands us a
language-independent genre id in the same object as the localised name — verified live,
3DMark is id 57 whether it renders "Utilities", "Utilitários" or "Werkzeuge" — and we
were throwing it away. Match the id, and the display language becomes free to follow the
user's preference without touching what the filter sees.

Offline. No network.
"""
import os
import sqlite3
import sys

import test_support

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


# Real appdetails shapes for 3DMark (appid 223850), captured live 2026-08-04.
EN = {"name": "3DMark", "type": "game",
      "genres": [{"id": "57", "description": "Utilities"}],
      "short_description": "3DMark is for gamers, overclockers and system builders"}
PT = {"name": "3DMark", "type": "game",
      "genres": [{"id": "57", "description": "Utilitários"}],
      "short_description": "O 3DMark é para gamers, overclockers e desenvolvedores"}
DE = {"name": "3DMark", "type": "game",
      "genres": [{"id": "57", "description": "Werkzeuge"}],
      "short_description": "3DMark ist für Spieler, Übertakter und Systembauer"}


def main():
    d = test_support.isolate("ludodex-genrelang-")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "ludodex"))
    import config
    import media_fetch
    from server import app as srv

    # ---- 1. the id survives extraction -------------------------------------------
    en = media_fetch._extract_steam_attrs(EN)
    check("extract keeps the genre id", en.get("genre_ids") == ["57"])
    check("extract still keeps the genre name", en.get("genres") == ["Utilities"])

    pt = media_fetch._extract_steam_attrs(PT)
    de = media_fetch._extract_steam_attrs(DE)
    check("genre id is identical across languages",
          pt.get("genre_ids") == de.get("genre_ids") == en.get("genre_ids"))
    check("genre NAME differs across languages (the thing we must not match on)",
          pt.get("genres") != en.get("genres") and de.get("genres") != en.get("genres"))
    check("description follows the fetch language",
          "Utilitários" not in (pt.get("description") or "")
          and pt.get("description") != en.get("description"))

    # a payload with no genres at all must not invent an empty kind
    check("no genres -> no genre_ids key",
          "genre_ids" not in media_fetch._extract_steam_attrs({"name": "x"}))
    check("genre with no id is skipped, name still kept",
          media_fetch._extract_steam_attrs(
              {"genres": [{"description": "Action"}]}).get("genre_ids") is None)

    # ---- 2. the two vocabularies cannot drift ------------------------------------
    # The failure mode this whole session has been about: a second copy of a rule that
    # nobody updates. The id map is keyed BY the English name so drift is detectable.
    unknown = [k for k in srv.STEAM_GENRE_IDS if k not in srv.NON_GAME_GENRES]
    check("every mapped genre id names a real NON_GAME_GENRES entry (got %r)" % unknown,
          not unknown)
    check("ids are the values actually used by the filter",
          set(srv.NON_GAME_GENRE_IDS) == set(srv.STEAM_GENRE_IDS.values()))
    check("utilities is mapped to Steam's id 57",
          srv.STEAM_GENRE_IDS.get("utilities") == "57")
    check("ids are strings, matching how game_attributes stores values",
          all(isinstance(v, str) for v in srv.STEAM_GENRE_IDS.values()))

    # ---- 3. the filter fires on the id, in any language --------------------------
    con = sqlite3.connect(":memory:")
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT, canonical_title TEXT);
    CREATE TABLE game_attributes(game_id INT, kind TEXT, value TEXT);
    """)
    ovp = os.path.join(d, "ov.sqlite")
    scp = os.path.join(d, "sco.sqlite")
    for p, ddl in ((ovp, "CREATE TABLE overrides(norm_key TEXT, kind TEXT, value TEXT)"),
                   (scp, "CREATE TABLE steam_type(norm_key TEXT, type TEXT, at INT)")):
        c = sqlite3.connect(p); c.execute(ddl); c.commit(); c.close()
    con.execute("ATTACH DATABASE ? AS ov", (ovp,))
    con.execute("ATTACH DATABASE ? AS sco", (scp,))

    def add(nk, title, genres=(), gids=(), override=None):
        cur = con.execute("INSERT INTO games(norm_key,canonical_title) VALUES(?,?)",
                          (nk, title))
        for g in genres:
            con.execute("INSERT INTO game_attributes VALUES(?,'genres',?)",
                        (cur.lastrowid, g))
        for i in gids:
            con.execute("INSERT INTO game_attributes VALUES(?,'genre_ids',?)",
                        (cur.lastrowid, i))
        if override:
            con.execute("INSERT INTO ov.overrides VALUES(?,'content_type',?)",
                        (nk, override))

    add("3dmark pt", "3DMark (pt-BR ingest)", genres=("Utilitários",), gids=("57",))
    add("3dmark de", "3DMark (de ingest)", genres=("Werkzeuge",), gids=("57",))
    add("3dmark old", "3DMark (pre-fix, English, no ids)", genres=("Utilities",))
    add("doom", "DOOM", genres=("Action",), gids=("1",))
    add("acao", "A Real Game, Localised", genres=("Ação",), gids=("1",))
    add("rescued", "Real Game Tagged Utilities", genres=("Utilitários",), gids=("57",),
        override="Game")
    add("forced", "Game Steam Calls A Game", genres=("Ação",), gids=("1",),
        override="Utility")
    con.commit()

    expr, args = srv._non_game_hidden_sql()
    hid = {r[0] for r in con.execute("SELECT g.norm_key FROM games g WHERE %s" % expr,
                                     args)}

    check("Portuguese genre is hidden via its id", "3dmark pt" in hid)
    check("German genre is hidden via its id", "3dmark de" in hid)
    check("English-only rows keep working (no re-fetch required)", "3dmark old" in hid)
    check("a real game is not hidden", "doom" not in hid)
    check("a localised real game is not hidden", "acao" not in hid)
    check("manual override still rescues", "rescued" not in hid)
    check("manual override still hides", "forced" in hid)

    # ---- 4. the fetch language comes from the ONE user setting -------------------
    config.set_("media_languages", "")
    check("unset preference -> english", "l=english" in media_fetch.appdetails_url(1))
    check("url carries the appid", "appids=1&" in media_fetch.appdetails_url(1))

    config.set_("media_languages", "Portuguese,English")
    u = media_fetch.appdetails_url(223850)
    check("preference drives the language", "l=portuguese" in u)
    check("only the FIRST preference is used", "english" not in u)

    config.set_("media_languages", "Korean")
    check("canonical name maps to Steam's own code (koreana, not korean)",
          "l=koreana" in media_fetch.appdetails_url(1))

    config.set_("media_languages", "Klingon")
    check("an unrecognised language falls back to english, never to unpinned",
          "l=english" in media_fetch.appdetails_url(1))

    config.set_("media_languages", "")
    check("no caller can build an unpinned url",
          "l=" in media_fetch.appdetails_url(1) and "%s" not in media_fetch.appdetails_url(1))

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
