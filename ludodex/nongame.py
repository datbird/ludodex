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

# What a STORE says a product is, when what it says is "not a game".
#
# `extra` is not a Steam word. It is ours, for a product a store sells beside a game
# rather than as one: GOG's "Cyberpunk 2077 Digital Goodies", an art book, a soundtrack
# bundle. GOG has no `type` field at all, but it does have `category`, and it leaves that
# EMPTY for exactly these. See `gog_owned.store_type`.
STORE_EXTRA = "extra"
NON_GAME_TYPES = ("application", "tool", "music", "video", "hardware", "series", "mod",
                  STORE_EXTRA)

def store_type_from_gog_category(category):
    """GOG's verdict, which is a MISSING field rather than a value.

    `isGame` IS NOT THE FIELD. GOG returns it TRUE for "Cyberpunk 2077 Digital Goodies".
    What separates that pack from a game is an EMPTY `category`: probed against a live
    account, exactly 1 of 60 owned products had one, and it was that pack.

    Returning None for a real category is deliberate. This answers "did the store tell us
    it is NOT a game", and a category is not a claim that it is one.

    It lives here rather than in `gog_owned` so the server can ask without importing that
    module, which reads GOG credentials at import time and would take the whole server
    down on an install that has never connected a GOG account.
    """
    return STORE_EXTRA if not (category or "").strip() else None


# ONE table for "what does the store say this is", across every store.
#
# It was `steam_type(norm_key, type, updated)`, which was the right mechanism wearing the
# wrong name. A GOG verdict had nowhere to go, so on 2026-08-26 a GOG bonus pack sat in
# the library as a game and AI enrichment dressed it in Cyberpunk 2077's year, genres,
# developer and description. Naming the store makes the second store possible.
STORE_TYPE_DDL = ("CREATE TABLE IF NOT EXISTS %sstore_type("
                  "norm_key TEXT, source TEXT, type TEXT, updated REAL, "
                  "PRIMARY KEY(norm_key, source))")


def ensure_store_type(con):
    """Create `store_type` on a WRITABLE scores connection, carrying `steam_type` over.

    Called from both places that open scores.sqlite for writing, so a live install picks
    the new table up the moment either one runs. The read path (`hidden_sql`) queries
    `store_type` unconditionally: a missing table there is an error, not an empty answer,
    which is the point. That is only safe because the server calls this at startup.

    Idempotent. The copy runs once because the INSERT is a no-op the second time.
    """
    con.execute(STORE_TYPE_DDL % "")
    try:
        con.execute("INSERT OR IGNORE INTO store_type(norm_key,source,type,updated) "
                    "SELECT norm_key,'steam',type,updated FROM steam_type")
    except sqlite3.OperationalError as e:
        # no steam_type at all is a fresh install, which needs no carry-over. Anything
        # else is a real fault and must not pass for one.
        if "no such table" not in str(e):
            raise
    return con

# Steam GENRES that only ever belong to software, never to a game. A second, independent
# signal for the same question — needed because `store_type` is populated only by
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
            "ELSE (g.norm_key IN (SELECT norm_key FROM sco.store_type WHERE type IN (%s)) "
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
             STORE_TYPE_DDL % "sco.")):
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
