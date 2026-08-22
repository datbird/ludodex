#!/usr/bin/env python3
"""Every store source is loaded. One list, not a copy per loop.

`build_library.py` defined `_STORE_SRCS` and then hardcoded the same seven names again
in two separate loops: the `_REGEN` carry-over set and the `load_tsv` pass. Adding
Nintendo to `_STORE_SRCS` was therefore invisible. The sync ran, `nintendo_owned.py`
wrote 179 rows to `nintendo_games.tsv`, and the catalog rebuilt with no Nintendo source
at all, because nothing iterated a list containing it.

Silent by construction: a missing source is not an error, it is an absence, and the
library simply came back looking exactly as it had before.

This is the same shape the 2026-08-21 audit found across the codebase, one rule written
in several places. Here the single home already existed and the callers ignored it, which
is the cheapest version of the bug to fix and the easiest to reintroduce.

The test reads the source rather than running a build, because a build needs the full
data directory. It pins the property that matters: no loop over store sources may spell
the list out for itself.
"""
import ast
import os
import re
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))
import test_support                              # noqa: E402
test_support.isolate("ludodex-storesrc-")

SRC = open(os.path.join(DIR, "ludodex", "build_library.py"), encoding="utf-8").read()


def check(label, cond):
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def store_srcs():
    """The declared list, read straight out of the module's own assignment."""
    for node in ast.parse(SRC).body:
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", "") == "_STORE_SRCS"):
            return list(ast.literal_eval(node.value))
    return []


def main():
    srcs = store_srcs()
    check("_STORE_SRCS is declared", bool(srcs))
    for s in ("steam", "gog", "epic", "itch", "ea", "psn", "xbox", "nintendo"):
        check("_STORE_SRCS carries %r" % s, s in srcs)

    # The defect: a for-loop over OWNED store TSVs whose iterable is a literal tuple of
    # store names. That is a second copy of the list, whatever the variable is called.
    #
    # NOT every inline list of store names is a copy. The WISHLIST loop iterates
    # ("steam", "gog") because only those two expose one, which is `WISHLIST_SPECS` in
    # server/app.py and a genuinely different set. Scoping the check to loops that touch
    # `_games.tsv` separates the two without weakening it: a real copy of the owned-store
    # list cannot avoid reading those files.
    names = set(srcs)
    copies = []
    for node in ast.walk(ast.parse(SRC)):
        if not isinstance(node, ast.For) or not isinstance(node.iter, (ast.Tuple, ast.List)):
            continue
        try:
            vals = [v for v in ast.literal_eval(node.iter) if isinstance(v, str)]
        except (ValueError, TypeError):
            continue
        if len([v for v in vals if v in names]) < 2:
            continue
        body = "\n".join(ast.unparse(b) for b in node.body)
        if "_games.tsv" in body:
            copies.append((node.lineno, vals))
    for ln, vals in copies:
        print("      line %d iterates %r" % (ln, vals))
    check("no loop spells the store list out for itself", not copies)

    # And the two that used to: they must now read the one list.
    check("the carry-over loop uses _STORE_SRCS",
          re.search(r"^for _s in _STORE_SRCS:", SRC, re.M) is not None)
    check("the load_tsv loop uses _STORE_SRCS",
          re.search(r"^for _src in _STORE_SRCS:", SRC, re.M) is not None)
    check("load_tsv is still what that loop calls",
          re.search(r"for _src in _STORE_SRCS:(?:.|\n){0,120}?load_tsv\(", SRC) is not None)

    print("test_store_sources_one_list: all checks passed")


if __name__ == "__main__":
    main()
