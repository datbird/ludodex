#!/usr/bin/env python3
"""IGDB's SERIES is `collections`, not `franchises`, and they are different facts.

datbird, 2026-08-27, on finding Slay the Spire and Slay the Spire 2 unassociated:
"This erodes my trust that we've got whats needed to have solid, thorough
assocciations."

He was right, and a fresh ingest would not have fixed it. `GAME_FIELDS` asked IGDB for
`franchises.name` and nothing else, so `collections` was never in the payload at all --
not empty, ABSENT. IGDB carries Slay the Spire and Slay the Spire II under collection
9750 ("Slay the Spire") and gives neither of them a franchise, so the link was there,
free, and never requested.

Measured over the 2,362 identified games in the live library:

    with a FRANCHISE (what was fetched)      656
    with a COLLECTION (what was not)       1,344
    collections holding 2+ owned games        288
    owned games inside one of those           973

So the Series section was showing the minority case. Mega Man, Final Fantasy, Resident
Evil, Doom, Quake, Tomb Raider, Far Cry, Civilization, Sonic and Yakuza were all absent.

THE TWO FIELDS ARE NOT INTERCHANGEABLE, which is why they get one row each rather than
being merged. A collection is the SEQUEL LINE (Mega Man, Baldur's Gate). A franchise is
the BRAND OR LICENCE the game sits under (Dungeons & Dragons, Marvel). Merging them makes
a licence read like a sequel, which is the same distinction datbird drew when he ruled
that a remaster is its own product.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-series-")

import igdb                                      # noqa: E402

PASS = []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    print("1. the query asks for both groupings")
    # A field absent from GAME_FIELDS is absent from the payload, and a payload that
    # never carried the key is indistinguishable from a game that has no series.
    check("collections.name is requested", "collections.name" in igdb.GAME_FIELDS)
    check("franchises.name is still requested", "franchises.name" in igdb.GAME_FIELDS)

    print("2. the collection is the SERIES")
    got = igdb.map_record({
        "id": 40477, "name": "Slay the Spire",
        "collections": [{"id": 9750, "name": "Slay the Spire"}]})
    check("a collection becomes series", got.get("series") == ["Slay the Spire"])
    check("and does not become a franchise", "franchise" not in got)

    print("3. the franchise is its OWN attribute")
    got = igdb.map_record({
        "id": 1, "name": "Baldur's Gate",
        "franchises": [{"id": 1, "name": "Dungeons & Dragons"}]})
    check("a franchise becomes franchise", got.get("franchise") == ["Dungeons & Dragons"])
    check("and never lands in series, where it would read as a sequel line",
          "series" not in got)

    print("4. a game with both keeps them apart")
    got = igdb.map_record({
        "id": 2, "name": "Baldur's Gate II",
        "collections": [{"id": 2, "name": "Baldur's Gate"}],
        "franchises": [{"id": 1, "name": "Dungeons & Dragons"}]})
    check("series is the sequel line", got.get("series") == ["Baldur's Gate"])
    check("franchise is the licence", got.get("franchise") == ["Dungeons & Dragons"])

    print("5. absence stays absence")
    # An empty list is not a value. Writing one would fill the attribute panel with
    # blank series rows and make "not set" indistinguishable from "set to nothing".
    got = igdb.map_record({"id": 3, "name": "Nothing", "collections": [],
                           "franchises": []})
    check("no collection, no series", "series" not in got)
    check("no franchise, no franchise attribute", "franchise" not in got)

    print("6. the new attribute is reachable everywhere series is")
    app = open(os.path.join(ROOT, "server", "app.py"), encoding="utf-8").read()
    # The DEFINITION, not the first mention of the name: `_EDITABLE_ATTR_KINDS` is read
    # a few thousand lines before it is declared, so splitting on the name lands in the
    # reader and the check passes or fails for the wrong reason.
    block = app.split("_EDITABLE_ATTR_KINDS = [")[1].split("]")[0]
    check("it is editable in the attribute panel", '"franchise",' in block)
    check("it is searchable in the query language",
          '"franchise": "franchise"' in app)
    tsx = open(os.path.join(ROOT, "web", "src", "App.tsx"), encoding="utf-8").read()
    check("it renders as its own labelled group",
          "['franchise', 'Franchise']" in tsx)

    print("7. the payload cache knows WHICH FIELDS it was filled with")
    # THE SECOND HALF OF THIS BUG, and the one that would have hidden the fix. IGDB omits
    # a field it was not asked for rather than returning it empty, so a payload cached
    # under an older GAME_FIELDS reads exactly like a game with no series. The cache
    # refetched on age alone (30 days), so adding a field reached games one at a time as
    # their TTLs lapsed, and a re-enrich run the same day would have changed nothing.
    sig = igdb.fields_sig()
    check("the signature is derived from the field list, not hand-maintained",
          sig and sig == igdb.fields_sig())
    real, igdb.GAME_FIELDS = igdb.GAME_FIELDS, igdb.GAME_FIELDS + ",collections.slug"
    try:
        check("changing what is asked for changes the signature",
              igdb.fields_sig() != sig)
    finally:
        igdb.GAME_FIELDS = real
    check("and restoring it restores the signature", igdb.fields_sig() == sig)

    enr = open(os.path.join(ROOT, "ludodex", "igdb_enrich.py"), encoding="utf-8").read()
    check("the cache stores the signature it fetched under",
          "fields_sig" in enr and "ADD COLUMN fields_sig" in enr)
    check("and a payload fetched under a different one is refetched",
          "have[iid][1] != sig" in enr)

    print("8. the exporters carry it, or a round trip silently drops it")
    lb = open(os.path.join(ROOT, "ludodex", "launchbox.py"), encoding="utf-8").read()
    check("launchbox knows the kind", '"franchise"' in lb)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
