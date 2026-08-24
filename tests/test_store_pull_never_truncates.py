#!/usr/bin/env python3
"""A store pull that did not succeed must leave the previous ownership list alone.

The ownership TSVs are durable input to the catalog: `build_library` loads whatever is
in them, so an empty `steam_games.tsv` means "you own no Steam games", not "the pull
failed". `_run_script` opened the destination for writing BEFORE starting the child, so
the file was truncated the moment a sync began and nothing put it back on failure. The
sync worker marks that one store `failed` and keeps going; `any_ok` from the other
stores then triggers the rebuild, and the failed store's entire library disappears from
the catalog until a later pull happens to succeed.

Two shapes of "did not succeed" matter, because the second one looks like success:

  * A NON-ZERO EXIT. Expired session, network blip, a crash mid-page.
  * A CLEAN EXIT WITH NOTHING TO SAY. `ea_owned.py` printed "not logged in" to stderr
    and returned, which is exit 0 with empty stdout — indistinguishable from "this
    account owns zero games" to anything reading the file afterwards.

An empty result is only refused when it would REPLACE a list that has rows. Clearing a
wishlist has to stay possible, and a first-ever pull has nothing to lose.

Writing beside the target and renaming on success covers every fetcher at once. Refusing
to replace a non-empty list with an empty one covers the second shape without trusting
each store script to get its exit code right, and ea_owned's exit code is fixed too.

Offline. No network.
"""
import os
import subprocess
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-tsv-guard-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402

PASS = []
TSV = "fake_games.tsv"
PREV = "1\tSonic Mania\n2\tCeleste\n"


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def seed():
    with open(os.path.join(DATA, TSV), "w", encoding="utf-8") as f:
        f.write(PREV)


def content():
    p = os.path.join(DATA, TSV)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else None


def script(body):
    p = os.path.join(DATA, "fetch_stub.py")
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    return p


def leftovers():
    return [f for f in os.listdir(DATA) if f.startswith(TSV) and f != TSV]


def main():
    print("a failed store pull never truncates the ownership list")
    app.DATA = DATA

    # ---- a fetcher that dies partway through --------------------------------- #
    seed()
    s = script("import sys\nprint('9\\tHalf a game')\nsys.exit(1)\n")
    ok, err = app._run_script(s, TSV, capture=True, timeout=60)
    check("a non-zero exit is reported as a failure", ok is False)
    check("the previous ownership list is untouched", content() == PREV)
    check("no partial file is left behind", leftovers() == [])

    # ---- a fetcher that exits 0 with nothing to say --------------------------- #
    seed()
    s = script("import sys\nprint('not logged in', file=sys.stderr)\n")
    ok, err = app._run_script(s, TSV, capture=True, timeout=60)
    check("an empty result over a non-empty list is a failure, not a wipe", ok is False)
    check("and it says so", "empty" in (err or "").lower())
    check("the previous ownership list survives that too", content() == PREV)
    check("still no partial file", leftovers() == [])

    # ---- a fetcher that works ------------------------------------------------- #
    seed()
    s = script("print('1\\tSonic Mania')\nprint('2\\tCeleste')\nprint('3\\tHades')\n")
    ok, err = app._run_script(s, TSV, capture=True, timeout=60)
    check("a good pull succeeds", ok is True)
    check("and replaces the list", content() == "1\tSonic Mania\n2\tCeleste\n3\tHades\n")
    check("leaving no partial file", leftovers() == [])

    # ---- an empty result with nothing to lose is legitimate ------------------- #
    # An empty wishlist is a real answer. The rule is about REPLACING a list that has
    # rows, not about refusing every empty one, or clearing a wishlist could never
    # take effect.
    os.remove(os.path.join(DATA, TSV))
    s = script("import sys\nprint('nothing wanted', file=sys.stderr)\n")
    ok, err = app._run_script(s, TSV, capture=True, timeout=60)
    check("an empty result is accepted when there is no list to lose", ok is True)
    check("and writes the empty list", content() == "")

    # ---- the store script that started this ----------------------------------- #
    env = dict(os.environ, LUDODEX_DATA=DATA)
    p = subprocess.run([sys.executable, os.path.join(DIR, "ludodex", "ea_owned.py")],
                       cwd=DIR, env=env, capture_output=True, text=True, timeout=120)
    check("ea_owned exits non-zero when it cannot log in", p.returncode != 0)
    check("and prints no games", p.stdout.strip() == "")

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
