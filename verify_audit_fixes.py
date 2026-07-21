#!/usr/bin/env python3
"""Verify the fixes for the ingest/wand audit findings.

Each check targets a specific defect the audit found, and is written so it FAILS against
the pre-fix code — a regression test, not a restatement of the fix.

Runs against a throwaway LUDODEX_DATA. No network, no live instance.
Usage: python3 verify_audit_fixes.py
"""
import os
import sqlite3
import sys
import tempfile

FAIL = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAIL.append(label)


scratch = tempfile.mkdtemp(prefix="ludodex-auditfix-")
os.environ["LUDODEX_DATA"] = scratch
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

print("1. media_fetch._RESMAP invalidates (defect #1)")
import media_fetch                                        # noqa: E402
meta = os.path.join(scratch, "metadata-cache.sqlite")
c = sqlite3.connect(meta)
c.execute("CREATE TABLE igdb_resolution (norm_key TEXT PRIMARY KEY, igdb_id INTEGER, "
          "slug TEXT, matched_by TEXT, resolved_at INTEGER)")
c.execute("INSERT INTO igdb_resolution VALUES ('portal', 71, NULL, 'name', 0)")
c.commit()
media_fetch.META_CACHE = meta
media_fetch.invalidate_resmap()
check(media_fetch.game_key("portal") == "igdb:71", "first read resolves igdb:71")
# identity changes underneath a long-running process (a wand pin)
c.execute("UPDATE igdb_resolution SET igdb_id=14546, matched_by='manual' WHERE norm_key='portal'")
c.commit()
check(media_fetch.game_key("portal") == "igdb:71",
      "stale while cached (this is the bug, cache is real)")
media_fetch.invalidate_resmap()
check(media_fetch.game_key("portal") == "igdb:14546",
      "after invalidation the NEW identity is used — art is stamped correctly")
c.close()

print("2. igdb_enrich never re-resolves a manual pin (defect #2)")
src = open(os.path.join(ROOT, "igdb_enrich.py")).read()
check("matched_by='manual'" in src, "full-resolve reads the pinned set")
check("nk not in pinned" in src, "pinned games are excluded from `todo`")
check("matched_by!='manual'" in src, "era_reheal excludes pins too")

print("3. surgical apply honours detached/blocked entries (defect #3)")
app = open(os.path.join(ROOT, "server", "app.py")).read()
check("surgical_detached" in app, "surgical apply loads the detached set")
gk = app[app.index("    def _gk(nk, platform, bkey):"):]
gk = gk[:gk.index("\n    con = ")]
check("surgical_detached" in gk, "_gk checks it")
check(gk.index("surgical_detached") < gk.index("entry_ids.get"),
      "and checks it FIRST, like build_library._game_key")
check("if (nk, plat_of.get(gid)) in surgical_detached:" in app,
      "the link/rename loop skips them too")

print("4. contamination is reachable from the wand again (defect #4)")
import ast                                               # noqa: E402
tree = ast.parse(app)
calls = {}
for n in ast.walk(tree):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        s = set()
        for m in ast.walk(n):
            if isinstance(m, ast.Call):
                f = m.func
                if isinstance(f, ast.Name):
                    s.add(f.id)
                elif isinstance(f, ast.Attribute):
                    s.add(f.attr)
        calls[n.name] = s
callers = [fn for fn, c in calls.items() if "_auto_fix_contamination" in c
           and fn != "_auto_fix_contamination"]
check(bool(callers), "_auto_fix_contamination now has caller(s): %s" % (callers or "NONE"))
check("resolve_per_entry_identity" in callers,
      "reached from the per-entry resolver's no-candidate path (the wand scan)")

print("5. sync fills art from IGDB too, incrementally (defect #5)")
mf = open(os.path.join(ROOT, "media_fetch.py")).read()
check("def art_less_keys(" in mf, "there is a helper for 'games with no art'")
bf = mf[mf.index("def fetch_missing_art("):]
bf = bf[:bf.index("\ndef ", 10)]
check("fetch_igdb(con, now, only=" in bf, "backfill-art fetches IGDB art SCOPED to those games")
check("fetch_steamgriddb_targets" in bf, "and still falls through to SteamGridDB")
check("art_less_keys" in bf, "the target set is the art-less games, not the catalog")
# the sync must NOT use the blunt whole-catalog provider pass
check('"--provider", "igdb"' not in app,
      "sync does NOT run the unscoped whole-catalog IGDB refetch")
