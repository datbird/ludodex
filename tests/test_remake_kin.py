#!/usr/bin/env python3
"""A remake is a separate product. Separate is not the same as invisible.

datbird ruled on 2026-08-26 that a remake never folds onto the original's card, and that
is right: X-COM: UFO Defense and XCOM: Enemy Unknown are different games. But `versions`
walks the lineage and STOPS at a remake by design, so the two ended up with no connection
at all. You own both and neither page mentions the other.

Measured on the live library 2026-08-27, ten pairs where BOTH ends are owned:

    Half-Life                    <-> Black Mesa
    X-COM: UFO Defense           <-> XCOM: Enemy Unknown
    Counter-Strike               <-> Counter-Strike: Source
    Day of Defeat                <-> Day of Defeat: Source
    The Binding of Isaac         <-> The Binding of Isaac: Rebirth
    Risk of Rain (2013)          <-> Risk of Rain Returns
    Rise of the Triad: Dark War  <-> Rise of the Triad
    Sid Meier's Colonization     <-> Civilization IV: Colonization
    Super Lucky's Tale           <-> New Super Lucky's Tale
    Arizona Sunshine VR Legacy   <-> Arizona Sunshine VR Remake

NO REFETCH WAS NEEDED. The mirror already carries every one of these: a remake is
`game_type` 8 with `parent_game` pointing at what it remakes. The edge was there and
nothing read it.

THE RELATION IS SYMMETRIC, and that is the whole point of a separate tier. The original
lists its remakes and each remake lists the original, without either being folded into the
other's card.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-remake-")

import remakekin                                 # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


# {igdb_id: (game_type, version_parent, parent_game)} — the mirror's own shape.
#   231 Half-Life            0
#  6739 Black Mesa           8, remakes 231
#   130 Half-Life: Source    0, parent 231      (a PORT, not a remake)
#    24 X-COM: UFO Defense   0
#  1318 XCOM: Enemy Unknown  8, remakes 24
#  9999 Some Other Remake    8, also remakes 24 (a sibling remake)
#  4242 Unrelated            0
GRAPH = {
    231: (0, None, None),
    6739: (8, None, 231),
    130: (11, None, 231),
    24: (0, None, None),
    1318: (8, None, 24),
    9999: (8, None, 24),
    4242: (0, None, None),
}


def main():
    print("1. a remake names what it remakes, and nothing else does")
    check("a remake resolves to its original",
          remakekin.remake_of(6739, GRAPH) == 231)
    check("a plain game remakes nothing", remakekin.remake_of(231, GRAPH) is None)
    check("a PORT is not a remake, however it is parented",
          remakekin.remake_of(130, GRAPH) is None)
    check("an id the mirror has never seen remakes nothing",
          remakekin.remake_of(777777, GRAPH) is None)

    print("2. the relation is symmetric")
    owned = [231, 6739, 130, 24, 1318, 4242]
    check("the original lists its remake",
          remakekin.kin(231, GRAPH, owned) == [6739])
    check("the remake lists the original",
          remakekin.kin(6739, GRAPH, owned) == [231])

    print("3. a port is never listed AS a remake, but it is part of what was remade")
    # Two different questions, and the asymmetry is deliberate.
    #
    # "What remakes this?" is asked of a LINEAGE. Half-Life: Source is Half-Life in
    # another form, so Black Mesa remakes it just as much as it remakes Half-Life, and
    # saying so is true. At the card level it never even arises: a port folds onto the
    # original's card, so the page is asked about igdb:231, never about the port.
    #
    # "Is this a remake?" is asked of a PRODUCT, and a port is not one however it is
    # parented. So the port never appears IN a remake list.
    check("the port is absent from the original's remakes",
          130 not in remakekin.kin(231, GRAPH, owned))
    check("the port is absent from the remake's list too",
          130 not in remakekin.kin(6739, GRAPH, owned))
    check("but the port still knows what remakes its lineage",
          remakekin.kin(130, GRAPH, owned) == [6739])

    print("4. a STANDALONE EXPANSION does not inherit the original's remakes")
    # Live, this was the whole false-positive class. Half-Life: Opposing Force and Blue
    # Shift are IGDB type 4: they descend from Half-Life without BEING Half-Life, so
    # walking through them made Black Mesa read as a remake of THEM. Black Mesa remakes
    # Half-Life. A remaster (type 9) is the same game and does inherit it.
    exp = dict(GRAPH)
    exp[500] = (4, None, 231)          # Opposing Force: standalone expansion
    exp[501] = (9, None, 231)          # Half-Life: Source: remaster
    owned3 = [231, 6739, 500, 501]
    check("the standalone expansion has no remake kin",
          remakekin.kin(500, exp, owned3) == [])
    check("and it is never offered as one either",
          500 not in remakekin.kin(231, exp, owned3))
    check("the REMASTER does inherit the remake",
          remakekin.kin(501, exp, owned3) == [6739])
    check("same-game root stops at a standalone expansion",
          remakekin.same_game_root(500, exp) == 500)
    check("and follows through a remaster",
          remakekin.same_game_root(501, exp) == 231)

    print("5. two remakes of one original are kin to each other")
    owned2 = [24, 1318, 9999]
    check("the original lists both",
          sorted(remakekin.kin(24, GRAPH, owned2)) == [1318, 9999])
    check("each remake lists the original AND its sibling",
          sorted(remakekin.kin(1318, GRAPH, owned2)) == [24, 9999])

    print("6. only what you OWN can appear")
    # The caller passes the owned set. A remake you do not have is Discover's job.
    check("an unowned remake is not listed",
          remakekin.kin(24, GRAPH, [24, 4242]) == [])
    check("an unrelated owned game is never kin",
          4242 not in remakekin.kin(231, GRAPH, owned))

    print("7. a cycle terminates instead of hanging")
    cyc = {1: (8, None, 2), 2: (8, None, 1)}
    check("a two-node cycle returns rather than looping",
          remakekin.remake_of(1, cyc) in (1, 2))
    check("and kin still answers", isinstance(remakekin.kin(1, cyc, [1, 2]), list))

    print("8. the module is a PURE RULE")
    # Same discipline as cardkey: a display relationship must never reach a database or a
    # provider, or "what is related" becomes something that can fail at request time.
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "ludodex", "remakekin.py"), encoding="utf-8").read()
    # IMPORTS, not the words. The docstring names `igdb_mirror.fold_graph()` because that
    # is where the caller gets the graph, and banning the string would only teach the next
    # person to stop explaining themselves.
    import ast as _ast
    imported = set()
    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, _ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    check("it imports NOTHING at all: %s" % (sorted(imported) or "none"), not imported)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
