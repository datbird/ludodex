#!/usr/bin/env python3
"""The rule that decides whether a provider's candidate IS the game we asked for.

One gate, shared by every provider, because each of them got this wrong in its own way:

  * ScreenScraper searched cleaned VARIANTS of a title and then scored the candidate
    against the variant it had searched — so a subtitle-stripped variant matched its own
    parent game perfectly. "Quake II: The Reckoning" became Quake II, "ELDEN RING
    NIGHTREIGN" became a Game Boy demake called "Elden Ring GB", and 191 titles ended up
    sharing 86 ids with each other.
  * SteamGridDB had no name check at all — `return items[0].get("id")`, the first
    autocomplete result whatever it was named.
  * IGDB alone was already strict (a literal exact-name lookup), which is why it has the
    fewest collisions, and it is the standard the other two are being held to here.

The rule: measure BOTH directions against the title the user owns.

  qc  how much of the OWNED title the candidate covers. Gated at 0.8 — a candidate
      missing "The Reckoning" is a different product however exactly it matches the rest.
      This is the direction that stops a parent, a sequel or a collection standing in.
  nc  how much of the CANDIDATE the owned title covers. Scored, never gated, because
      providers append edition, region and platform words we do not carry.

Search variants stay free to be as loose as they like; they exist to FIND rows a provider
spells differently. They just do not get to redefine what game was asked for.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import titlenorm      # noqa: E402

# How much of the owned title a candidate must account for. 0.8 tolerates one dropped
# article or edition word in a long title while still refusing a missing subtitle.
COVER_MIN = 0.8


def score(owned, cand_name, year=None, cand_year=None):
    """Is `cand_name` acceptable for one of the OWNED titles? Returns (ok, score). Split out of `_ss_match` because the acceptance test used to be
    applied to whichever cleaned VARIANT had been searched, not to the title the user
    owns. Variants exist so that "Mega Man X4" can find ScreenScraper's "Megaman X4";
    they must not also let a subtitle-stripped variant match its own parent game:
    "Half-Life: Opposing Force" searched as "Half-Life" scored a perfect 1.0 against
    Half-Life and was recorded as Opposing Force's identity. Live that bound 191 titles
    onto 86 ScreenScraper ids, and the loser of each collision inherited the winner's art.

    So both directions are measured against the owned title:
      qc  how much of the OWNED title the candidate covers — a candidate missing
          "opposing force" is a different game, however exactly it matches the rest;
      nc  how much of the candidate the owned title covers — tolerant, because
          providers append edition and region words we do not carry.
    """
    best = (False, 0.0)
    cn = titlenorm.norm(cand_name or "")
    ntok = set(cn.split())
    if not ntok:
        return best
    for t in (owned or []):
        qn = titlenorm.norm(t or "")
        qtok = set(qn.split())
        if not qtok:
            continue
        inter = len(qtok & ntok)
        qc = inter / len(qtok)
        nc = inter / len(ntok)
        # Token sets cannot see that "Megaman X4" and "Mega Man X4" are the same game:
        # they share ONE token and score 0.50. Squashing the spaces out makes an equal
        # string an exact match whatever the word breaks.
        if qn.replace(" ", "") == cn.replace(" ", ""):
            qc = nc = 1.0
        score = qc + nc + (0.4 if year and cand_year == str(year) else 0)
        if qc >= COVER_MIN and score > best[1]:
            best = (True, score)
        elif not best[0] and score > best[1]:
            best = (False, score)
    return best
