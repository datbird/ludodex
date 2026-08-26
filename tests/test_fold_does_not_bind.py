#!/usr/bin/env python3
"""A card grouping is a display decision, and must stay one.

Task #21 established the rule this enforces: a MATCH IS NOT AN INGEST. The fold reads a
provider's parent graph to decide which tile a game sits on, which is exactly the kind of
convenience that grows into an identity binding if nobody pins it down. So: the module
does no I/O, and assigning a card never alters a game_key.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import cardkey

    src = open(os.path.join(root, "ludodex", "cardkey.py"), encoding="utf-8").read()
    for banned in ("import sqlite3", "import urllib", "import requests",
                   "import http", "import socket", "open("):
        check("cardkey does no I/O: no %r" % banned, banned not in src)
    # The docstrings NAME matchgate, deliberately, to say where identity binding lives.
    # What must not exist is an import of it, so check the import, not the word.
    code = [l for l in src.split("\n") if l.startswith(("import ", "from "))]
    check("cardkey imports nothing at all", code == [])
    check("so it cannot reach matchgate", not any("matchgate" in l for l in code))

    graph = {193359: (0, None, None), 1968: (11, None, 193359)}
    entries = [("rayman legends@pc", "igdb:1968", "Rayman Legends")]
    before = [tuple(e) for e in entries]
    got = cardkey.assign(entries, graph)
    check("assign does not mutate its input", [tuple(e) for e in entries] == before)
    check("the game_key is not rewritten", entries[0][1] == "igdb:1968")
    check("only the card moves", got["rayman legends@pc"] == "igdb:193359")

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
