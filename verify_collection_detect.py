#!/usr/bin/env python3
"""Verify the systematic compilation detection pass (task #12).

Covers the two halves separately:
  1. `_looks_like_collection` — the algorithmic pre-filter that decides which titles are
     worth spending an AI call on. Real compilation names must hit; editions/remasters/
     sequels must not.
  2. `_auto_detect_collections` — the batching + recording path, with the AI stubbed, so
     the confidence gate, the "no members -> record nothing" rule, and the write into
     collections.sqlite are all exercised without a network call or an API key.

Runs against a throwaway LUDODEX_DATA, so it never touches a real install.
Usage: python3 verify_collection_detect.py
"""
import os
import sqlite3
import sys
import tempfile

FAIL = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAIL.append(label)


scratch = tempfile.mkdtemp(prefix="ludodex-collcheck-")
os.environ["LUDODEX_DATA"] = scratch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# A minimal catalog holding just what _collection_candidates reads.
GAMES = [
    ("sonicmegacollection", "Sonic Mega Collection", "gamecube"),
    ("segagenesisclassics", "Sega Genesis Classics", "steam"),
    ("metroidprimetrilogy", "Metroid Prime Trilogy", "wii"),
    ("tombraideranniversary", "Tomb Raider: Anniversary", "ps2"),
    ("darksoulsremastered", "Dark Souls Remastered", "steam"),
    ("doom", "Doom", "sega 32x"),
]
con = sqlite3.connect(os.path.join(scratch, "game-library.sqlite"))
con.execute("CREATE TABLE games (norm_key TEXT, canonical_title TEXT, platform TEXT)")
con.executemany("INSERT INTO games VALUES (?,?,?)", GAMES)
con.commit()
con.close()

import compilations                      # noqa: E402
from server import app as A              # noqa: E402

print("1. pre-filter — real compilations must hit")
for t in ["Sonic Mega Collection", "Sega Genesis Classics", "Mega Man Legacy Collection",
          "Castlevania Anniversary Collection", "Super Mario All-Stars",
          "Metroid Prime Trilogy", "Final Fantasy Anthology", "150-in-1", "6 in 1",
          "Namco Museum Arcade Classics"]:
    check(A._looks_like_collection(t), "hits: %s" % t)

print("2. pre-filter — one game must NOT be mistaken for a bundle")
for t in ["Tomb Raider: Anniversary", "Dark Souls Remastered", "Sonic CD", "Doom",
          "The Witcher 3: Game of the Year Edition", "Resident Evil 4 HD", "F1 2019",
          "Halo 3", "Portal 2"]:
    check(not A._looks_like_collection(t), "skips: %s" % t)

print("3. candidates come only from bundle-looking titles in the catalog")
cands = A._collection_candidates([g[0] for g in GAMES])
keys = sorted(c["norm_key"] for c in cands)
check(keys == ["metroidprimetrilogy", "segagenesisclassics", "sonicmegacollection"],
      "3 candidates, editions/plain titles excluded (got %s)" % keys)

print("4. recording path (AI stubbed)")
VERDICTS = {
    "Sonic Mega Collection": {"is_collection": True, "name": "Sonic Mega Collection",
                              "confidence": 0.95,
                              "members": [{"title": "Sonic the Hedgehog", "platform": "genesis",
                                           "year": 1991},
                                          {"title": "Sonic the Hedgehog 2", "platform": "genesis",
                                           "year": 1992}]},
    # High confidence but NO members -> must record nothing (a bundle we can't enumerate).
    "Sega Genesis Classics": {"is_collection": True, "name": "Sega Genesis Classics",
                              "confidence": 0.99, "members": []},
    # Real bundle but below the confidence gate -> must not be recorded.
    "Metroid Prime Trilogy": {"is_collection": True, "name": "Metroid Prime Trilogy",
                              "confidence": 0.4,
                              "members": [{"title": "Metroid Prime", "platform": "gamecube",
                                           "year": 2002}]},
}
calls = {"n": 0}


def fake_detect(items, **kw):
    calls["n"] += 1
    out = []
    for it in items:
        v = VERDICTS.get(it["title"])
        if v:
            out.append({**v, "n": it["n"]})
        else:
            out.append({"n": it["n"], "is_collection": False, "confidence": 0.9, "members": []})
    return out


A.ai.detect_collections = fake_detect
A.ai.area_available = lambda area: True

rec = A._auto_detect_collections([g[0] for g in GAMES])
check(calls["n"] == 1, "one batched AI call for all candidates (got %d)" % calls["n"])
check([r["norm_key"] for r in rec] == ["sonicmegacollection"],
      "only the confident, member-bearing bundle recorded (got %s)"
      % [r["norm_key"] for r in rec])

got = compilations.get_collection(scratch, "sonicmegacollection")
check(bool(got), "collection persisted to collections.sqlite")
check(got and len(got.get("members") or []) == 2,
      "both members stored (got %s)" % (len(got.get("members") or []) if got else None))
check(compilations.get_collection(scratch, "segagenesisclassics") is None,
      "member-less bundle recorded nothing")
check(compilations.get_collection(scratch, "metroidprimetrilogy") is None,
      "below-threshold bundle recorded nothing")

print("5. already-known collections aren't re-asked")
cands2 = A._collection_candidates([g[0] for g in GAMES])
check("sonicmegacollection" not in [c["norm_key"] for c in cands2],
      "recorded collection drops out of the candidate set")

print("6. AI unavailable -> no-op, never raises")
A.ai.area_available = lambda area: False
check(A._auto_detect_collections([g[0] for g in GAMES]) == [], "returns [] with no AI configured")

print()
if FAIL:
    print("FAILED (%d): %s" % (len(FAIL), "; ".join(FAIL)))
    sys.exit(1)
print("ALL CHECKS PASSED")
