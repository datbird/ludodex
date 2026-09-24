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
a key-shaped path parameter must take it through a key DEPENDENCY (`BaseKey`, `EntryKey`,
`BaseNk`, `CollBaseKey` in server/app.py, which FastAPI resolves through
`_split_entry_key` before the handler runs), or hand it to a helper that resolves it,
and a route that genuinely takes something else has to say so out loud, here, by name.
Resolving it by hand inside the route is itself a failure: that line is the one the next
route forgets.
"""
import os
import re
import sys

PASS = []

# The key dependencies, and the path parameter each one reads. FastAPI binds a
# dependency's parameter to the path parameter of the SAME NAME, so a route that
# declares `BaseKey` on a `{nk}` path would silently read a query parameter instead.
KEY_DEPS = {"BaseKey": "norm_key", "EntryKey": "norm_key", "BaseNk": "nk",
            "CollBaseKey": "coll_key"}

# Helpers that take the raw key and resolve it themselves (they return the row, or
# store under the base title). A route handing its key to one of these is safe.
ROW_RESOLVERS = ("_resolve_entry", "_card_copies", "_card_entry", "_store_upload")

# Path parameters that carry a GAME KEY and therefore must be resolved.
KEY_PARAMS = ("norm_key", "entry_key", "key", "nk", "coll_key")

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
    # Verified 2026-09-23. The attribute editor calls these with d.norm_key, and the
    # resolve modal's notes save and its merge are handed `base`, which is d.norm_key.
    # They store and compare the title key they are given, so resolving would change
    # nothing today; they are listed because they take the key without the dependency.
    "/api/games/{norm_key}/attribute":
        "api.setAttributeOverride(d.norm_key, ...) and ResolveModal's nk={base}",
    "/api/games/{norm_key}/attribute/{kind}":
        "api.clearAttributeOverride(d.norm_key, kind)",
    "/api/games/{nk}/merge":
        "api.mergeGame from FixDupModal, whose nk is ResolveModal's base = d.norm_key",
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
        # the decorated function starts at the next `def` (or `async def`)
        j = i + 1
        while j < len(lines) and not lines[j].startswith(("def ", "async def ")):
            j += 1
        if j >= len(lines):
            continue
        name = lines[j].split("def ", 1)[1].split("(")[0]
        k = j + 1
        while k < len(lines) and not (lines[k].startswith(
                ("def ", "async def ", "@app.", "class "))):
            k += 1
        out.append((m.group(2), name, "\n".join(lines[j:k])))
    return out


def signature(body):
    """The `def ...(...)` header of a route body, however many lines it wraps."""
    m = re.match(r"(?:async )?def \w+\((.*?)\)\s*(?:->[^:]*)?:\s*\n", body + "\n", re.S)
    return m.group(1) if m else body.split("\n", 1)[0]


def offenders_in(src):
    """(offenders, guarded count) for every key-taking route in `src`."""
    offenders = []
    guarded = 0
    for path, name, body in routes(src):
        params = re.findall(r"\{([a-z_]+)(?::[a-z]+)?\}", path)
        if not any(p in KEY_PARAMS for p in params):
            continue
        if path in EXEMPT:
            continue
        sig = signature(body)
        deps = [d for d in KEY_DEPS if re.search(r":\s*%s\b" % d, sig)]
        # A dependency reads the path parameter it is named for. Declared on a path
        # without that parameter, FastAPI quietly turns it into a QUERY parameter.
        wrong = [d for d in deps if KEY_DEPS[d] not in params]
        if wrong:
            offenders.append("%s (%s) declares %s, but the path has no {%s}"
                             % (path, name, wrong[0], KEY_DEPS[wrong[0]]))
            continue
        # 1. A HAND-ROLLED PARSE. Splitting the key here means this route will miss the
        #    next shape, exactly as every route missed the card shape.
        if re.search(r"\.split\(['\"]@['\"]\)|rsplit\(['\"]@['\"]", body):
            offenders.append("%s (%s) parses the key itself instead of resolving it"
                             % (path, name))
            continue
        # 2. RESOLVING BY HAND. The right function, called in the wrong place: every
        #    route that has to remember this line is a route that can forget it.
        if "_split_entry_key(" in body:
            offenders.append("%s (%s) resolves the key inline; declare it as a "
                             "BaseKey/EntryKey parameter instead" % (path, name))
            continue
        if deps or any(r + "(" in body for r in ROW_RESOLVERS):
            guarded += 1
            continue
        # 3. QUERYING BY norm_key WITH AN UNRESOLVED KEY. This is the shape that broke
        #    the media panel: the route asked the database for a game whose norm_key was
        #    "igdb:2155", got nothing, and returned an empty 200.
        if re.search(r"norm_key\s*=\s*\?|WHERE norm_key|norm_key\s+IN\s*\(", body):
            offenders.append("%s (%s) queries by norm_key without resolving the key"
                             % (path, name))
            continue
        # 4. Anything else that takes a key without resolving it. It may be harmless
        #    today, but the next line added to it will not know that.
        offenders.append("%s (%s) takes a game key without the key dependency"
                         % (path, name))
    return offenders, guarded


# Routes this lint MUST reject, so a regex that stopped matching cannot pass silently.
BAD = {
    "raw SQL on an unresolved key": (
        '@app.get("/api/games/{norm_key}/zzz")\n'
        'def zzz(norm_key: str):\n'
        '    return lib().execute("SELECT id FROM games WHERE norm_key=?", (norm_key,))\n'),
    "an inline resolve": (
        '@app.get("/api/games/{norm_key}/zzz")\n'
        'def zzz(norm_key: str):\n'
        '    norm_key = _split_entry_key(norm_key)[0]\n'
        '    return framing.get(DATA, norm_key)\n'),
    "a hand-rolled parse": (
        '@app.get("/api/games/{norm_key}/zzz")\n'
        'def zzz(norm_key: str):\n'
        '    return norm_key.rsplit("@", 1)[0]\n'),
    "a dependency bound to the wrong parameter": (
        '@app.get("/api/games/{nk}/zzz")\n'
        'def zzz(nk: BaseKey):\n'
        '    return nk\n'),
    "a key taken and passed on unresolved": (
        '@app.post("/api/games/{norm_key}/zzz")\n'
        'async def zzz(norm_key: str,\n'
        '              body: dict = Body(...)):\n'
        '    return framing.set(DATA, norm_key, body)\n'),
}
GOOD = (
    '@app.get("/api/games/{norm_key}/zzz")\n'
    'def zzz(norm_key: BaseKey, kind: str):\n'
    '    return lib().execute("SELECT id FROM games WHERE norm_key=?", (norm_key,))\n'
    '\n\n'
    '@app.get("/api/games/{nk}/yyy/{aid}")\n'
    'def yyy(nk: BaseNk,\n'
    '        aid: int):\n'
    '    return nk\n')


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "server", "app.py"), encoding="utf-8").read()
    rs = routes(src)
    check("the file parses into routes", len(rs) > 100, len(rs))

    # the lint itself: each bad shape is caught, and the resolved shape is not
    for label, bad in BAD.items():
        found, _ = offenders_in(bad)
        check("the lint catches " + label, len(found) == 1, found)
    found, good = offenders_in(GOOD)
    check("the lint passes a route that takes its key through the dependency",
          not found and good == 2, found)

    offenders, guarded = offenders_in(src)
    check("at least some routes are guarded", guarded >= 5, guarded)
    check("no route parses or uses a game key without the resolver",
          not offenders, "\n            ".join(offenders))

    # each key dependency must be what its name says: a FastAPI Depends on a helper
    # whose one parameter is the path parameter it claims, and that calls the resolver
    for alias, param in KEY_DEPS.items():
        m = re.search(r"^%s = Annotated\[\w+, Depends\((\w+)\)\]" % alias, src, re.M)
        check("%s is a FastAPI dependency" % alias, m, alias)
        fn = src[src.index("def %s(" % m.group(1)):]
        fn = fn[:fn.index("\ndef ", 5)]
        check("%s reads {%s}" % (alias, param),
              fn.startswith("def %s(%s: str)" % (m.group(1), param)), fn[:80])
        check("%s resolves through _split_entry_key" % alias, "_split_entry_key(" in fn)

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
