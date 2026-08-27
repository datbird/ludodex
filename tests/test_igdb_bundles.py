#!/usr/bin/env python3
"""IGDB states bundle membership outright. Deriving it beats paying a model to guess.

Collections were found ONE way: `_looks_like_collection` guesses from the title, then
`ai.detect_collections` judges the nominee. That works, and it costs money, and it can
only see what a title advertises.

IGDB carries `bundles` on every game: the bundles that CONTAIN it. Measured on the live
library 2026-08-27, 106 pairs where both the game and the bundle holding it are owned. The
AI path had already found 93 of them. It had missed 13, including:

    Mega Man 6                        is in  Mega Man Legacy Collection
    Streets of Rage 2                 is in  Sega Mega Drive and Genesis Classics
    Quest for Glory V: Dragon Fire    is in  Quest for Glory Collection
    King's Quest II                   is in  King's Quest Collection
    Homeworld 2                       is in  Homeworld Collection

THE COUNT IS NOT THE POINT. A provider stating a fact outranks a model inferring one, and
it is free. This runs BEFORE the AI pass and whatever it records leaves the candidate
pool, so the paid step is asked about less, not more. Same shape as `store_type`: when the
store says what a thing IS, stop guessing.

WHAT IT WILL NOT DO. It never records a bundle whose membership it cannot see, and it
never overwrites a collection a person curated. `origin='igdb'` keeps the provenance
visible, and `origin='manual'` still wins.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-bundles-")

import bundlemap                                 # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


# `bundles` on a game lists the bundles CONTAINING it, so the map is built by inverting
# it. owned: igdb_id -> (norm_key, title)
OWNED = {
    10: ("mega man 6", "Mega Man 6"),
    11: ("mega man 5", "Mega Man 5"),
    99: ("mega man legacy collection", "Mega Man Legacy Collection"),
    20: ("some game", "Some Game"),
    # 77 is deliberately ABSENT: a bundle IGDB says contains your games but that you do
    # not own yourself. It must never become a collection.
}
BUNDLES = {                       # igdb_id -> the bundles it belongs to
    10: [99, 77],
    11: [99],
    20: [77],
}


def main():
    print("1. membership is inverted into collections")
    got = bundlemap.collections_from_bundles(BUNDLES, OWNED)
    check("the owned bundle becomes a collection",
          "mega man legacy collection" in got)
    check("and holds the members you own",
          sorted(m["norm_key"] for m in got["mega man legacy collection"]["members"])
          == ["mega man 5", "mega man 6"])
    check("the member carries its title, which is what the store shows",
          {m["title"] for m in got["mega man legacy collection"]["members"]}
          == {"Mega Man 5", "Mega Man 6"})

    print("2. a bundle you do NOT own is not a collection")
    # A collection is a thing you bought. Recording one for a bundle absent from the
    # library would invent an entry nothing owns.
    check("bundle 77 is absent", len(got) == 1)

    print("3. a bundle with only ONE owned member is still recorded")
    # It is still true, and it still credits that member's ownership. The old title-guess
    # path could not see it at all.
    one = bundlemap.collections_from_bundles({11: [99]}, OWNED)
    check("a single-member bundle records",
          [m["norm_key"] for m in one["mega man legacy collection"]["members"]]
          == ["mega man 5"])

    print("4. a bundle never contains itself")
    # IGDB occasionally lists a pack inside itself. Left alone, the collection would
    # credit its own ownership and the member list would contain the product.
    self_ref = bundlemap.collections_from_bundles({99: [99], 10: [99]}, OWNED)
    check("the bundle is not its own member",
          [m["norm_key"] for m in self_ref["mega man legacy collection"]["members"]]
          == ["mega man 6"])

    print("5. the name comes from the bundle's OWN title")
    check("named after the bundle, never a member",
          got["mega man legacy collection"]["name"] == "Mega Man Legacy Collection")

    print("6. nothing to say is an empty answer, not an empty collection")
    check("no bundles, no collections",
          bundlemap.collections_from_bundles({}, OWNED) == {})
    check("no owned games, no collections",
          bundlemap.collections_from_bundles(BUNDLES, {}) == {})
    check("a game whose bundle list is empty contributes nothing",
          bundlemap.collections_from_bundles({10: []}, OWNED) == {})

    print("7. already-known and rejected keys are the CALLER's to skip")
    # The module states what IGDB says. Whether to record it is a policy question that
    # belongs with the durable store, not here.
    got2 = bundlemap.collections_from_bundles(BUNDLES, OWNED,
                                              skip={"mega man legacy collection"})
    check("a skipped bundle is not offered", got2 == {})

    print("8. the module is a PURE RULE")
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "ludodex", "bundlemap.py"), encoding="utf-8").read()
    import ast as _ast
    imported = set()
    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, _ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    check("it imports NOTHING at all: %s" % (sorted(imported) or "none"), not imported)

    print("9. the server EXTENDS an existing collection, and never replaces it")
    # `set_collection` rewrites the member set, so handing it IGDB's list alone would
    # DELETE members the model or the user had already established. IGDB knows what is in
    # a bundle; it does not know what else is. Live, DOOM 3 BFG gains "Doom + Doom II"
    # and Serious Sam Classics Revolution gains two, with nothing lost.
    app = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "server", "app.py"), encoding="utf-8").read()
    blk = app.split("def _igdb_declared_collections", 1)[1].split("\ndef ", 1)[0]
    check("it reads what the collection already holds", "get_collection(DATA" in blk)
    check("it carries the existing members through", "keep + added" in blk)
    check("it does nothing when it has nothing new", "if not added:" in blk)
    check("it keeps the existing name rather than renaming",
          'prior.get("name") or name' in blk)

    print("10. only a MANUAL decision is binding")
    # `mark_rejected` says an origin='ai' verdict is a guess that "any later record
    # clears", and a provider stating membership IS that later evidence. A manual veto is
    # different, and `set_collection` enforces it, so this must not re-derive it.
    check("a manual collection is skipped", '== "manual"' in blk)
    check("an AI rejection is NOT treated as a skip",
          "rejected_keys" not in blk)

    print("11. the field is actually requested, or the payload never carries it")
    import igdb
    check("bundles is in GAME_FIELDS", "bundles" in igdb.GAME_FIELDS.split(","))

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
