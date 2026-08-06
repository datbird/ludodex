#!/usr/bin/env python3
"""Vision must be able to say "that is the wrong game" — and be acted on (#28).

Live, Police Quest: In Pursuit of the Death Angel was showing Police Quest II: The
Vengeance's cover as its primary. The proximate cause was a provider collision (SS bound
both titles to id 31435), but the deeper one is that vision could not have saved it: the
art prompt says a "different-game image is rejected outright", and then `pick_art`
returns nothing but an index, clamped into range. There was no way to express rejection,
so the wrong cover was picked with a confident-sounding reason.

Same-series siblings are the hard case and the common one. A sequel's art shares the
wordmark, the artist, the palette and the layout, and differs by a numeral or a subtitle
— exactly the signal a resolution-and-composition ranking is blind to. This is a
different failure from the regional-title case (#26): 'The Story of Thor' IS Beyond
Oasis and is worth keeping, while Police Quest II art is simply not this game's art and
is worth deleting.

So: a reject verdict, a way to return no pick at all when everything is wrong, and — at
high confidence — a durable ban so the asset is not re-downloaded on the next sync.

Offline. No network, no model calls.
"""
import os
import sqlite3
import sys

import test_support

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    test_support.isolate("ludodex-artreject-")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from server import ai
    from server import app as srv

    # ---- 1. the prompt asks the question ----------------------------------------
    p = ai.area_prompt("art", kind="cover", title="Police Quest: In Pursuit of the "
                       "Death Angel", count=3, aliases="")
    low = p.lower()
    check("prompt distinguishes a SERIES ENTRY, not just a series",
          "sequel" in low or "series entry" in low or "numeral" in low)
    check("prompt tells it to read the number/subtitle",
          ("number" in low or "numeral" in low) and "subtitle" in low)
    check("prompt asks for a machine-readable reject list", '"reject' in low)
    check("prompt allows returning no pick when everything is wrong",
          "null" in low or "no acceptable" in low)
    check("prompt still carries the regional-title rule (#26 must not regress)",
          "story of thor" in low and "regional" in low)
    check("prompt separates 'wrong region, keep it' from 'wrong game, drop it'",
          "same game" in low and ("different game" in low or "not this game" in low))

    # ---- 2. pick_art surfaces the verdict ---------------------------------------
    parse = ai._parse_art_verdict

    v = parse({"index": 2, "reason": "sharp",
               "rejects": [{"index": 1, "confidence": 0.95, "why": "Police Quest II"}]},
              n=3)
    check("index is 0-based", v["index"] == 1)
    check("rejects are 0-based too", v["rejects"][0]["index"] == 0)
    check("reject confidence survives", v["rejects"][0]["confidence"] == 0.95)
    check("reject reason survives", "Police Quest II" in v["rejects"][0]["why"])

    check("no rejects key -> empty list, old behaviour intact",
          parse({"index": 1, "reason": "x"}, n=2)["rejects"] == [])
    check("a null index still means candidate 1 when nothing was rejected",
          parse({"index": None, "reason": "x"}, n=2)["index"] == 0)
    check("an out-of-range index is still clamped",
          parse({"index": 99}, n=3)["index"] == 2)

    allbad = parse({"index": None, "reason": "none of these are this game",
                    "rejects": [{"index": i + 1, "confidence": 0.9} for i in range(3)]},
                   n=3)
    check("everything rejected -> NO pick, rather than a forced one",
          allbad["index"] is None)
    check("everything rejected -> every reject kept", len(allbad["rejects"]) == 3)

    mixed = parse({"index": 1, "reason": "ok",
                   "rejects": [{"index": 1, "confidence": 0.9}]}, n=2)
    check("a model that rejects its own pick does not get to keep it",
          mixed["index"] is None)

    check("a reject naming a candidate that does not exist is dropped",
          parse({"index": 1, "rejects": [{"index": 9, "confidence": 1}]},
                n=2)["rejects"] == [])
    check("a non-numeric confidence is treated as unknown, never as certain",
          parse({"index": 1, "rejects": [{"index": 2, "confidence": "very"}]},
                n=2)["rejects"][0]["confidence"] == 0.0)

    # ---- 3. the verdict is ACTED on ---------------------------------------------
    idx = os.path.join(os.environ["LUDODEX_DATA"], "media-index.sqlite")
    con = sqlite3.connect(idx)
    con.execute("DROP TABLE IF EXISTS media")
    con.execute("CREATE TABLE media(id INTEGER PRIMARY KEY, norm_key TEXT, kind TEXT, "
                "provider TEXT, ref TEXT, chosen INT DEFAULT 0, ai_pick INT, "
                "system TEXT, game_key TEXT)")
    rows = [(1, "pq1", "cover", "screenscraper", "ss://31435", 1),
            (2, "pq1", "cover", "steamgriddb", "sgdb://a", 0),
            (3, "pq1", "cover", "igdb", "igdb://co4xgs", 0)]
    for r in rows:
        con.execute("INSERT INTO media(id,norm_key,kind,provider,ref,chosen) "
                    "VALUES(?,?,?,?,?,?)", r)
    con.commit()

    cands = [{"id": 1, "provider": "screenscraper", "ref": "ss://31435"},
             {"id": 2, "provider": "steamgriddb", "ref": "sgdb://a"},
             {"id": 3, "provider": "igdb", "ref": "igdb://co4xgs"}]

    import mediaflags
    n = srv._apply_art_rejects(
        con, "pq1", "cover", cands,
        [{"index": 0, "confidence": 0.95, "why": "PQ2 art",
          "depicts": "Police Quest II: The Vengeance"},
         {"index": 1, "confidence": 0.4, "why": "maybe", "depicts": "Some Other Game"}],
        title="Police Quest: In Pursuit of the Death Angel")
    con.commit()
    banned = mediaflags.banned_set()

    check("a confident reject is BANNED, so it is not re-downloaded",
          ("pq1", "cover", "screenscraper", "ss://31435") in banned)
    check("an unsure reject is NOT banned — a guess must not delete art",
          ("pq1", "cover", "steamgriddb", "sgdb://a") not in banned)
    check("the banned row loses `chosen` immediately",
          con.execute("SELECT chosen FROM media WHERE id=1").fetchone()[0] == 0)
    check("the unsure row is left alone in the index",
          con.execute("SELECT COUNT(*) FROM media WHERE id=2").fetchone()[0] == 1)
    check("the innocent candidate is untouched",
          con.execute("SELECT COUNT(*) FROM media WHERE id=3").fetchone()[0] == 1)
    check("it reports how many it banned", n == 1)

    check("no rejects is a no-op",
          srv._apply_art_rejects(con, "pq1", "cover", cands, [], title="X") == 0)

    # ---- the 624-cover lesson ----------------------------------------------------
    # A model asked for "rejects" lists the candidates it did NOT pick, not the ones
    # that are wrong. Run live, that banned 624 correct covers in a single pass — Aces &
    # Adventures, Across the Obelisk, Actraiser Renaissance, Age of Empires II DE, all
    # deleted as "wrong game". So a reject must carry a CHECKABLE claim: name the game
    # the image is really for, and let the deterministic gate confirm it is a different
    # one. A verdict nothing can check is not evidence.
    con.execute("UPDATE media SET chosen=0")
    con.execute("INSERT INTO media(id,norm_key,kind,provider,ref,chosen) "
                "VALUES(50,'aoe2','cover','igdb','igdb://coar0y',1)")
    con.commit()
    c2 = [{"id": 50, "provider": "igdb", "ref": "igdb://coar0y"}]
    t2 = "Age of Empires II: Definitive Edition"

    check("a confident reject with NO named game bans nothing",
          srv._apply_art_rejects(con, "aoe2", "cover", c2,
                                 [{"index": 0, "confidence": 0.99, "why": "worse"}],
                                 title=t2) == 0)
    check("a reject naming THIS game is a preference, not a fault",
          srv._apply_art_rejects(con, "aoe2", "cover", c2,
                                 [{"index": 0, "confidence": 0.99,
                                   "depicts": "Age of Empires II Definitive Edition"}],
                                 title=t2) == 0)
    check("...and naming a genuinely different game still bans",
          srv._apply_art_rejects(con, "aoe2", "cover", c2,
                                 [{"index": 0, "confidence": 0.99,
                                   "depicts": "Age of Empires IV"}],
                                 title=t2) == 1)
    check("with no title to check against, nothing is banned",
          srv._apply_art_rejects(con, "aoe2", "cover", c2,
                                 [{"index": 0, "confidence": 0.99,
                                   "depicts": "Anything At All"}]) == 0)
    check("a reject naming a known REGIONAL name of this game is not a fault",
          srv._apply_art_rejects(con, "aoe2", "cover", c2,
                                 [{"index": 0, "confidence": 0.99,
                                   "depicts": "Contra III: The Alien Wars"}],
                                 title="Super Probotector: Alien Rebels",
                                 aliases=["Contra III: The Alien Wars"]) == 0)
    check("...but an unrelated game is still banned even with aliases present",
          srv._apply_art_rejects(con, "aoe2", "cover", c2,
                                 [{"index": 0, "confidence": 0.99,
                                   "depicts": "Super Mario World"}],
                                 title="Super Probotector: Alien Rebels",
                                 aliases=["Contra III: The Alien Wars"]) == 1)

    check("the prompt demands the image be identified, not just disliked",
          "depicts" in ai.area_prompt("art", kind="cover", title="x", count=2,
                                      aliases="").lower())

    # ---- 4. a lone candidate is still questioned --------------------------------
    # Ranking needs two candidates; "is this even this game?" does not. A single asset
    # is the dangerous case — nothing competes with it, so it is served unchallenged.
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "server", "app.py")).read()
    check("no vision path skips a game for having only one candidate",
          "len(cands) < 2" not in src)

    # ---- vision must judge the art the page actually SHOWS ----------------------
    # The pass filtered candidates to the system-neutral bucket, but a console entry
    # serves its system-siloed art (DESIGN §11.4), so the model spent every call judging
    # images the page would never display. Live: 1,608 chosen assets system-siloed, zero
    # AI verdicts among them, and Beyond Oasis still wearing "The Story of Thor" after a
    # complete Lite pass. Candidates must be grouped by the SAME tuple `chosen` is keyed
    # by — (system, game_key) — not filtered down to one value of it.
    adj = src[src.index("def _ai_adjudicate_game"):]
    adj = adj[:adj.index("\ndef ", 10)]
    check("the vision pass no longer judges only the neutral bucket",
          "COALESCE(system,'')=''" not in adj)
    check("candidates are grouped per (system, game_key)",
          'r["system"] or ""' in adj and 'r["game_key"] or ""' in adj)
    check("the chosen reset is scoped to the same silo it judged",
          "COALESCE(system,'')=?" in adj)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
