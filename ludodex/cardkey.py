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


def card_key_for_title(game_key, canonical_title, title_index, graph):
    """The card key for an entry no provider matched.

    An unmatched edition has no id for `fold_root` to start from. The live catalog's
    "DARK SOULS: Prepare To Die Edition" is exactly this: matchgate refused it, so its
    game_key is `title:dark souls prepare to die`, and it still belongs on the Dark
    Souls card. So the walk starts from the TITLE instead: strip a trailing edition
    marker and look the result up by norm_key.

    A hit supplies the CARD ONLY. The entry's game_key, its provider link and its
    matched_by are untouched, and no provider is called. Grouping a card is a display
    decision; binding an identity is matchgate's job and stays there.
    """
    if not game_key or not game_key.startswith("title:"):
        return card_key_for(game_key, graph)
    if not title_index or not canonical_title:
        return game_key
    stripped = strip_edition(canonical_title)
    iid = title_index.get(norm(stripped))
    if not iid:
        return game_key
    return "igdb:%d" % fold_root(int(iid), graph)


def assign(entries, graph, unfolded=(), title_index=None):
    """{entry_key: card_key} for a whole catalog.

    `entries` is an iterable of (entry_key, game_key, canonical_title). `unfolded` is
    the set of entry_keys the user has pinned to their own card; those keep their
    `game_key` and are never folded, which is the manual reverse for IGDB's looser
    links. `title_index` maps norm_key -> igdb_id and is what lets an UNMATCHED edition
    find its card; omit it and unmatched entries simply stay on their own cards.
    """
    unfolded = set(unfolded or ())
    out = {}
    for ekey, gkey, title in entries:
        if ekey in unfolded:
            out[ekey] = gkey
        else:
            out[ekey] = card_key_for_title(gkey, title, title_index, graph)
    return out


def card_title(card_key, copy_titles, root_names):
    """The title to display on a card.

    Rule: take the first owned copy's title, and strip a trailing edition marker ONLY
    when the stripped form is the fold root's own name. That turns "DARK SOULS:
    REMASTERED" into "DARK SOULS" (root: Dark Souls) while leaving "Mega Man 2" alone
    (root: Rockman 2: Dr. Wily no Nazo). Falls back to the root's name when the card
    has no copies, which happens only for a synthetic card.
    """
    root_name = ""
    if card_key and card_key.startswith("igdb:"):
        try:
            root_name = root_names.get(int(card_key[5:]), "") or ""
        except ValueError:
            root_name = ""
    titles = [t for t in (copy_titles or []) if t]
    if not titles:
        return root_name
    first = titles[0]
    if root_name:
        stripped = strip_edition(first)
        if stripped != first and _same_title(stripped, root_name):
            return stripped
    return first


def _same_title(a, b):
    """Loose title equality for the strip check. Compares alphanumerics only, so
    trademark symbols, punctuation, spacing and case cannot defeat the match —
    "DARK SOULS" and "Dark Souls" are the same name."""
    keep = lambda s: "".join(c for c in (s or "").lower() if c.isalnum())
    return keep(a) == keep(b)
