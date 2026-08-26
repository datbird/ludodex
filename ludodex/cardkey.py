#!/usr/bin/env python3
"""Which card an entry belongs to.

The library shows one card per GAME. An entry's `game_key` already answers "which game
is this" for ports of one release, so it is the default. What it does not answer is
"which game is this an EDITION of": Dark Souls: Remastered, Prepare To Die Edition and
plain Dark Souls are three IGDB ids and one game.

IGDB records that relationship over TWO columns and uses them inconsistently:

  * `version_parent` carries editions and bundles-of-one-game. 6,877 plain type-0 games
    have one, so filtering on game_type before reading it misses most editions.
  * `parent_game` carries remasters (9), expanded games (10) and ports (11) — and also
    DLC (1), expansions (2), packs (13) and remakes (8), none of which may fold.

So the rule reads both, and the type filter applies only to the `parent_game` branch.

WHAT NEVER FOLDS, each for its own reason:
  * type 8, remake — a remake is a different game. All 1,460 remake rows carry a
    parent_game, so without this clause every remake would fold into its original.
  * types 1 and 2, dlc and expansion — add-ons already leave the grid and list under
    their parent (2026-08-22-addons-design.md).
  * type 13, pack — a pack is a multi-game compilation, which the collections engine
    owns. All 8,915 carry a parent_game. Folding one would file several distinct games
    under a single card.

This module is PURE. It reads a graph dict and returns keys. It never opens a database,
never calls a provider, and never writes an identity. A card grouping is a display
decision; binding identity is `matchgate`'s job and stays there.
"""
from titlenorm import norm

# game_type values that fold through `parent_game`.
FOLD_TYPES = frozenset((9, 10, 11))     # remaster, expanded_game, port
REMAKE = 8
MAX_DEPTH = 4

# Trailing edition markers, longest first so "Game of the Year Edition" wins over
# "Edition". Matched case-insensitively against the end of a title, after an optional
# ":" or "-" separator.
EDITION_SUFFIXES = (
    "Game of the Year Edition", "Prepare To Die Edition", "Definitive Edition",
    "Complete Edition", "Enhanced Edition", "Remastered Edition", "Deluxe Edition",
    "Special Edition", "Anniversary Edition", "Game of the Year", "Remastered",
    "Enhanced", "Definitive", "Complete", "Deluxe", "GOTY", "HD",
)

_SEPARATORS = (":", "-", "–")


def fold_root(igdb_id, graph, max_depth=MAX_DEPTH):
    """The id of the game an edition belongs to. Returns `igdb_id` unchanged when the
    entry is already a root, is a type that never folds, or is absent from the graph.

    `graph` maps igdb_id -> (game_type, version_parent, parent_game).

    Terminates on a cycle and at `max_depth`. A malformed provider graph must not hang
    a catalog rebuild, and leaving the entry on its own card is the safe failure.
    """
    try:
        cur = int(igdb_id)
    except (TypeError, ValueError):
        return igdb_id
    seen = {cur}
    for _ in range(max_depth):
        row = graph.get(cur)
        if not row:
            return cur
        gtype, vparent, pparent = row
        nxt = None
        if vparent and gtype != REMAKE:
            nxt = int(vparent)
        elif not vparent and gtype in FOLD_TYPES and pparent:
            nxt = int(pparent)
        if nxt is None or nxt in seen:
            return cur
        seen.add(nxt)
        cur = nxt
    return cur


def card_key_for(game_key, graph, max_depth=MAX_DEPTH):
    """The card key for one entry. `igdb:<id>` folds to its root; every other shape —
    `title:<norm_key>`, None, a malformed key — is returned untouched, because only a
    resolved identity has a provider graph to walk."""
    if not game_key or not game_key.startswith("igdb:"):
        return game_key
    raw = game_key[5:]
    try:
        iid = int(raw)
    except ValueError:
        return game_key
    return "igdb:%d" % fold_root(iid, graph, max_depth)


def strip_edition(title):
    """Remove ONE trailing edition marker. Returns the title unchanged when nothing
    matches, and never returns an empty string — a game actually called "Remastered"
    keeps its name."""
    if not title:
        return title
    t = title.rstrip()
    low = t.lower()
    for suf in EDITION_SUFFIXES:
        if not low.endswith(suf.lower()):
            continue
        head = t[:len(t) - len(suf)].rstrip()
        while head and head[-1] in _SEPARATORS:
            head = head[:-1].rstrip()
        if head:
            return head
    return title
