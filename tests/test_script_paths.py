#!/usr/bin/env python3
"""Every pipeline script the server names must actually be there.

THIS IS THE TEST THAT WAS MISSING WHEN THE PACKAGE WAS RESTRUCTURED. The scripts moved
into ludodex/ and the callers did not follow. `_run_script("steam_owned.py")` ran
`python3 steam_owned.py` against a working directory of /app, so it became

    /usr/local/bin/python3.12: can't open file '/app/steam_owned.py': [Errno 2]

and the same held for media_index, media_choose, build_library, scores_fetch,
media_fetch and every *_owned fetcher. Store syncs, art, scores and the catalog rebuild
were all unreachable from the server at once.

Nothing caught it because a missing script is a RUNTIME failure in a subprocess, on a
path only exercised by a real sync. Grepping the source for the names and checking the
files exist costs nothing and fails the moment a file moves again.
"""
import os
import re
import sys

PASS = []


def check(label, cond):
    PASS.append(bool(cond))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = open(os.path.join(root, "server", "app.py")).read()
    pkg = os.path.join(root, "ludodex")

    print()
    print("1. every script named in the server exists in the package")
    named = set(re.findall(r'_run_script\(\s*"([a-z_]+\.py)"', app))
    named |= set(re.findall(r'os\.path\.join\(PKG,\s*"([a-z_]+\.py)"', app))
    named |= {m for m in re.findall(r'SYNC_SPECS\s*=\s*\{(.*?)\}', app, re.S)
              for m in re.findall(r'"([a-z_]+\.py)"', m)}
    named |= {m for m in re.findall(r'WISHLIST_SPECS\s*=\s*\{(.*?)\}', app, re.S)
              for m in re.findall(r'"([a-z_]+\.py)"', m)}
    check("the server names some scripts to run: %d" % len(named), len(named) >= 10)
    missing = sorted(n for n in named if not os.path.exists(os.path.join(pkg, n)))
    check("all %d resolve inside ludodex/ (missing: %s)" % (len(named), missing or "none"),
          not missing)

    print()
    print("2. nothing resolves a script against the repo root any more")
    # DIR is the repo root and holds no scripts. A join against it is the exact shape
    # that produced '/app/steam_owned.py'.
    stray = re.findall(r'os\.path\.join\(DIR,\s*"([a-z_]+\.py)"', app)
    check("no os.path.join(DIR, '*.py') remains: %s" % (stray or "none"), not stray)
    check("_run_script resolves through one helper",
          "_script_path(script)" in app and "def _script_path(" in app)

    print()
    print("3. the helper prefers the package and never invents a path")
    sys.path.insert(0, pkg)
    import test_support
    test_support.isolate("ludodex-scriptpath-")
    sys.path.insert(0, root)
    from server import app as srv
    check("a package script resolves into ludodex/",
          srv._script_path("build_library.py") == os.path.join(srv.PKG,
                                                               "build_library.py"))
    check("an absolute path is left alone",
          srv._script_path("/x/y.py") == "/x/y.py")
    # A name that is in neither place must still come back as a path, so the caller
    # reports "no such file" rather than this raising somewhere further away.
    check("an unknown name still returns a path",
          srv._script_path("nope.py").endswith("nope.py"))

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
