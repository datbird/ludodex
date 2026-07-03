#!/usr/bin/env python3
"""Dump Epic-owned games to epic_games.tsv using legendary's cached login
(auto-refreshes; no interactive auth after the first time)."""
import json
import os
import shutil
import subprocess
import sys

OWN = os.path.dirname(os.path.abspath(__file__))
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
            f.write("%s\t%s\n" % (a, t))
print("# epic games: %d" % len(rows), file=sys.stderr)
