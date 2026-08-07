#!/usr/bin/env python3
"""What counts as NOT a game — one definition, every caller.

This rule started life inside `server/app.py`, where it guarded three read sites: the
library listing, the facet builder and Spotlight. That was the whole of it, so an
entry hidden from the library was still perfectly visible to everything else — and the
AI scan is the caller where that costs money. On 2026-08-07 the metadata area analyzed
3DMark and The Jackbox Megapicker, both Steam genre `Utilities`, both already hidden,
and wrote 3DMark a release year and a description as though it were a game.

Same shape as `matchgate.game_era`: the definition moves to a module both sides import
rather than being restated. `server/app.py` keeps its own names bound to these, so
nothing that referenced `srv.NON_GAME_GENRES` had to change.

The signals, in precedence order:
  1. a manual `content_type` override — the user's word, in BOTH directions (it hides
     something Steam sells as a game, and rescues a real game Steam calls a tool)
  2. Steam's own appdetails `type`
  3. Steam's language-independent genre ID
  4. the genre NAME, for rows written before genre_ids existed and for non-Steam genres
"""
import os
import sqlite3

# Steam appdetails `type`s that aren't games.
NON_GAME_TYPES = ("application", "tool", "music", "video", "hardware", "series", "mod")

# Steam GENRES that only ever belong to software, never to a game. A second, independent
# signal for the same question — needed because `steam_type` is populated only by
# scores_fetch and was EMPTY for the whole library (0 of 2208 rows), which left the
# type-based rule testing membership in an empty table and therefore hiding nothing, ever.
# Genres are already on the entry, cost nothing to consult, and catch the case the type
# signal cannot even in principle: Steam SELLS fpsVR and Wallpaper Engine as `game`, so
# their type is right by Steam's lights and wrong by ours — but their genre says Utilities.
# A manual content_type override still wins over this, so a real game tagged Utilities is
# one click from being rescued.
NON_GAME_GENRES = ("utilities", "software", "software training", "audio production",
                   "video production", "photo editing", "animation & modeling",
                   "design & illustration", "web publishing", "game development",
                   "accounting")

# The same genres by Steam's OWN id — the only form of this rule that survives a
# localised catalog. Steam returns `{"id": "57", "description": "Utilities"}`, and the id
# is language-independent while the description is not: 3DMark is 57 as "Utilities",
# "Utilitários" and "Werkzeuge" alike (verified live 2026-08-04). Matching the NAME meant
# a Portuguese-localised ingest quietly hid nothing at all.
#
# Keyed BY the English name so the two lists cannot drift apart unnoticed — a genre with
# no Steam id (our own "software" catch-all, and anything IGDB-sourced) simply has no
# key here; anything present names a real NON_GAME_GENRES entry.
STEAM_GENRE_IDS = {"accounting": "50", "animation & modeling": "51",
                   "audio production": "52", "design & illustration": "53",
                   "photo editing": "55", "software training": "56",
                   "utilities": "57", "video production": "58",
                   "web publishing": "59", "game development": "60"}
NON_GAME_GENRE_IDS = tuple(sorted(set(STEAM_GENRE_IDS.values()), key=int))


def hidden_sql():
    """SQL boolean (+args) that is TRUE for an entry to hide as a NON-game. The manual
    `content_type` override (attr-overrides, aliased `ov`) wins over Steam's detected
    type: a value other than 'Game' hides an item Steam mis-tagged as a game (Wallpaper
    Engine, fpsVR — Steam calls them games), and 'Game' rescues a real game Steam
    mis-tagged as a tool. With no manual override, fall back to the Steam type. Requires
    a connection with `ov` + `sco` attached — see `attach()`."""
    ph = ",".join("?" * len(NON_GAME_TYPES))
    gph = ",".join("?" * len(NON_GAME_GENRES))
    iph = ",".join("?" * len(NON_GAME_GENRE_IDS))
    expr = ("CASE WHEN EXISTS(SELECT 1 FROM ov.overrides o WHERE o.norm_key=g.norm_key "
            "AND o.kind='content_type') "
            "THEN EXISTS(SELECT 1 FROM ov.overrides o WHERE o.norm_key=g.norm_key AND "
            "o.kind='content_type' AND lower(o.value)<>'game') "
            "ELSE (g.norm_key IN (SELECT norm_key FROM sco.steam_type WHERE type IN (%s)) "
            "      OR EXISTS(SELECT 1 FROM game_attributes ga WHERE ga.game_id=g.id "
            "                AND ga.kind='genre_ids' AND ga.value IN (%s)) "
            "      OR EXISTS(SELECT 1 FROM game_attributes ga WHERE ga.game_id=g.id "
            "                AND ga.kind='genres' AND lower(ga.value) IN (%s))) "
            "END" % (ph, iph, gph))
    # id branch FIRST because it is the language-proof one; the name branch stays so
    # rows written before genre_ids existed — and non-Steam genres, which have no id —
    # keep being caught without waiting for a re-fetch.
    return expr, (list(NON_GAME_TYPES) + list(NON_GAME_GENRE_IDS)
                  + list(NON_GAME_GENRES))


def attach(con, data_dir):
    """ATTACH the two stores `hidden_sql()` reads onto a bare library connection.

    A store that does not exist yet is attached as an EMPTY in-memory table rather
    than created on disk: this is a read path, and on a fresh install neither
    attr-overrides nor scores exists until something writes one. Attaching nothing
    would make the expression a syntax error; creating the file would have a query
    quietly mint databases."""
    for alias, fname, ddl in (
            ("ov", "attr-overrides.sqlite",
             "CREATE TABLE IF NOT EXISTS ov.overrides("
             "norm_key TEXT, kind TEXT, value TEXT, origin TEXT, created REAL)"),
            ("sco", "scores.sqlite",
             "CREATE TABLE IF NOT EXISTS sco.steam_type("
             "norm_key TEXT PRIMARY KEY, type TEXT, updated REAL)")):
        path = os.path.join(data_dir, fname)
        if os.path.exists(path):
            try:
                con.execute("ATTACH DATABASE ? AS %s" % alias,
                            ("file:%s?mode=ro" % path,))
                continue
            except sqlite3.Error:
                pass                    # unreadable — fall through to the empty stand-in
        con.execute("ATTACH DATABASE ':memory:' AS %s" % alias)
        con.execute(ddl)
    return con
