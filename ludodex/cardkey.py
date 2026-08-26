#!/usr/bin/env python3
"""Which card an entry belongs to.

THE RULE: **the only axis that folds is PLATFORM.** A product is a card.

Own "Dark Souls: Prepare To Die Edition" on Xbox, PlayStation and Steam and that is ONE
card listing three platforms. "Dark Souls: Remastered" is a DIFFERENT product, so it is
its own card listing the platforms you own IT on. So is "Dark Souls II", and so is
"Scholar of the First Sin".

The first version of this folded editions, remasters and expanded games onto the
original, and that was wrong in a way only looking could show: it hid Remastered inside
Dark Souls, and a user searching for the game they own could not find it. Corrected
2026-08-26.

WHAT STILL FOLDS, and only this: IGDB's `port` type (11). A port is the SAME product with
its own record, usually another platform or region, and collapsing those is exactly the
platform axis. It is what makes "DOOM 2" and "DOOM II" one card, or "Into The Breach" and
"Into the Breach", which are one game listed twice.

WHAT NEVER FOLDS, and why each is a product in its own right:
  * 8 remake and 9 remaster — a different game, and a different thing to own.
  * 3 bundle, 10 expanded_game — an edition. You bought THAT, not the original.
  * 1 dlc and 2 expansion — add-ons, which already leave the grid and list under their
    parent (2026-08-22-addons-design.md).
  * 13 pack — a multi-game compilation, which the collections engine owns.

None of those links is discarded. They become the "Other versions" and "Series" sections
on the detail page, where a relationship belongs: shown, not merged.

This module is PURE. It reads a graph dict and returns keys. It never opens a database,
never calls a provider, and never writes an identity. A card grouping is a display
decision; binding identity is `matchgate`'s job and stays there.
"""

# The ONE game_type that folds, and it folds through `parent_game`.
FOLD_TYPES = frozenset((11,))           # port: the same product, its own record
REMAKE = 8
MAX_DEPTH = 4

# Trailing edition markers, longest first so "Game of the Year Edition" wins over
# "Edition". These no longer decide what FOLDS: an edition is a product. They are used
# only to name a card when a port merge leaves two spellings of one title.
EDITION_SUFFIXES = (
    "Game of the Year Edition", "Prepare To Die Edition", "Definitive Edition",
    "Complete Edition", "Enhanced Edition", "Remastered Edition", "Deluxe Edition",
    "Special Edition", "Anniversary Edition", "Game of the Year", "Remastered",
    "Enhanced", "Definitive", "Complete", "Deluxe", "GOTY", "HD",
)

_SEPARATORS = (":", "-", "\u2013")


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
        gtype, _vparent, pparent = row
        # `version_parent` is DELIBERATELY not followed. It is the edition link, and an
        # edition is a product. It feeds the "Other versions" section instead.
        nxt = int(pparent) if (gtype in FOLD_TYPES and pparent) else None
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


def card_key_for_entry(entry_key, game_key, graph, unfolded=()):
    """THE decision: which card one entry belongs to. `build_library` calls this at
    every insert site, and `assign` is a loop over it, so the user's pin and the fold
    are decided in exactly one place.

    It used to be inlined at the insert sites, with `assign` holding a second copy for
    the tests. That is the two-derivations shape this codebase keeps paying for: the
    copy the tests exercised was not the copy that ran.
    """
    if entry_key in unfolded:
        return game_key                    # the user pinned this entry to its own card
    return card_key_for(game_key, graph)


def assign(entries, graph, unfolded=()):
    """{entry_key: card_key} for a whole catalog. A LOOP over `card_key_for_entry` and
    nothing else, so it cannot drift from what a rebuild does.

    `entries` is an iterable of (entry_key, game_key, canonical_title). The title is
    accepted and ignored: it was needed when editions folded by name, and the shape is
    kept so callers do not have to change.
    """
    unfolded = set(unfolded or ())
    return {ekey: card_key_for_entry(ekey, gkey, graph, unfolded)
            for ekey, gkey, _title in entries}


def card_title(card_key, copy_titles, root_names):
    """The title to display on a card, in three tiers.

    1. A copy whose title IS the root's name wins outright. If you own the base game,
       that is what the card is called. Without this tier the Dark Souls II card wore
       "Scholar of the First Sin" while plain "Dark Souls II" sat on the same card, and
       "Scholar of the First Sin" is not a strippable edition marker so tier 2 could not
       save it. Found live on 2026-08-26.
    2. Else a copy that STRIPS to the root's name, so "DARK SOULS: REMASTERED" becomes
       "DARK SOULS" when nothing plainer is owned.
    3. Else the first copy's title, untouched. This is what protects a regional root:
       "Mega Man 2" folds onto "Rockman 2: Dr. Wily no Nazo" and must not be renamed
       into Japanese.

    Tiers 1 and 2 scan every copy, so the answer does not depend on which copy sorts
    first. Falls back to the root's name only when the card has no copies at all.
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
    if root_name:
        for t in titles:                       # tier 1: a copy that IS the game
            if _same_title(t, root_name):
                return t
        for t in titles:                       # tier 2: a copy that strips to it
            stripped = strip_edition(t)
            if stripped != t and _same_title(stripped, root_name):
                return stripped
    return titles[0]                           # tier 3: leave it alone


def _same_title(a, b):
    """Loose title equality for the strip check. Compares alphanumerics only, so
    trademark symbols, punctuation, spacing and case cannot defeat the match —
    "DARK SOULS" and "Dark Souls" are the same name."""
    keep = lambda s: "".join(c for c in (s or "").lower() if c.isalnum())
    return keep(a) == keep(b)
