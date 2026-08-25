#!/usr/bin/env python3
"""What the AI ingest pass is ASKED, and what it is allowed to remember.

Two functions decide the whole cost and the whole blast radius of the tiered import.

`ingest_ai.targets` picks which ROM titles are worth a model call. Every item it returns
is money; every item it drops is a mangled title that silently becomes a catalog entry.
The rules that keep both honest:

  * DEDUPE BY (system, game). The catalog groups ROMs that way, so one answer covers a
    game's whole set — regions, revisions, discs. A per-FILE target list would ask the
    same question for every one of them.
  * ONLY SUSPECT TITLES, unless asked for all. `_suspect` is deliberately asymmetric: a
    false positive costs about fifty tokens and the model answers "already right", while
    a false NEGATIVE ships "SMW_U" into the library forever. So real games that trip it
    ("Ico", "1943") are accepted, not tuned away.
  * SKIP WHAT IS ALREADY HINTED. Without this a re-run pays a second time for every
    answer already on disk. `--all` still re-reads the titles that have no hint yet.
  * READ-ONLY. `--estimate` calls this function, and an estimate that writes hints is
    not an estimate.

`ingesthints.put` records the answer. A hint is ADVISORY, keyed by (system, game), and
applied at add() time so the ROM index stays a faithful description of what is on disk
and the whole thing stays reversible. The rule worth pinning is the refusal: a hint that
asserts NOTHING is dropped rather than stored, so `overrides()` never has to filter noise
it could have avoided — and, because `have_keys()` drives the skip above, a stored empty
hint would also mean permanently never asking about that game again.

Offline. No AI calls, no network — nothing here goes near a model.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-ported-hints-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import ingest_ai                                               # noqa: E402
import ingesthints                                             # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def seed_rom_index(mgr, rows, with_hashes=False):
    """A minimal ROM index in the shape build_romdb produces."""
    p = os.path.join(DATA, "roms-index-mgr%d.sqlite" % mgr)
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE roms (id INTEGER PRIMARY KEY, system TEXT, subdir TEXT, "
                "game TEXT, filename TEXT, ext TEXT, name TEXT, region TEXT, "
                "languages TEXT, version TEXT, revision TEXT, disc TEXT, flags TEXT, "
                "tags TEXT, relpath TEXT, fullpath TEXT, size_bytes INTEGER, mtime REAL)")
    if with_hashes:
        con.execute("CREATE TABLE rom_hashes (relpath TEXT PRIMARY KEY, crc TEXT, "
                    "sha1 TEXT)")
    for system, game, relpath in rows:
        con.execute("INSERT INTO roms(system,game,filename,ext,relpath) VALUES(?,?,?,?,?)",
                    (system, game, os.path.basename(relpath),
                     relpath.rsplit(".", 1)[-1], relpath))
    con.commit()
    con.close()
    return p


def keys(targets):
    return sorted((t["system"], t["game"]) for t in targets)


def main():
    print("what the ingest pass is asked, and what it remembers")
    test_support.assert_isolated()
    check("both modules resolved DATA to the fixture dir",
          os.path.abspath(ingesthints.DATA) == os.path.abspath(DATA)
          and os.path.abspath(ingest_ai.DATA) == os.path.abspath(DATA))

    print()
    print("1. the suspect heuristic picks mangled titles and leaves good ones alone")
    for t in ("SMW_U", "FF7", "SMBDLX", "GRADIUS3", "sonic2b", "", "DK", "smb3"):
        check("suspect:     %r" % t, ingest_ai._suspect(t))
    for t in ("Super Mario World", "Final Fantasy VII", "Doom", "Chrono Trigger",
              "Sonic the Hedgehog 2", "R-Type", "III", "Metal Gear Solid"):
        check("not suspect: %r" % t, not ingest_ai._suspect(t))
    # Real games that trip it. Asserted rather than quietly tolerated: the asymmetry
    # (a cheap false positive, an expensive false negative) IS the design, and someone
    # "fixing" these would be trading it away.
    for t in ("Ico", "1943"):
        check("known-acceptable false positive: %r" % t, ingest_ai._suspect(t))

    print()
    print("2. targets() asks about the suspect titles only, deduped by (system, game)")
    seed_rom_index(9, [
        ("snes", "SMW_U", "snes/SMW_U.sfc"),
        ("snes", "SMW_U", "snes/SMW_U_(E).sfc"),        # same game, second region dump
        ("snes", "SMW_U", "snes/SMW_U_rev1.sfc"),       # and a revision
        ("snes", "Chrono Trigger", "snes/Chrono Trigger.sfc"),
        ("gen", "SONIC2B", "gen/SONIC2B.bin"),
    ])
    tg = ingest_ai.targets(mgr=9)
    check("only the two mangled titles are targeted: %s" % keys(tg),
          keys(tg) == [("gen", "SONIC2B"), ("snes", "SMW_U")])
    check("three files of one game are ONE question, not three",
          len([t for t in tg if t["game"] == "SMW_U"]) == 1)
    check("and a real relpath rides along, not just the title",
          [t for t in tg if t["game"] == "SMW_U"][0]["path"].startswith("snes/SMW_U"))

    print()
    print("3. --all re-reads the clean titles too")
    allt = ingest_ai.targets(mgr=9, take_all=True)
    check("the clean title is now included: %s" % keys(allt),
          keys(allt) == [("gen", "SONIC2B"), ("snes", "Chrono Trigger"),
                         ("snes", "SMW_U")])
    check("still deduped", len(allt) == 3)
    check("limit caps the ask", len(ingest_ai.targets(mgr=9, take_all=True, limit=2)) == 2)

    print()
    print("4. targets() is READ-ONLY — an estimate that writes hints is not an estimate")
    check("nothing was recorded by any of the calls above", ingesthints.count() == 0)

    print()
    print("5. a hint that asserts nothing is REFUSED, not stored")
    check("no title, no platform, no year -> refused",
          ingesthints.put("snes", "Chrono Trigger", to_title="", to_platform="",
                          year=None) is False)
    check("and nothing was written", ingesthints.count() == 0)
    check("a year alone IS an assertion, so it is kept",
          ingesthints.put("snes", "Yearonly", year=1995) is True)
    check("a platform alone too",
          ingesthints.put("snes", "Platonly", to_platform="snes") is True)
    check("but a nameless key is refused", ingesthints.put("", "", to_title="X") is False)
    ingesthints.clear()

    print()
    print("6. a real hint round-trips as (title, platform, year)")
    check("it is stored",
          ingesthints.put("snes", "SMW_U", to_title="Super Mario World",
                          confidence=0.95, sample_path="snes/SMW_U.sfc") is True)
    ov = ingesthints.overrides()
    check("overrides() exposes it in build_library's shape: %r" % (ov.get(("snes", "SMW_U")),),
          ov.get(("snes", "SMW_U")) == ("Super Mario World", "", None))
    check("the refused empty hint never reaches build_library",
          ("snes", "Chrono Trigger") not in ov)
    check("and it is auditable — the sample path is kept for a human",
          ingesthints.listing()[0]["sample_path"] == "snes/SMW_U.sfc")

    print()
    print("7. re-answering REPLACES rather than duplicating")
    ingesthints.put("snes", "SMW_U", to_title="Super Mario World (USA)",
                    to_platform="snes", year=1990, confidence=0.99)
    check("still one row for that key", ingesthints.count() == 1)
    check("holding the newer answer",
          ingesthints.overrides()[("snes", "SMW_U")]
          == ("Super Mario World (USA)", "snes", 1990))

    print()
    print("8. the confidence floor filters without deleting")
    ingesthints.put("gen", "SONIC2B", to_title="Sonic the Hedgehog 2", confidence=0.30)
    check("the low-confidence hint IS stored",
          ("gen", "SONIC2B") in ingesthints.overrides())
    check("but a caller can require >= 0.5 and not see it",
          ("gen", "SONIC2B") not in ingesthints.overrides(min_confidence=0.5))
    check("while the confident one survives that bar",
          ("snes", "SMW_U") in ingesthints.overrides(min_confidence=0.5))

    print()
    print("9. a re-run does not pay twice for an answer already on disk")
    check("both hinted games drop out of the target list: %s"
          % keys(ingest_ai.targets(mgr=9)), ingest_ai.targets(mgr=9) == [])
    check("--all still re-reads the title that has NO hint yet",
          keys(ingest_ai.targets(mgr=9, take_all=True))
          == [("snes", "Chrono Trigger")])
    check("and the skip can be turned off explicitly",
          keys(ingest_ai.targets(mgr=9, skip_hinted=False))
          == [("gen", "SONIC2B"), ("snes", "SMW_U")])

    print()
    print("10. clear() is a real undo, and it can be scoped to one system")
    check("two hints are on record", ingesthints.count() == 2)
    ingesthints.clear("gen")
    check("clearing one system leaves the other", ingesthints.count() == 1)
    check("and it is the right one", ("snes", "SMW_U") in ingesthints.overrides())
    check("the cleared game is askable again",
          ("gen", "SONIC2B") in keys(ingest_ai.targets(mgr=9)))
    ingesthints.clear()
    check("clearing everything reverts to the algorithmic titles",
          ingesthints.count() == 0 and ingesthints.overrides() == {})

    print()
    print("11. the free answer rides along: a hash, when the index has one")
    seed_rom_index(8, [("nes", "SMB_U", "nes/SMB_U.nes")], with_hashes=True)
    con = sqlite3.connect(os.path.join(DATA, "roms-index-mgr8.sqlite"))
    con.execute("INSERT INTO rom_hashes VALUES ('nes/SMB_U.nes','D445F698','abc123')")
    con.commit()
    con.close()
    t8 = ingest_ai.targets(mgr=8)
    check("the CRC is carried, so identify_from_index can answer for free",
          t8 and t8[0]["crc"] == "D445F698" and t8[0]["sha1"] == "abc123")
    check("an index with no hash table still works, reporting none",
          ingest_ai.targets(mgr=9)[0]["crc"] is None)

    print()
    print("12. a manager filter means one manager")
    check("mgr=8 sees only its own index", keys(ingest_ai.targets(mgr=8))
          == [("nes", "SMB_U")])
    check("mgr=9 does not see mgr 8's rows",
          ("nes", "SMB_U") not in keys(ingest_ai.targets(mgr=9)))
    check("and no filter sees both",
          set(keys(ingest_ai.targets())) >= {("nes", "SMB_U"), ("snes", "SMW_U")})

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
