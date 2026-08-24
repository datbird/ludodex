#!/usr/bin/env python3
"""A tooltip that promises a provider will fill something is a promise about the
PIPELINE, not about the provider.

provider_caps.CAPS is checked, claim by claim, against each provider's real mapper —
which is why every row is true about what that provider RETURNS. It is not evidence that
anything asks. MobyGames, ArcadeDB and ZXInfo are credited with release_year, genres,
description and more, and each has a switch in Settings; repo-wide, their mappers are
imported only by config.py's help text, moby_mirror.py's catalogue walk, a status
endpoint in server/app.py, and the tests. A user who switched one on got a tooltip
promising data no step fetches — the same failure the file's own RULE warns about,
arriving from the other side: not an invented capability, a real one nobody asks for.

This test holds the wiring claim to the source, so a provider cannot be quietly marked
wired without a caller, and a caller cannot be removed without the promise being
withdrawn.
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


# provider -> the call a pipeline step would have to make to actually use it.
ENTRY_POINTS = {
    "mobygames": ("mobygames", ("extract_metadata",)),
    "arcadedb": ("arcadedb", ("query",)),
    "zxinfo": ("zxinfo", ("search", "game")),
}
# Files that may mention a provider without consulting it: help text, its own mirror
# walk, a status endpoint, and the provider's own module.
ALLOWED = ("config.py", "moby_mirror.py", "provider_caps.py", "provider_links.py",
           "matchindex.py", "app.py", "mobygames.py", "arcadedb.py", "zxinfo.py")


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_support
    test_support.isolate("ludodex-prov-wiring-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import provider_caps as PC

    print("1. THE MATRIX SAYS WHETHER ANYTHING WILL ACTUALLY ASK")
    for p in ("mobygames", "arcadedb", "zxinfo"):
        check("%-10s is credited with capabilities" % p,
              len([k for k, v in PC.CAPS.items() if p in v]) >= 4)
        check("%-10s is NOT claimed as wired into the pipeline" % p,
              not PC.wired(p))
    for p in ("igdb", "screenscraper"):
        check("%-10s is wired, and says so" % p, PC.wired(p))

    print()
    print("2. and the claim matches the source, not my memory of it")
    # If someone wires one in, this fails and the matrix has to be updated with it.
    for provider, (mod, funcs) in sorted(ENTRY_POINTS.items()):
        callers = []
        for d in ("ludodex", "server"):
            for fn in sorted(os.listdir(os.path.join(root, d))):
                if not fn.endswith(".py") or fn in ALLOWED:
                    continue
                src = open(os.path.join(root, d, fn), encoding="utf-8",
                           errors="replace").read()
                if not re.search(r"\bimport %s\b" % mod, src):
                    continue
                if any(re.search(r"\b%s\.%s\b" % (mod, f), src) for f in funcs):
                    callers.append(fn)
        check("%-10s really has no pipeline caller: %s"
              % (provider, callers or "none"),
              bool(callers) == PC.wired(provider))

    print()
    print("3. THE TOOLTIP SAYS IT, because the tooltip is what the user reads")
    tip = PC.tooltip("release_year")
    check("an unwired provider is marked in the sentence: %s"
          % tip[:70], "not wired in" in tip)
    check("a wired one is not", "IGDB (not wired in)" not in tip)

    print()
    print("4. a kind ONLY unwired providers can fill is not described as filled")
    # `device` is xbox + zxinfo, and `language` is rom + arcadedb + zxinfo: those have a
    # wired filler each. A kind where every listed provider is unwired must say so.
    m = PC.matrix(["release_year", "themes", "player_perspectives"])
    check("release_year has wired fillers", m["release_year"]["unfilled_today"] is False)
    check("and the per-provider flag is in the API answer",
          all("wired" in r for r in m["release_year"]["providers"]))
    check("themes is filled by IGDB and AI, so it is not unfilled",
          m["themes"]["unfilled_today"] is False)

    print()
    print("5. 'unsupplied' and 'nothing fills it today' stay different answers")
    m = PC.matrix(["completion_status"])
    check("a kind nobody can fill is still unsupplied",
          m["completion_status"]["unsupplied"] is True)
    check("and is trivially unfilled too",
          m["completion_status"]["unfilled_today"] is True)

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
