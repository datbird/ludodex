#!/usr/bin/env python3
"""Verify the scoped reset (library / curation / factory).

The point of a reset is what it does NOT touch, so most checks here assert
survival, not deletion. Runs against a throwaway LUDODEX_DATA; no live instance.

Usage: python3 verify_reset.py
"""
import os
import sys
import tempfile

scratch = tempfile.mkdtemp(prefix="ludodex-reset-")
os.environ["LUDODEX_DATA"] = scratch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reset                                               # noqa: E402

FAIL = []


def check(c, l):
    print(("  ok   " if c else "  FAIL ") + l)
    if not c:
        FAIL.append(l)


files = (reset.IMPORT_DBS + reset.CURATION_DBS + reset.CONFIG_DBS
         + ["auth.sqlite", "backups.sqlite", "ai-usage.sqlite",
            "steam_games.tsv", "gog_games.tsv", "roms-index-mgr3.sqlite"])
for f in files:
    open(os.path.join(scratch, f), "w").write("x" * 100)
open(os.path.join(scratch, "game-library.sqlite-wal"), "w").write("w")
repo = os.path.join(scratch, "media")
os.makedirs(repo, exist_ok=True)
for i in range(5):                              # content-addressed blobs
    open(os.path.join(repo, "%040x.jpg" % i), "w").write("img")
os.makedirs(os.path.join(repo, ".thumbs"), exist_ok=True)
open(os.path.join(repo, ".thumbs", "t.jpg"), "w").write("thumb")
# A media BACKUP living inside the repo. A real install had a 9.4 GB one of these,
# and an earlier rmtree(repo) would have destroyed it during a "library" reset.
os.makedirs(os.path.join(repo, ".backup-2026-07-21"), exist_ok=True)
for i in range(3):
    open(os.path.join(repo, ".backup-2026-07-21", "b%d.png" % i), "w").write("backup")
for d in reset.TOKEN_DIRS:
    os.makedirs(os.path.join(scratch, d), exist_ok=True)


def state():
    return set(os.listdir(scratch))


print("scope=library — the 'try that import again' button")
p = reset.plan("library")
check("config.sqlite" not in p["databases"], "credentials NOT in the plan")
check("tags.sqlite" not in p["databases"], "hand-curation NOT in the plan")
check("game-library.sqlite" in p["databases"], "the catalog IS")
check(p["media_files"] == 6, "blobs + thumbs counted, backup NOT (%d)" % p["media_files"])
check(p["media_preserved"] == [".backup-2026-07-21"],
      "the in-repo media backup is reported as PRESERVED (got %s)" % p["media_preserved"])
check(sorted(p["tsvs"]) == ["gog_games.tsv", "steam_games.tsv"], "TSVs counted")
check(p["rom_indexes"] == ["roms-index-mgr3.sqlite"], "ROM index counted")
check(not p["token_dirs"], "store logins untouched at this scope")
r = reset.run("library")
s = state()
check(r["ok"], "run ok")
check("config.sqlite" in s and "auth.sqlite" in s, "credentials + login SURVIVE")
check("tags.sqlite" in s and "pins.sqlite" in s, "curation SURVIVES")
check("game-library.sqlite" not in s, "catalog gone")
check("game-library.sqlite-wal" not in s, "its stale WAL gone too (would replay otherwise)")
check("steam_games.tsv" not in s, "TSVs gone — the silent-repopulate trap")
check("roms-index-mgr3.sqlite" not in s, "ROM index gone")
left = sorted(os.listdir(repo))
check(left == [".backup-2026-07-21", ".thumbs"], "only the backup + empty thumbs remain (%s)" % left)
check(not os.listdir(os.path.join(repo, ".thumbs")), "regenerable thumbs cleared")
check(len(os.listdir(os.path.join(repo, ".backup-2026-07-21"))) == 3,
      "THE MEDIA BACKUP IS UNTOUCHED — a library reset must never eat a backup")
check("backups.sqlite" in s and "ai-usage.sqlite" in s, "way-back + spend history kept")

print("scope=curation")
p = reset.plan("curation")
check("tags.sqlite" in p["databases"], "curation now included")
check("config.sqlite" not in p["databases"], "credentials still not")
reset.run("curation")
s = state()
check("tags.sqlite" not in s and "merges.sqlite" not in s, "curation gone")
check("config.sqlite" in s, "credentials STILL survive")

print("scope=factory")
p = reset.plan("factory")
check("config.sqlite" in p["databases"], "credentials included")
check(sorted(p["token_dirs"]) == sorted(reset.TOKEN_DIRS), "store logins included")
reset.run("factory")
s = state()
check("config.sqlite" not in s, "credentials gone")
check("auth.sqlite" in s, "your ACCOUNT survives — factory cannot lock you out")
check("backups.sqlite" in s, "archives list survives, so it stays recoverable")
check(not any(os.path.isdir(os.path.join(scratch, d)) for d in reset.TOKEN_DIRS),
      "store token dirs gone")

print("guards")
try:
    reset.plan("everything")
    check(False, "unknown scope raises")
except ValueError:
    check(True, "unknown scope raises")
asrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "server", "app.py")).read()
ep = asrc[asrc.index("def ops_reset("):asrc.index("@app.get(\"/api/ops/backups\")")]
check("safety = ops_backup()" in ep, "a safety snapshot is taken BEFORE any deletion")
check('!= scope' in ep and "type %r to confirm" in ep,
      "curation/factory need the scope typed; library stays one click")
check("ops_restart()" in ep, "re-execs so deleted DBs are reopened/recreated")

print()
if FAIL:
    print("FAILED (%d): %s" % (len(FAIL), "; ".join(FAIL)))
    sys.exit(1)
print("ALL CHECKS PASSED")
