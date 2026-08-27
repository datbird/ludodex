#!/usr/bin/env python3
"""One provider id is one game — enforced, not merely checked (#30).

I9 caught 111 colliding provider ids, and fixing the two matchers that caused most of them
still left a residue no gate could see, because the gate is applied to the string that was
SEARCHED. An AI-proposed alias drops a distinguishing word — "Ninja Gaiden Sigma 2"
searched as "Ninja Gaiden 2" — the provider answers with its nearest record, and the
candidate matches that alias perfectly. Aliases widen acceptance exactly the way
subtitle-stripped variants did, and unlike variants they cannot be constrained by token
overlap: a good alias ("Rockman X4" for "Mega Man X4") shares no tokens at all.

So the constraint moves to where it IS decidable. Two titles arriving at the same provider
id means at least one is wrong, and that is checkable without knowing which. A searched id
another game already holds is refused outright, and nothing is written, so the loser is
re-asked later rather than remembered as having no match.

Narrow on purpose: `steam_appid` and `manual` are exempt. An appid lookup is exact, and a
DLC or beta appid legitimately resolves to its parent's record — the provider modelling one
product where our catalog lists two.

Offline. No network.
"""
import os, sqlite3, sys
import test_support
PASS=[]
def check(l,c):
    PASS.append(c); print("  %s   %s"%("ok " if c else "FAIL",l))
    if not c: sys.exit("FAILED: "+l)
def main():
    d=test_support.isolate("ludodex-uniq-")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "ludodex"))
    import provider_ids as P
    con=sqlite3.connect(":memory:"); P.ensure_tables(con)
    check("first searched match is recorded",
          P.record(con,"screenscraper","ninja gaiden sigma 2",25266,
                   platform="pc")==25266)
    check("a second game cannot take the same searched id",
          P.record(con,"screenscraper","ninja gaiden ii black",25266,
                   platform="pc")==0)
    r=P.cached(con,"screenscraper","ninja gaiden ii black",platform="pc")
    check("the refusal is recorded as a MISS, not as nothing (it WAS attempted)",
          r is not None and r[0]==0)
    check("...and it is tagged so the reason is legible", r[1]=="collision")
    check("the original keeps its id",
          P.cached(con,"screenscraper","ninja gaiden sigma 2",
                   platform="pc")[0]==25266)
    check("re-recording the SAME game with the same id still works",
          P.record(con,"screenscraper","ninja gaiden sigma 2",25266,
                   platform="pc")==25266)
    check("a different id for a second game is fine",
          P.record(con,"screenscraper","ninja gaiden ii black",99999,
                   platform="pc")==99999)
    # appid + manual are exempt: a DLC appid legitimately resolves to its parent
    check("a steam_appid match may share an id",
          P.record(con,"steamgriddb","cult of the lamb",5294443,
                   matched_by="steam_appid")==5294443)
    check("...and so may the DLC that resolves to the same record",
          P.record(con,"steamgriddb","cult of the lamb heretic pack",5294443,
                   matched_by="steam_appid")==5294443)
    check("a manual decision is never refused",
          P.record(con,"steamgriddb","something else",5294443,
                   matched_by="manual")==5294443)
    check("a miss is never blocked by uniqueness",
          P.record(con,"screenscraper","some other game",0,platform="pc")==0)
    check("holder() names the game already holding an id",
          P.holder(con,"screenscraper",25266)=="ninja gaiden sigma 2")
    check("holder() ignores the asking game itself",
          P.holder(con,"screenscraper",25266,"ninja gaiden sigma 2") is None)
    print("\n%d/%d passed"%(sum(PASS),len(PASS)))
main()
