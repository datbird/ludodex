#!/usr/bin/env python3
"""The media table has ONE schema, not three that happen to agree.

Three modules opened the same file and each had its own opinion about what it contains:

  * `media_index.index_con()`   — claims in its own comments to be canonical, and creates
                                  the full set including filler / ai_pick / detail /
                                  frame / sil.
  * `media_fetch.con_index()`   — CREATE TABLE IF NOT EXISTS with none of those five.
  * `media_choose.con_index()`  — ALTERs a different subset in, and assumes the table
                                  already exists.

It worked by accident of ordering. Whichever ran first on a fresh install decided the
shape; open the index with `media_choose.con_index()` on a fresh data dir and it raised
`no such table: media` — the canonical creator was simply never the one that ran. Three
derivations of one fact is the defect this codebase keeps fixing everywhere else, and a
schema is the worst place to leave it, because the loser is silent: a column that never
got created is a ranking term that never fires.

Offline. No network.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-schema-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


PROBE = r'''
import os, sqlite3, sys
os.environ["LUDODEX_DATA"] = sys.argv[2]
sys.path.insert(0, sys.argv[3])
sys.path.insert(0, os.path.join(sys.argv[3], "ludodex"))
mod = __import__(sys.argv[1])
opener = getattr(mod, "index_con", None) or getattr(mod, "con_index")
con = opener()
cols = sorted(r[1] for r in con.execute("PRAGMA table_info(media)"))
idx = sorted(r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='media' "
    "AND name NOT LIKE 'sqlite_%'"))
con.close()
print("\t".join(cols))
print("\t".join(idx))
'''


def opened_by(module):
    """Columns + indexes a FRESH data dir ends up with when only `module` opens it.

    A subprocess with its own empty data dir, because the whole point is what happens
    when this opener is the FIRST one to touch the file — importing all three into one
    process would let the canonical creator hide the others' gaps."""
    d = tempfile.mkdtemp(prefix="ludodex-schema-%s-" % module, dir=DATA)
    p = os.path.join(DATA, "probe.py")
    with open(p, "w") as fh:
        fh.write(PROBE)
    out = subprocess.run([sys.executable, p, module, d, DIR],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None, (out.stderr or "").strip().splitlines()[-1:]
    lines = out.stdout.strip().split("\n")
    return (lines[0].split("\t"), lines[1].split("\t") if len(lines) > 1 else [])


def main():
    print("1. every opener creates the SAME table on a fresh data dir")
    ref_cols, ref_idx = opened_by("media_index")
    check("media_index opens a fresh index", ref_cols is not None)
    for mod in ("media_fetch", "media_choose"):
        cols, idx = opened_by(mod)
        check("%s opens a fresh index at all" % mod, cols is not None)
        missing = sorted(set(ref_cols) - set(cols or []))
        check("%s creates every column (missing: %s)" % (mod, missing), not missing)
        extra = sorted(set(cols or []) - set(ref_cols))
        check("%s invents none of its own (extra: %s)" % (mod, extra), not extra)
        check("%s creates the same indexes" % mod, set(idx or []) == set(ref_idx))

    print("2. the columns the ranker and the pruner depend on are all there")
    for c in ("sha1", "width", "height", "filler", "ai_pick", "detail", "frame",
              "sil", "hidden", "game_key", "meta", "probed"):
        check("%s exists" % c, c in ref_cols)

    print("3. and it is defined in exactly one place")
    src = {m: open(os.path.join(DIR, "ludodex", m + ".py"), encoding="utf-8").read()
           for m in ("media_index", "media_fetch", "media_choose")}
    check("media_index is the one that CREATEs it",
          "CREATE TABLE IF NOT EXISTS media(" in src["media_index"])
    for m in ("media_fetch", "media_choose"):
        check("%s does not create a second one" % m,
              "CREATE TABLE IF NOT EXISTS media(" not in src[m])

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
