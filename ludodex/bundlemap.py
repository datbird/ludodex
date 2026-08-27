#!/usr/bin/env python3
"""What IGDB says is inside a bundle. A PURE RULE, like `cardkey` and `remakekin`.

Collections had one source: `_looks_like_collection` guesses from the title and
`ai.detect_collections` judges the nominee. That works, it costs money, and it can only
see what a title advertises.

IGDB carries `bundles` on every game -- the bundles that CONTAIN it. Measured on the live
library 2026-08-27, 106 pairs where both the game and the bundle holding it are owned. The
AI path had found 93 and missed 13, among them Mega Man 6 in Mega Man Legacy Collection,
Streets of Rage 2 in Sega Mega Drive and Genesis Classics, and Quest for Glory V in Quest
for Glory Collection.

THE COUNT IS NOT THE POINT. A provider stating a fact outranks a model inferring one, and
it is free. Recording these first means the paid pass is asked about fewer titles, not
more -- the same shape as `store_type`, where the store saying what a product IS ended a
guess.

STATES, DOES NOT DECIDE. This module reports what IGDB says. Whether to record it, what a
prior human decision outranks, and which keys are already known are policy questions that
belong with the durable store (`compilations`), not here.
"""


def collections_from_bundles(bundles_by_game, owned, skip=()):
    """{bundle_norm_key: {"name": str, "members": [{"norm_key", "title"}]}}.

    `bundles_by_game` is {igdb_id: [bundle_igdb_id]} exactly as IGDB reports it -- the
    bundles CONTAINING each game -- so the map is built by inverting it.
    `owned` is {igdb_id: (norm_key, title)} or {igdb_id: (norm_key, title, platform)};
    nothing outside it can appear. The platform is carried onto the member when given,
    because materializing a member without one files it under no hardware at all.
    `skip` is bundle norm_keys the caller has already settled.

    A BUNDLE YOU DO NOT OWN IS NOT A COLLECTION. A collection is a product you bought, and
    recording one for a bundle absent from the library would invent an entry nothing owns.

    A single owned member is still worth recording. It is true, it credits that member's
    ownership, and the title-guess path could not see it at all.
    """
    skip = set(skip or ())
    out = {}
    for gid, blist in (bundles_by_game or {}).items():
        member = (owned or {}).get(gid)
        if not member:
            continue                          # a game we do not own says nothing here
        for bid in (blist or []):
            if bid == gid:
                # IGDB occasionally lists a pack inside itself. Left alone the collection
                # would credit its own ownership and hold the product as a member.
                continue
            bundle = (owned or {}).get(bid)
            if not bundle:
                continue
            bkey, bname = bundle[0], bundle[1]
            if bkey in skip or bkey == member[0]:
                continue
            slot = out.setdefault(bkey, {"name": bname, "members": []})
            if not any(m["norm_key"] == member[0] for m in slot["members"]):
                row = {"norm_key": member[0], "title": member[1]}
                if len(member) > 2 and member[2]:
                    row["platform"] = member[2]
                slot["members"].append(row)
    return {k: v for k, v in out.items() if v["members"]}
