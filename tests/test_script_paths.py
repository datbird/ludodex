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
    # EVERY .py LITERAL, not the ones a pattern happened to know about. The first
    # version of this test matched `_run_script("x.py")` and PKG joins, and passed
    # while _run_streaming — a SECOND runner, joining against the repo root — still
    # produced "can't open file '/app/build_library.py'". A test that only looks where
    # the last bug was found cannot catch the next one.
    named = set(re.findall(r'"([a-z_][a-z0-9_]*\.py)"', app))
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
          "def _script_path(" in app)
    # EVERY runner, found by shape rather than by name, so a third one cannot appear
    # and quietly resolve paths its own way.
    runners = re.findall(r'argv = \[sys\.executable, ([^\]]+)\]', app)
    check("all %d runners resolve the same way: %s" % (len(runners), runners),
          runners and all("_script_path(" in r for r in runners))

    print()
    print("2b. EVERY spawn resolves against the package, whatever its shape")
    # Not just the two runners: eight direct subprocess.run calls build their own argv.
    spawns = [ln.strip() for ln in app.splitlines() if "sys.executable" in ln]
    bad = [ln for ln in spawns
           if "_script_path(" not in ln and "os.path.join(PKG," not in ln]
    check("all %d spawns in server/app.py (stray: %d)" % (len(spawns), len(bad)),
          spawns and not bad)

    # devices.py runs scripts too, from its OWN DIR — which is the package, not the
    # repo root. Different constant, same requirement: the files must be there.
    dev = open(os.path.join(pkg, "devices.py")).read()
    check("devices.DIR is the package it lives in",
          'DIR = os.path.dirname(os.path.abspath(__file__))' in dev)
    dev_named = set(re.findall(r'os\.path\.join\(DIR,\s*"([a-z_]+\.py)"', dev))
    dev_missing = sorted(n for n in dev_named
                         if not os.path.exists(os.path.join(pkg, n)))
    check("the %d scripts devices.py runs all exist (missing: %s)"
          % (len(dev_named), dev_missing or "none"), not dev_missing)

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
