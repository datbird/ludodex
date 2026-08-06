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

# Words that carry no identity: a candidate may omit them freely, and they must never be
# the reason a title is refused. Everything NOT in here is treated as distinguishing.
#
# 0.8 fractional coverage was the first attempt and is subtly wrong in both directions.
# It let "Mega Man Legacy Collection" stand in for "Mega Man X Legacy Collection" — the
# single token "x" is 20% of a five-word title, so it landed exactly on the threshold —
# while refusing "Mass Effect 2" for our own disambiguated "Mass Effect 2 (2021)",
# because the year we added counted as a real word. Whether a missing word matters is
# not a question of how long the title is.
NOISE = {
    # editions and packaging
    "edition", "editions", "deluxe", "premium", "ultimate", "complete", "definitive",
    "collectors", "collector", "goty", "anniversary", "enhanced", "remaster",
    "remastered", "remake", "redux", "hd", "sd", "4k", "uhd", "classic", "original",
    "standard", "special", "gold", "platinum", "digital", "bundle", "pack",
    # platform / distribution noise
    "pc", "steam", "windows", "version", "release", "the", "a", "an", "of", "and",
}
# A bare 4-digit year is ours, not the title's: entries are disambiguated as
# "Mass Effect 2 (2021)" / "(2023)" when a store lists the same game twice.
YEAR = __import__("re").compile(r"^(19|20)\d{2}$")

# How far two release years may differ and still be the same release. Regional launches
# straddle a new year routinely; an original and its remake do not.
YEAR_TOLERANCE = 1


def _year(v):
    """`v` as a 4-digit year, or None if it is absent or not one."""
    t = str(v or "").strip()
    return int(t) if t.isdigit() and len(t) == 4 else None


def _significant(tokens):
    """The tokens that actually identify a game."""
    return {t for t in tokens if t not in NOISE and not YEAR.match(t)}


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
        # The gate is not fractional: EVERY distinguishing word of the owned title has
        # to appear in the candidate. "Boltgun" is not "Boltgun 2", "Cult of the Lamb"
        # is not its Heretic Pack, and "Hammerwatch II" is not "Heroes of Hammerwatch
        # II" — in each case exactly one word tells them apart, and that word is the
        # whole point. Noise words and our own year suffixes are exempt.
        sig = _significant(qtok)
        covered = bool(sig) and sig <= ntok
        # Token sets cannot see that "Megaman X4" and "Mega Man X4" are the same game:
        # they share ONE token and score 0.50. Squashing the spaces out makes an equal
        # string an exact match whatever the word breaks.
        if qn.replace(" ", "") == cn.replace(" ", ""):
            qc = nc = 1.0
            covered = True
        # A year DISAGREEMENT is disqualifying, not merely unrewarded. The NOISE rule
        # above strips a 4-digit year so our own "Mass Effect 2 (2021)" matches the
        # provider's "Mass Effect 2" — correct for a duplicate store listing, and
        # exactly backwards for a remake, whose title is identical to its original and
        # whose year is the only thing separating them. Live, Resident Evil 4 (2023)
        # took ScreenScraper 4750, the 2005 GameCube game, and wore its box art.
        #
        # Only ever applied when BOTH years are known and numeric: an absent year is not
        # evidence of anything, and must never turn into a refusal.
        y1, y2 = _year(year), _year(cand_year)
        if y1 and y2 and abs(y1 - y2) > YEAR_TOLERANCE:
            continue
        score = qc + nc + (0.4 if y1 and y1 == y2 else 0)
        if covered and score > best[1]:
            best = (True, score)
        elif not best[0] and score > best[1]:
            best = (False, score)
    return best
