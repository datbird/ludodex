#!/usr/bin/env python3
"""A collection already RECORDED is a decision — do not re-propose it.

`collection_rejected` makes the AI's negative verdict durable: a candidate judged
not-a-collection is never re-nominated, and never re-billed. The positive verdict had
no equivalent. `_collection_candidates` (the dedicated detection scan) does skip
`known` keys, but that is only one of the two onramps: the `metadata` area answers a
COLLECTION question for every game it analyzes, and `store_finding` turned that answer
into a finding with no idea the collection was already recorded.

Live cost of the gap, 2026-08-07: of 34 collection findings in the Lite-import queue,
33 re-proposed a collection already in `collections.sqlite`. That is not merely noise.
The model is not deterministic, so 11 came back DIFFERENT from what was recorded, and
several were worse — a Heretic + Hexen missing Hexen II and Portal of Praevus, a DOOM 3
BFG missing Resurrection of Evil, a Police Quest II carrying an invented subtitle. A
re-proposal that can only be accepted or rejected is a chance to lose good data.

The distinction these pin: a RECORDED collection is a decision to respect; the rest of
the finding (attribute gaps, a wrong match) is not part of that decision and must
survive.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-collsettled-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def coll_result(name, members, attrs=None, status="ok"):
    return {"match": {"status": status, "confidence": 1.0},
            "attributes": attrs or {},
            "collection": {"is_collection": True, "name": name,
                           "members": [{"title": t} for t in members]}}


def findings(kind=None):
    con = sqlite3.connect(os.path.join(D, "ai-metadata.sqlite"))
    q = "SELECT id, kind, payload_json FROM findings WHERE status='proposed'"
    if kind:
        q += " AND kind='%s'" % kind
    try:
        rows = con.execute(q).fetchall()
    except sqlite3.OperationalError:
        rows = []                       # nothing stored means the table may not exist
    con.close()
    return rows


def main():
    import aimeta
    import compilations

    # a collection the user already has recorded
    compilations.set_collection(
        D, "mega man legacy collection", "Mega Man Legacy Collection",
        [{"title": "Mega Man"}, {"title": "Mega Man 2"}], origin="ai")

    ctx = {"norm_key": "mega man legacy collection",
           "title": "Mega Man Legacy Collection", "match": None, "missing": []}
    # the model re-answers the COLLECTION question, and drops a member this time
    kind = aimeta.store_finding(1, ctx, coll_result(
        "Mega Man Legacy Collection", ["Mega Man"]))
    check("a recorded collection is not re-proposed", kind is None)
    check("no collection finding was stored", not findings("collection"))

    # a collection NOT yet recorded must still be proposed — this is the whole feature
    ctx2 = {"norm_key": "contra anniversary collection",
            "title": "Contra Anniversary Collection", "match": None, "missing": []}
    kind2 = aimeta.store_finding(1, ctx2, coll_result(
        "Contra Anniversary Collection", ["Contra", "Super C"]))
    check("an unrecorded collection is still proposed", kind2 == "collection")

    # the collection claim is settled; the REST of the finding is not
    ctx3 = {"norm_key": "mega man legacy collection",
            "title": "Mega Man Legacy Collection", "match": None,
            "missing": ["genres"]}
    kind3 = aimeta.store_finding(1, ctx3, coll_result(
        "Mega Man Legacy Collection", ["Mega Man"],
        attrs={"genres": ["Platform"]}))
    check("attribute gaps on a settled collection still surface", kind3 == "identify")
    rows = [r for r in findings() if r[1] == "identify"]
    check("the settled collection is stripped from the payload",
          rows and '"collection": null' in rows[0][2].replace(" ", " "))

    # A manual VETO is the other half: the entry is deliberately NOT recorded, so
    # `get_collection` is None and the suppression above cannot see it. Without this
    # the user's removal is undone by the next scan proposing the bundle again.
    compilations.set_collection(D, "retro game crunch", "Retro Game Crunch",
                                [{"title": "BrainShatter"}], origin="ai")
    compilations.clear_collection(D, "retro game crunch", origin="manual")
    ctx4 = {"norm_key": "retro game crunch", "title": "Retro Game Crunch",
            "match": None, "missing": []}
    kind4 = aimeta.store_finding(1, ctx4, coll_result(
        "Retro Game Crunch", ["BrainShatter", "Gauntlet of Fools"]))
    check("a manually vetoed collection is not proposed either", kind4 is None)

    print("\n  %d/%d passed" % (sum(1 for _, c in PASS if c), len(PASS)))


if __name__ == "__main__":
    main()
