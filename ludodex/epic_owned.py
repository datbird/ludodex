#!/usr/bin/env python3
"""Dump Epic-owned games to epic_games.tsv using legendary's cached login
(auto-refreshes; no interactive auth after the first time)."""
import json
import os
import shutil
import subprocess
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config                                             # noqa: E402

# DIR is this package; the default output dir is the REPO ROOT above it — the same
# resolution every sibling store script uses, and the only place build_library looks.
# Deriving it from DIR wrote epic_games.tsv into ludodex/ whenever LUDODEX_DATA was
# unset, where nothing reads it: an Epic library that pulled fine and never appeared.
OWN = os.environ.get("LUDODEX_DATA") or os.path.dirname(DIR)
leg = shutil.which("legendary") or os.path.expanduser("~/.local/bin/legendary")
try:
    out = subprocess.run([leg, "list", "--json"], capture_output=True, text=True,
                         timeout=120)
except (OSError, subprocess.TimeoutExpired) as e:
    sys.exit("legendary failed: %s" % e)
if out.returncode != 0:
    sys.exit("legendary list failed: " + (out.stderr or "")[-300:])
data = json.loads(out.stdout)
rows = sorted(((g.get("app_name", ""), g.get("app_title") or g.get("title") or "")
               for g in data), key=lambda x: x[1].lower())
with open(OWN + "/epic_games.tsv", "w", encoding="utf-8") as f:
    for a, t in rows:
        if t:
            f.write(config.tsv_row(a, t) + "\n")
print("# epic games: %d" % len(rows), file=sys.stderr)
