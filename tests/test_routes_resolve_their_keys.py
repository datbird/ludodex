#!/usr/bin/env python3
"""Every route that takes a game key must resolve it, never parse it inline.

THE BUG THIS CATCHES, at write time, with no server and no browser.

A game key has three shapes: `<norm_key>@<platform>`, a bare `<norm_key>`, and since
2026-08-25 a CARD key (`igdb:2155` / `title:<nk>`). One function understands all three:
`_split_entry_key`. On 2026-08-26 the grid started navigating by card keys and three
paths still assumed the old two, so the wand, the hero preference and every media lookup
silently queried for a game named "igdb:2155". All returned 200 with an empty body, so
nothing raised and 180 unit tests stayed green.

A route that splits a key itself is one that will miss the next shape. So: any route with
a key-shaped path parameter must hand it to the resolver, and a route that genuinely
takes something else has to say so out loud, here, by name.
"""
import os
import re
import sys

PASS = []

# Resolvers that understand every key shape. A route reaching one of these is safe.
RESOLVERS = ("_split_entry_key", "_resolve_entry", "_card_copies", "_card_entry")

# Path parameters that carry a GAME KEY and therefore must be resolved.
KEY_PARAMS = ("norm_key", "entry_key", "key", "nk")

# Routes whose key-shaped parameter is NOT a game key. Each one is named on purpose:
# an exemption that nobody has to justify is an exemption that hides the next bug.
# Each reason below was VERIFIED by reading the caller on 2026-08-26, not assumed. An
# exemption nobody has to justify is an exemption that hides the next bug, and a stale
# one fails this test rather than quietly widening.
EXEMPT = {
    # The detail panel passes `d.norm_key` to these, never the key that opened the
    # panel: OwnershipEditor takes nk={d.norm_key}, and identity takes d.norm_key
    # directly. They receive a real title key by construction.
    "/api/games/{norm_key}/ownership":
        "caller is OwnershipEditor, which is given d.norm_key",
    "/api/games/{norm_key}/releases":
        "same OwnershipEditor nk prop, so a real norm_key",
    "/api/games/{norm_key}/identity/{provider}":
        "called as api.setIdentityDisabled(d.norm_key, ...)",
    # The publish and device-wants routes are addressed from the publish tables, which
    # store ENTRY keys. They are never reached with a key the grid navigated by.
    "/api/devices/{dev_id}/wants/{norm_key:path}":
        "key comes from the device wants table, already an entry key",
    "/api/devices/{dev_id}/publish/{entry_key:path}":
        "key comes from the publish intent table, already an entry key",
    "/api/games/{norm_key:path}/publish":
        "title-level publish intent, addressed by norm_key from the publish table",
    # The unfold pin is matched against `entry_key` at rebuild time, so an entry key is
    # the only shape that can ever work. Resolving a card key here would silently pin
    # one copy while the user meant the card.
    "/api/cards/unfold/{entry_key:path}":
        "the pin is matched against games.entry_key by build_library",
}


def check(label, cond, detail=""):
    PASS.append((label, bool(cond)))
    print("  %s   %s%s" % ("ok " if cond else "FAIL", label,
                           "" if cond else "\n            " + str(detail)[:900]))
    if not cond:
        sys.exit("FAILED: " + label)


def routes(src):
    """[(path, funcname, body)] for every FastAPI route in the file."""
    out = []
    lines = src.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r'@app\.(get|post|put|delete|patch)\("([^"]+)"', line.strip())
        if not m:
            continue
        # the decorated function starts at the next `def`
        j = i + 1
        while j < len(lines) and not lines[j].startswith("def "):
            j += 1
        if j >= len(lines):
            continue
        name = lines[j][4:].split("(")[0]
        k = j + 1
        while k < len(lines) and not (lines[k].startswith(("def ", "@app.", "class "))):
            k += 1
        out.append((m.group(2), name, "\n".join(lines[j:k])))
    return out


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "server", "app.py"), encoding="utf-8").read()
    rs = routes(src)
    check("the file parses into routes", len(rs) > 100, len(rs))

    offenders = []
    guarded = 0
    for path, name, body in rs:
        params = re.findall(r"\{([a-z_]+)(?::[a-z]+)?\}", path)
        if not any(p in KEY_PARAMS for p in params):
            continue
        if path in EXEMPT:
            continue
        if any(r + "(" in body for r in RESOLVERS):
            guarded += 1
            continue
        # Two things are actually dangerous, and only two.
        #
        # 1. A HAND-ROLLED PARSE. Splitting the key here means this route will miss the
        #    next shape, exactly as every route missed the card shape.
        if re.search(r"\.split\(['\"]@['\"]\)|rsplit\(['\"]@['\"]", body):
            offenders.append("%s (%s) parses the key itself instead of resolving it"
                             % (path, name))
            continue
        # 2. QUERYING BY norm_key WITH AN UNRESOLVED KEY. This is the shape that broke
        #    the media panel: the route asked the database for a game whose norm_key was
        #    "igdb:2155", got nothing, and returned an empty 200. Handing the key to a
        #    helper is fine; asking SQL about it directly is not.
        if re.search(r"norm_key\s*=\s*\?|WHERE norm_key", body):
            offenders.append("%s (%s) queries by norm_key without resolving the key"
                             % (path, name))

    check("at least some routes are guarded", guarded >= 5, guarded)
    check("no route parses or uses a game key without the resolver",
          not offenders, "\n            ".join(offenders))

    # the resolver itself must still know all three shapes
    body = src[src.index("def _split_entry_key"):]
    body = body[:body.index("\ndef ", 5)]
    check("the resolver handles the platform shape", '"@"' in body)
    check("the resolver handles the CARD shape", "_card_key_lookup" in body)
    check("the resolver falls back rather than inventing an entry",
          "return key, None" in body)

    # and every exemption must still name a real route, or it is stale cover
    live = {p for p, _, _ in rs}
    stale = [p for p in EXEMPT if p not in live]
    check("no exemption is stale", not stale, stale)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
