#!/usr/bin/env python3
"""The rules that decide whether a provider is asked correctly, and what a non-answer means.

datbird's priority, stated plainly: *"I'm far less concerned about the current library
being correct and more concerned about the systematic/programmatic being correct and not
experiencing the issue again in the future."*

Two rule families, both of which were live defects found from one report ("does Mass
Effect 2 really not have a SS match?"), and neither of which had any test:

A. QUERY NORMALISATION — what we send the provider. Three separate live misses:
     - trademark symbols passed through, often GLUED to a word, so "ACE COMBAT(tm)7" is
       one nonsense token to a search engine (cost Ace Combat 7, Age of Empires III);
     - edition suffixes: the catalog stores the edition you own, the provider stores the
       game (cost Mirror of Fate HD, Chernobylite Complete Edition);
     - a game with no per-system id got only its RAW title tried, because the
       cross-system pass was capped at one query variant — correct when it is a fallback,
       catastrophic when it is a PC game's only search, which is most of a Steam library.

B. FAILURE IS NOT A MISS — all three providers recorded "we could not look" as "it is not
   there". ScreenScraper via a swallowed error or an exhausted budget; SteamGridDB via
   `except Exception: pass; return None`; IGDB via `except: hits = []` falling through to
   a matched_by='none' write. A transient API failure became a stored fact.

B is the one that makes the whole thing self-correcting: while a non-answer stays out of
the negative cache, any later improvement to A retroactively rescues every game that
missed. That is exactly what happened — fixing A flipped 229 games from "no match" to
matched with no repair logic at all.
"""
import os
import re
import sys

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ludodex")
sys.path.insert(0, os.path.dirname(DIR))   # for `import server`
sys.path.insert(0, DIR)
import test_support                              # noqa: E402
test_support.isolate("ludodex-qrules-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    from server import app as srv
    import media_fetch as _mf
    import screenscraper as ss

    # Capture the queries _ss_match actually sends, without touching the network.
    sent = []

    def spy(creds, q, systemeid=None, limit=8):
        sent.append((q, systemeid))
        return []

    real = ss.jeu_recherche
    ss.jeu_recherche = spy
    try:
        print("A1. trademark symbols never reach the provider, and do not glue words")
        sent.clear()
        try:
            srv._ss_match(["ACE COMBAT™7: SKIES UNKNOWN"], ["pc"])
        except Exception:
            pass                                   # a total miss may raise; queries still recorded
        joined = " | ".join(q for q, _ in sent)
        check("no ™/® survives into a query: %r" % joined[:70],
              not re.search(r"[™®©℠]", joined))
        check("the symbol became a SPACE, so '7' stays its own token",
              any(re.search(r"\bcombat\b\s*7", q, re.I) for q, _ in sent))

        print("A1b. typographic punctuation is folded to ASCII")
        # Worse than the trademark case: a curly apostrophe returns ZERO candidates from
        # ScreenScraper, so those titles were removed from consideration entirely rather
        # than merely mis-scored. Steam stores plenty of them.
        sent.clear()
        try:
            srv._ss_match(["Baldur\u2019s Gate 3"], ["pc"])
        except Exception:
            pass
        allq = " | ".join(q for q, _ in sent)
        check("no curly quote or dash survives: %r" % allq[:60],
              not re.search(r"[\u2018\u2019\u201c\u201d\u2013\u2014\u2026]", allq))
        check("it became a straight apostrophe, not nothing",
              any("baldur's gate 3" == q.lower() for q, _ in sent))

        print("A2. an edition suffix is tried stripped as well as intact")
        sent.clear()
        try:
            srv._ss_match(["Castlevania: Lords of Shadow - Mirror of Fate HD"], ["pc"])
        except Exception:
            pass
        qs = [q.lower() for q, _ in sent]
        check("the full title is tried", any("mirror of fate hd" in q for q in qs))
        check("and the edition-stripped form is too",
              any(q.rstrip().endswith("mirror of fate") for q in qs))

        print("A3. more edition words than just HD")
        for word in ("Remastered", "Definitive Edition", "Complete Edition",
                     "Deluxe Edition", "Game of the Year Edition", "Anniversary Edition"):
            sent.clear()
            try:
                srv._ss_match(["Some Game %s" % word], ["pc"])
            except Exception:
                pass
            check("%r is stripped in some variant" % word,
                  any(q.strip().lower() == "some game" for q, _ in sent))

        print("A4. a game with NO per-system id still gets every variant")
        # This is the PC case: ss.systeme_id('pc') is None, so the cross-system pass is
        # the only search there is. Capping it at one query meant most of a Steam library
        # was matched on its raw stored title alone.
        check("screenscraper really has no id for pc (the premise)",
              ss.systeme_id("pc") is None)
        sent.clear()
        try:
            srv._ss_match(["Castlevania: Lords of Shadow - Mirror of Fate HD"], ["pc"])
        except Exception:
            pass
        check("more than one query variant was sent: %d" % len(sent), len(sent) > 1)
        check("every one went cross-system", all(sid is None for _q, sid in sent))
    finally:
        ss.jeu_recherche = real

    print("B1. ScreenScraper: a completed search that finds nothing IS a miss")
    ss.jeu_recherche = lambda creds, q, systemeid=None, limit=8: []
    try:
        check("returns None rather than raising",
              srv._ss_match(["Nothing At All Here 4242"], ["pc"]) is None)
    finally:
        ss.jeu_recherche = real

    print("B2. ScreenScraper: a search that never succeeds RAISES")
    def boom(creds, q, systemeid=None, limit=8):
        raise RuntimeError("provider down")
    ss.jeu_recherche = boom
    try:
        raised = False
        try:
            srv._ss_match(["Mass Effect 2"], ["pc"])
        except Exception:
            raised = True
        check("raises instead of reporting a miss", raised)
    finally:
        ss.jeu_recherche = real

    print("B3. SteamGridDB: same rule")
    real_get = _mf._sgdb_get
    _mf._sgdb_get = lambda path, key: (_ for _ in ()).throw(RuntimeError("sgdb down"))
    try:
        raised = False
        try:
            _mf._sgdb_game_id("k", appid="123", title="Anything")
        except Exception:
            raised = True
        check("a failed lookup raises rather than returning None", raised)
    finally:
        _mf._sgdb_get = real_get

    _mf._sgdb_get = lambda path, key: {"data": []}
    try:
        check("but a successful lookup with no results is still a miss (None)",
              _mf._sgdb_game_id("k", appid=None, title="Nothing Here") is None)
    finally:
        _mf._sgdb_get = real_get

    print("B4. IGDB: a failed search is not written as matched_by='none'")
    src = open(os.path.join(DIR, "igdb_enrich.py"), encoding="utf-8").read()
    # the resolve loop's search-failure branch must SKIP the row; falling through reaches
    # the `matched_by='none' if not iid` write, which is the defect.
    i = src.find("igdb_enrich: search failed for")
    check("the failure branch is reachable and logged", i > 0)
    check("it skips the row rather than falling through to the resolution write",
          "continue" in src[i:i + 320])
    check("and the write it would have reached still stamps 'none' for a real miss",
          '"name" if iid else "none"' in src)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
