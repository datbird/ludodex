#!/usr/bin/env python3
"""The detail panel's `base` must be a norm_key, whatever key opened the panel.

THIS BUG HAS NOW HAPPENED TWICE. In 2026-07-15 the single-game magic wand was passed an
entry_key ("doom@gba"); `aimeta.game_context` resolves by BARE norm_key, found no row,
and the scan reported 0 findings with no error. The wand looked like it did nothing, on
every game.

The card collapse reintroduced it in a new shape. The grid now opens a game by its CARD
key ("igdb:2155"), and the panel derived `base` by splitting on "@". A card key has no
"@", so `base` became "igdb:2155" and the wand, the resolve modal and the hero
preference all keyed off a string that matches no game.

The fix is to take `base` from the DETAIL the server returned, which is the only place
that actually knows. This pins that, because the next new key shape will do it again.
"""
import os
import re
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = open(os.path.join(root, "web", "src", "App.tsx"), encoding="utf-8").read()

    # the server always returns the real title key, so the client must prefer it
    check("the panel derives base from the server's norm_key",
          re.search(r"const base = [^\n]*d\?\.norm_key", app) is not None)

    # and the parse-the-key fallback must not be the primary source any more.
    # `const base =` appears several times in this file; take the one in the detail panel.
    decl = [l for l in app.split("\n")
            if l.strip().startswith("const base =") and "norm_key" in l]
    check("exactly one base declaration reads norm_key", len(decl) == 1)
    line = decl[0]
    check("a raw '@' split is at most the fallback",
          "@" not in line or line.index("d?.norm_key") < line.index("@"))

    # the three consumers that a wrong `base` silently breaks
    check("the wand still targets base", "norm_keys: [base]" in app)
    check("the resolve modal still takes base", "ResolveModal nk={base}" in app)
    check("the hero preference still writes base", "api.setHeroPref(base," in app)

    # `base` must be declared AFTER `d`, or it cannot read it
    i_d = app.index("const [d, setD] = useState<GameDetail | null>(null)")
    i_b = app.index(line.strip())          # the detail panel's declaration, not another
    check("base is declared after the detail state", i_b > i_d)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
