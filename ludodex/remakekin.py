#!/usr/bin/env python3
"""Which owned games are remakes of one another. A PURE RULE, like `cardkey`.

A remake is a separate product and never folds onto the original's card (datbird,
2026-08-26). `_related`'s `versions` tier walks the version lineage and STOPS at a remake
for exactly that reason. The consequence nobody intended: the two games ended up with no
connection at all, so you could own Half-Life and Black Mesa and neither page mentioned
the other. Separate is not the same as invisible.

Measured on the live library 2026-08-27: ten pairs where both ends are owned, including
Half-Life/Black Mesa, X-COM: UFO Defense/XCOM: Enemy Unknown, Counter-Strike/Counter-
Strike: Source and The Binding of Isaac/Rebirth.

NO NEW DATA WAS NEEDED. The mirror already carries every one: a remake is `game_type` 8
with `parent_game` naming what it remakes. The edge was there and nothing read it.

NO I/O, ON PURPOSE. A displayed relationship must not be able to fail at request time, and
this module is imported by a request path. It takes the graph the caller already holds
(`igdb_mirror.fold_graph()`) and the ids the caller already knows are owned. A test asserts
it cannot open a database or reach a provider.
"""

REMAKE = 8                      # IGDB game_type for a remake

# The types that mean "the SAME GAME in another form", and so inherit its remakes.
#   0  a main game, or an edition when it carries a version_parent
#   9  remaster        11  port        10  expanded game
#
# NOT 4 (standalone expansion), 2 (expansion), 1 (dlc), 6 (episode), 7 (season). Those
# descend from a game without BEING it, and the difference is not academic: Half-Life:
# Opposing Force and Blue Shift are type 4, so walking through them made Black Mesa read
# as a remake OF THEM. Black Mesa remakes Half-Life. Half-Life: Source is type 9, the same
# game remastered, and it inherits the remake correctly.
SAME_GAME = frozenset({0, 9, 10, 11})


def _parent(row):
    """What this row is parented to, whichever column carries it."""
    if not row:
        return None
    _gtype, vparent, pparent = row
    return vparent or pparent


def version_root(igdb_id, graph, max_depth=4):
    """The oldest ancestor in the VERSION lineage, stopping at a remake.

    Mirrors `app._version_root` deliberately: the two tiers must agree on where a lineage
    ends, or a game could appear in `versions` AND `remakes` and the page would contradict
    itself. A remake terminates the walk because it is a new work, not another way to own
    the same one.
    """
    cur, seen = int(igdb_id), {int(igdb_id)}
    for _ in range(max_depth):
        row = graph.get(cur)
        if not row:
            return cur
        if row[0] == REMAKE:
            return cur
        nxt = _parent(row)
        if not nxt or int(nxt) in seen:
            return cur
        seen.add(int(nxt))
        cur = int(nxt)
    return cur


def same_game_root(igdb_id, graph, max_depth=4):
    """The oldest ancestor reached WITHOUT leaving this game.

    Stricter than `version_root`, which follows any parent link. A remake belongs to a
    GAME, so deciding what it remakes may only walk through forms of that same game: an
    edition, a remaster, a port, an expanded edition. A standalone expansion is a
    different product that merely descends from one, and treating it as the same game is
    what made Black Mesa appear on Opposing Force's page.
    """
    cur, seen = int(igdb_id), {int(igdb_id)}
    for _ in range(max_depth):
        row = graph.get(cur)
        if not row or row[0] not in SAME_GAME:
            return cur
        nxt = _parent(row)
        if not nxt or int(nxt) in seen:
            return cur
        seen.add(int(nxt))
        cur = int(nxt)
    return cur


def remake_of(igdb_id, graph, max_depth=4):
    """The lineage this game is a REMAKE of, or None when it is not a remake.

    Returns the other side's version ROOT rather than its immediate parent, so a remake of
    a remaster still points at the original work. A PORT is never a remake however it is
    parented: `versions` already says a port is another way to own the same game, and
    repeating it here would make the two tiers mean different things by the same edge.
    """
    row = graph.get(int(igdb_id))
    if not row or row[0] != REMAKE:
        return None
    nxt = _parent(row)
    if not nxt:
        return None
    return same_game_root(int(nxt), graph, max_depth)


def kin(igdb_id, graph, owned, max_depth=4):
    """Owned ids related to `igdb_id` by a remake edge, in either direction.

    SYMMETRIC, which is the whole point of a separate tier: the original lists its
    remakes, each remake lists the original, and two remakes of one work list each other.
    Neither is folded into the other's card.

    `owned` is the ids the caller has; nothing outside it can appear, because listing what
    you do not own is Discover's job.
    """
    me = int(igdb_id)
    # SAME-GAME root, not the version root: only another FORM of this game inherits its
    # remakes. See SAME_GAME.
    my_root = same_game_root(me, graph, max_depth)
    my_source = remake_of(me, graph, max_depth)
    out = []
    for other in owned:
        o = int(other)
        if o == me:
            continue
        o_source = remake_of(o, graph, max_depth)
        if o_source is not None and o_source == my_root:
            out.append(o)                    # `other` remakes this game
        elif my_source is not None and o == my_source:
            # THE ROOT ITSELF, not everything sharing it. A port of the original has the
            # same root, and `versions` already lists it as another way to own that game;
            # naming it here too would make the two tiers mean different things by one
            # edge. At the card level this is moot anyway, because a port folds onto the
            # original's card.
            out.append(o)                    # this game remakes `other`
        elif (my_source is not None and o_source is not None
                and o_source == my_source):
            out.append(o)                    # both remake the same work
    return sorted(set(out))
