#!/usr/bin/env python3
"""Verify the legacy fuzzy-match classifier (task #9). `_name_anchor_class` must KEEP
anchored subtitle/prefix variants and FLAG interior-word mis-matches (the 'journey' ->
'The Sims 4: Journey to Batuu' class), so the scrub clears the bad without nuking the good."""
import igdb_enrich as E


def main():
    C = E._name_anchor_class
    # exact -> always fine
    assert C("journey", ["Journey"]) == "exact", "exact normalized match"
    # anchored: nk is a contiguous run at START or END of the title -> legit variant, KEEP
    assert C("1943", ["1943: The Battle of Midway"]) == "anchored", "prefix subtitle"
    assert C("abadox", ["Abadox: The Deadly Inner War"]) == "anchored", "prefix subtitle 2"
    assert C("007 agent under fire", ["James Bond 007: Agent Under Fire"]) == "anchored", \
        "franchise-prefix, nk anchored at END"
    # interior: nk is a common word buried mid-title -> the egregious class, FLAG
    assert C("journey", ["The Sims 4: Journey to Batuu"]) == "interior", "journey -> sims 4"
    assert C("ball", ["Dragon Ball Z: Super Butouden 3"]) == "interior", "ball -> dbz"
    assert C("chess", ["Fritz & Chesster: Learn to Play Chess Vol. 1"]) == "interior", "chess"
    # norun: not a contiguous run at all -> murky, left alone by default
    assert C("75 bingo", ["Bingo 75"]) == "norun", "reordered tokens"
    assert C("17 plus 4", ["Blackjack"]) == "norun", "semantic, no token overlap"
    # best-class-wins across multiple names: an alt name that anchors saves it
    assert C("rondo of blood", ["Akumajou Dracula X: Chi no Rondo",
                                "Castlevania: Rondo of Blood"]) == "anchored", \
        "alt name anchors -> keep"
    print("verify_scrub_fuzzy: OK")


if __name__ == "__main__":
    main()
