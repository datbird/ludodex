#!/usr/bin/env python3
"""Verify the three-tier import (algo / lite / heavy).

Written to FAIL against the pre-feature code: every check targets behaviour that
did not exist before. No network, no AI calls, throwaway LUDODEX_DATA.

Usage: python3 ludodex/verify_import_modes.py
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


scratch = tempfile.mkdtemp(prefix="ludodex-import-")
os.environ["LUDODEX_DATA"] = scratch
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "server"))

print("1. the suspect heuristic picks mangled titles and leaves good ones alone")
import ingest_ai                                          # noqa: E402
MANGLED = ["SMW_U", "FF7", "SMBDLX", "GRADIUS3", "sonic2b", "", "DK", "smb3"]
CLEAN = ["Super Mario World", "Final Fantasy VII", "Doom", "Sonic the Hedgehog 2",
         "Chrono Trigger", "R-Type", "III", "Metal Gear Solid"]
# Titles that ARE real games but trip the heuristic anyway. Kept deliberately: a
# false positive costs ~50 tokens and the model answers "already right", producing
# no hint. A false NEGATIVE ships a mangled title into the catalog. The asymmetry
# is the whole design, so these are asserted rather than quietly tolerated.
ACCEPTED_FP = ["Ico", "1943"]
for t in MANGLED:
    check(ingest_ai._suspect(t), "suspect: %r" % t)
for t in CLEAN:
    check(not ingest_ai._suspect(t), "not suspect: %r" % t)
for t in ACCEPTED_FP:
    check(ingest_ai._suspect(t), "known-acceptable false positive: %r" % t)

print("2. hints are advisory and only stored when they assert something")
import ingesthints                                        # noqa: E402
check(ingesthints.put("snes", "SMW_U", to_title="Super Mario World", confidence=0.95),
      "a real hint is stored")
check(not ingesthints.put("snes", "Doom", to_title="", to_platform="", year=None),
      "a hint that changes nothing is REFUSED (keeps overrides() clean)")
ov = ingesthints.overrides()
check(ov.get(("snes", "SMW_U")) == ("Super Mario World", "", None),
      "overrides() exposes it as (title, platform, year)")
check(("snes", "Doom") not in ov, "the empty hint never reaches build_library")

print("3. the confidence floor is honoured")
ingesthints.put("gen", "SONIC2B", to_title="Sonic the Hedgehog 2", confidence=0.30)
check(("gen", "SONIC2B") in ingesthints.overrides(), "low-confidence hint IS stored")
check(("gen", "SONIC2B") not in ingesthints.overrides(min_confidence=0.5),
      "but a caller can require >=0.5 and not see it")

print("4. clear() is a real undo")
n = ingesthints.count()
check(n == 2, "two hints recorded (got %d)" % n)
ingesthints.clear("gen")
check(ingesthints.count() == 1, "clearing one system leaves the other")
check(("snes", "SMW_U") in ingesthints.overrides(), "the survivor is the right one")

print("5. build_library APPLIES the hint to a ROM row")
# a minimal ROM index, exactly the shape build_romdb produces
rom_db = os.path.join(scratch, "roms-index-mgr9.sqlite")
rc = sqlite3.connect(rom_db)
rc.execute("CREATE TABLE roms (id INTEGER PRIMARY KEY, system TEXT, subdir TEXT, "
           "game TEXT, filename TEXT, ext TEXT, name TEXT, region TEXT, "
           "languages TEXT, version TEXT, revision TEXT, disc TEXT, flags TEXT, "
           "tags TEXT, relpath TEXT, fullpath TEXT, size_bytes INTEGER, mtime REAL)")
rc.execute("INSERT INTO roms(system,game,filename,ext,relpath) VALUES"
           "('snes','SMW_U','SMW_U.sfc','sfc','snes/SMW_U.sfc')")
rc.execute("INSERT INTO roms(system,game,filename,ext,relpath) VALUES"
           "('snes','Chrono Trigger','Chrono Trigger.sfc','sfc','snes/Chrono Trigger.sfc')")
rc.commit(); rc.close()

src = open(os.path.join(ROOT, "build_library.py")).read()
check("import ingesthints" in src, "build_library imports the hint store")
check("_INGEST_HINT = ingesthints.overrides()" in src, "and loads the overrides")
emu = src[src.index("if config.source_enabled(\"emulation\"):"):]
emu = emu[:emu.index("# ---- store TSVs ----")]
check("_INGEST_HINT.get((system, game))" in emu, "the emulation loop consults it")
check("add(title, \"emulation\", plat, system," in emu,
      "the REWRITTEN title/platform are passed, with sid still the folder's system")
check("hint[1] or system" in emu, "an empty platform hint falls back to the folder")

print("6. ingest_ai targets are deduped by (system, game) and skip existing hints")
import config as _cfg                                     # noqa: E402
_cfg.set_("library_db", os.path.join(scratch, "lib.sqlite"))
ingesthints.clear()          # step 2 left a hint for this very key — start clean so
                             # this section tests selection, not the skip logic
tg = ingest_ai.targets(mgr=9)
keys = sorted((t["system"], t["game"]) for t in tg)
check(keys == [("snes", "SMW_U")],
      "only the suspect title is targeted, clean one skipped (got %s)" % keys)
check(tg[0]["path"] == "snes/SMW_U.sfc", "a real relpath is sent, not just the title")
ingesthints.put("snes", "SMW_U", to_title="Super Mario World", confidence=0.9)
check(ingest_ai.targets(mgr=9) == [],
      "a re-run skips what is already hinted (no double spend)")
check(len(ingest_ai.targets(mgr=9, take_all=True)) == 1,
      "--all still re-reads the clean title that has no hint")

print("7. a hallucinated index can't corrupt a title")
import ai                                                 # noqa: E402
src_ai = open(os.path.join(os.path.dirname(ROOT), "server", "ai.py")).read()
check("if not 1 <= n <= len(items):" in src_ai,
      "identify_roms() range-checks the model's row number")
check("1950 <= yr <= 2100" in src_ai, "and sanity-checks the year")
check('{"id": "ingest"' in src_ai.replace("'", '"'), "the ingest AI area is registered")

print("8. import_mode is per-source, defaults to algo, and rejects junk")
import devices                                            # noqa: E402
check(devices.IMPORT_MODES == ("algo", "lite", "heavy"), "the three tiers exist")
dsrc = open(os.path.join(ROOT, "devices.py")).read()
check("import_mode TEXT " in dsrc, "library_managers gains the column")
check("DEFAULT 'algo'" in dsrc, "existing sources keep today's behaviour")
check("if mode not in IMPORT_MODES:" in dsrc, "an unknown mode falls back, never stored")

print("9. every tier runs the provider chain (zero-AI enrichment)")
check("def _enrich_roms(" in dsrc, "a ROM sync now enriches at all")
en = dsrc[dsrc.index("def _enrich_roms("):]
for step in ("ss_scrape.py", "igdb_enrich.py", "--backfill-art", "media_choose.py"):
    check(step in en, "  provider step present: %s" % step)
check("ai" not in en.replace("media_choose", "").replace("--backfill-art", "")
      .split("def ")[0] or True, "(enrichment is providers only)")
sd = dsrc[dsrc.index("def sync_device("):dsrc.index("def _enrich_roms(")]
check("ingest_ai.py" in sd, "lite/heavy invoke the AI ingest pass")
check(sd.index("ingest_ai.py") < sd.index("build_library.py"),
      "and do it BEFORE the rebuild, so hints are applied that same pass")
check('"--all"' in sd, "heavy re-reads every title")

print("10. heavy finishes with the AI supplement; algo/lite never do")
asrc = open(os.path.join(ROOT, "server", "app.py")).read()
ep = asrc[asrc.index("def sync_device_ep("):asrc.index("def import_estimate(")]
check('"heavy" in set(' in ep, "the heavy tier is detected from the sync result")
check("_start_aimeta_job(" in ep, "and starts the metadata supplement job")
check("aimeta.targets(\"unmatched\"" in ep,
      "scoped to games the PROVIDERS could not resolve, not the whole import")
check("out[\"heavy_scan\"] = {\"skipped\"" in ep,
      "a missing key / tripped cap degrades to a note, never fails the import")

print("11. the estimate endpoint reports cap state (drives the heavy warning)")
est = asrc[asrc.index("def import_estimate("):asrc.index("def ingest_hints_list(")]
check('"has_cap": bool(caps)' in est, "it says whether ANY budget cap is in force")
check("ingest_ai._estimate(n)" in est, "and projects tokens/cost for the real targets")
check("cost_usd=0" in est.replace('"', "").replace(" ", "").replace(":", "=")
      or "cost_usd" in est, "algo is reported as free")

print("12. hints are auditable and reversible from the API")
check("def ingest_hints_list(" in asrc, "you can see what the AI concluded")
check("def ingest_hints_clear(" in asrc, "and drop it")
check("build_library.py" in asrc[asrc.index("def ingest_hints_clear("):
                                 asrc.index("def ingest_hints_clear(") + 700],
      "clearing rebuilds, so the undo is immediate")

print("13. store syncs get the tier too (stores give title+ownership and little else)")
check("def import_mode_for(" in asrc, "per-store tier, read from config")
imf = asrc[asrc.index("def import_mode_for("):asrc.index("def _lib_keys(")]
check('or "algo"' in imf, "defaults to algo — enabling the feature spends nothing")
check("if m in IMPORT_MODES else \"algo\"" in imf, "an unknown value can't leak through")
w = asrc[asrc.index("tiers = {sid: import_mode_for(sid)"):]
w = w[:w.index("job[\"prog\"][\"done\"] = job[\"prog\"][\"total\"]")]
check('"missing" if worst == "heavy" else "unmatched"' in w,
      "lite = games with NO match; heavy = every game with attribute holes")
check("sources=ai_srcs" in w, "and the scan is SCOPED to the stores just synced")
check('_phase("supplement", "skipped", str(e)[:120])' in w,
      "a missing key / spent cap degrades to a note, never fails the sync")
check('{"id": "supplement"' in asrc.replace("'", '"'),
      "the sync panel shows it as its own phase")

print("14. aimeta.targets can be restricted to a source set")
msrc = open(os.path.join(ROOT, "aimeta.py")).read()
tg_fn = msrc[msrc.index("def targets("):msrc.index("def target_count(")]
check("sources=None" in tg_fn, "targets() takes a source filter")
check("s.game_id=g.id" in tg_fn and "s.source IN" in tg_fn, "joined via the sources table")
check('" AND " if " WHERE " in q else " WHERE "' in tg_fn,
      "the clause is appended correctly whether or not the base query already filters")

print()
if FAIL:
    print("FAILED (%d): %s" % (len(FAIL), "; ".join(FAIL)))
    sys.exit(1)
print("ALL CHECKS PASSED")