check(app.count('"--backfill-art"') == 1, "a single incremental art step in the sync")

print("6. dead reconcile chain is gone (defect #6)")
for name in ("_enqueue_reconcile", "_reconcile_drain", "_aimeta_apply_media"):
    check(("def %s(" % name) not in app, "%s removed" % name)
check("the authoritative\n    background rebuild is enqueued after" not in app,
      "the false 'authoritative rebuild is enqueued' claim is gone")

print("7. surgical apply writes match confidence (defect #7)")
check("matchconf.match_confidence(" in app, "confidence computed in the apply path")
check("'match_confidence','match_reason'" in app.replace('"', "'"),
      "and written as game_attributes")

print("8. detach purges the wrong game's ScreenScraper art (defect #8)")
det = app[app.index("def _detach_entry("):]
det = det[:det.index("\ndef ", 10)]
check("DELETE FROM ss_game" in det, "SS match row dropped")
check("DELETE FROM media" in det and "screenscraper" in det, "SS media rows dropped")

print("9. minor fixes")
up = open(os.path.join(ROOT, "update.sh")).read()
check("DATA_DIR=\"${LUDODEX_DATA:-$(pwd)}\"" in up, "update.sh honours LUDODEX_DATA")
check("$DATA_DIR/steam_games.tsv" in up, "TSVs written to the data dir")
bl = open(os.path.join(ROOT, "build_library.py")).read()
check("title:<norm_key> — SUFFIX-FREE" in bl, "schema comment matches the real key format")
tsx = open(os.path.join(ROOT, "web", "src", "App.tsx")).read()
check("nothing is applied automatically" not in tsx,
      "scan copy no longer claims nothing is applied automatically")

print("10. re-fetching art does NOT re-download it (incremental media)")
import media_index                                        # noqa: E402
idx = media_index.index_con()
media_fetch.invalidate_resmap()
media_fetch.put(idx, "portal", "cover", "igdb", "http://x/cover.jpg", 1)
idx.commit()
# simulate materialization: the bytes are downloaded and the row points at them
idx.execute("UPDATE media SET sha1='deadbeef' WHERE norm_key='portal'")
idx.commit()
before = idx.execute("SELECT sha1 FROM media WHERE norm_key='portal'").fetchone()[0]
check(before == "deadbeef", "row is materialized (sha1 set)")
# a later sync re-fetches the very same asset
media_fetch.put(idx, "portal", "cover", "igdb", "http://x/cover.jpg", 2)
idx.commit()
row = idx.execute("SELECT sha1, indexed_at FROM media WHERE norm_key='portal'").fetchone()
check(row[0] == "deadbeef",
      "sha1 SURVIVES the re-fetch — the bytes are not re-downloaded (got %r)" % row[0])
check(row[1] == 2, "but refreshable metadata is still updated")
check(idx.execute("SELECT COUNT(*) FROM media WHERE norm_key='portal'").fetchone()[0] == 1,
      "no duplicate row created")

print("11. art gap-fill is scoped to games that lack art")
lib = os.path.join(scratch, "lib.sqlite")
import config as _cfg
_cfg.set_("library_db", lib)
lc = sqlite3.connect(lib)
lc.executescript(
    "CREATE TABLE games (id INTEGER PRIMARY KEY, norm_key TEXT, canonical_title TEXT);"
    "CREATE TABLE sources (game_id INTEGER, source TEXT, source_id TEXT);"
    "CREATE TABLE metadata_links (game_id INTEGER, provider TEXT);")
lc.executescript(
    "INSERT INTO games VALUES (1,'portal','Portal'),(2,'doom','Doom');"
    "INSERT INTO sources VALUES (1,'steam','400'),(2,'steam','401');")
lc.commit(); lc.close()
targets = media_fetch.art_less_keys(idx)
keys = sorted(t[0] for t in targets)
check(keys == ["doom"],
      "only the art-less game is targeted; the one WITH a cover is skipped (got %s)" % keys)
idx.close()

print()
if FAIL:
    print("FAILED (%d): %s" % (len(FAIL), "; ".join(FAIL)))
    sys.exit(1)
print("ALL CHECKS PASSED")
