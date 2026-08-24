#!/usr/bin/env python3
"""The free, keyless sources — and three ways their fetchers were quietly lossy.

  * HAND-ROLLED CSV LOSES DATA THAT CONTAINS A COMMA. wikidata_ids split each line on
    the first comma and then wrote its values back with `.replace(",", "")`, which does
    not escape a value, it ALTERS it. Its sibling tgdb_freemap already uses the csv
    module; there was no reason for this one not to.
  * GITHUB TRUNCATES A BIG TREE AND SAYS SO IN THE PAYLOAD. libretro_dats read
    `tree` and ignored `truncated: true`, so a truncated listing was indistinguishable
    from "this collection has fewer systems today" — and it fetched the whole recursive
    tree once per collection, twice per --fetch, to find that out.
  * PRETENDING TO BE CHROME. steam_tags sent a spoofed desktop Chrome User-Agent to
    SteamSpy while every other module in the stack identifies itself as ludodex — and
    wikidata_ids carries a comment about exactly why that is the wrong move.

Nothing here touches the network: urlopen is replaced.
"""
import io
import json
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_support
    data = test_support.isolate("ludodex-prov-free-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import config
    import wikidata_ids as W
    import libretro_dats as L
    import steam_tags as ST

    print("1. A VALUE WITH A COMMA IN IT SURVIVES THE ROUND TRIP")
    # Wikidata really does carry these: a MobyGames slug or a TheGamesDB title can hold
    # a comma, and silently deleting it produces a pointer that resolves to nothing.
    body = ('igdb,val\n'
            '"grand-theft-auto","grand_theft_auto,_vice_city"\n'
            'plain-slug,plain-value\n')
    W.urllib.request.urlopen = lambda req, timeout=None: Resp(body.encode())
    config.set_("wikidata_ids_namespaces", "mobygames")
    p = W.fetch(force=True)
    got = dict((slug, val) for _ns, slug, val in W.rows(p))
    check("the comma is still there: %r" % got.get("grand-theft-auto"),
          got.get("grand-theft-auto") == "grand_theft_auto,_vice_city")
    check("and the ordinary row is unharmed", got.get("plain-slug") == "plain-value")

    print()
    print("2. A TRUNCATED GITHUB TREE IS NOT A SMALLER CATALOGUE")
    calls = {"n": 0}
    tree = {"truncated": False,
            "tree": [{"path": "metadat/no-intro/Nintendo - NES.dat"},
                     {"path": "metadat/no-intro/Sega - Genesis.dat"},
                     {"path": "metadat/redump/Sony - PlayStation.dat"},
                     {"path": "metadat/no-intro/README.md"}]}

    def serve(req, timeout=None):
        calls["n"] += 1
        return Resp(json.dumps(tree).encode())

    L.urllib.request.urlopen = serve
    L._tree_cache.clear()
    names = L.systems("no-intro")
    check("it lists the .dat files and nothing else: %s" % names,
          names == ["Nintendo - NES.dat", "Sega - Genesis.dat"])
    L.systems("redump")
    check("and the tree is fetched ONCE for every collection, not once each: %d"
          % calls["n"], calls["n"] == 1)

    tree["truncated"] = True
    L._tree_cache.clear()
    calls["n"] = 0
    trunc = L.systems("no-intro")
    check("a truncated listing is refused, not passed off as the whole thing: %s"
          % trunc, trunc == [])

    print()
    print("3. WE SAY WHO WE ARE")
    src = open(os.path.join(root, "ludodex", "steam_tags.py"), encoding="utf-8").read()
    check("no spoofed browser User-Agent is left", "Mozilla/5.0" not in src)
    check("and the agent identifies ludodex, like every other module: %r" % ST.UA,
          "ludodex" in ST.UA.lower())
    check("with a contact URL, which is what the service asks for",
          "http" in ST.UA)

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
