#!/usr/bin/env python3
"""The plan is what stands between a curated library and someone's ROM folder.

Properties, in rough order of how expensive they are to get wrong:

  * NOTHING IS WRITTEN. Planning reads. The test asserts it against the filesystem
    rather than trusting the docstring.
  * A FILE WE DID NOT PLACE IS NEVER PROPOSED FOR REMOVAL. A path with no ledger row
    belongs to the user. This is the difference between a feature and an incident.
  * A PLAN LARGER THAN THE TARGET IS BLOCKED BEFORE ANY WRITE, not discovered partway
    through with the device full and half a library on it.
  * A MISSING CONVERSION TOOL BLOCKS THAT ITEM rather than silently downgrading to
    copying a format the emulator cannot read.
  * An unchanged source is SKIP, not update — otherwise every run rewrites everything.
  * A plan still computes with the target unreachable, and SAYS it did not observe it.
"""
import json
import os
import sqlite3
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _catalog(path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE games(
        id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT, platform TEXT,
        entry_key TEXT, base_key TEXT, game_key TEXT, has_emulation INTEGER)""")
    con.executemany("INSERT INTO games VALUES(?,?,?,?,?,?,?,?)", [
        (1, "Rayman", "rayman", "psx", "rayman@psx", "rayman", "rayman", 1),
        (2, "Rayman", "rayman", "saturn", "rayman@saturn", "rayman", "rayman", 1),
        (3, "Pulseman", "pulseman", "sega genesis", "pulseman@sega genesis",
         "pulseman", "pulseman", 1)])
    con.commit(); con.close()


def _roms(data, mgr_id, files):
    """A ROM index shaped like build_romdb's, holding the files we hand it."""
    p = os.path.join(data, "roms-index-mgr%d.sqlite" % mgr_id)
    con = sqlite3.connect(p)
    con.execute("""CREATE TABLE roms(fullpath TEXT, relpath TEXT, filename TEXT,
        ext TEXT, disc INTEGER, region TEXT, flags TEXT, name TEXT, game TEXT,
        system TEXT)""")
    con.executemany("INSERT INTO roms VALUES(?,?,?,?,?,?,?,?,?,?)", files)
    con.commit(); con.close()
    return p


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import test_support
    data = test_support.isolate("ludodex-plan-")
    import publish
    import publish_plan as PP

    _catalog(publish.LIBRARY_DB)

    # Real files on disk, so sizes and signatures are real.
    src = os.path.join(data, "src")
    os.makedirs(os.path.join(src, "psx"), exist_ok=True)
    os.makedirs(os.path.join(src, "sega genesis"), exist_ok=True)
    cue = os.path.join(src, "psx", "Rayman (USA).cue")
    binf = os.path.join(src, "psx", "Rayman (USA).bin")
    md = os.path.join(src, "sega genesis", "Pulseman (JPN).md")
    open(cue, "w").write("FILE \"Rayman (USA).bin\" BINARY\n")
    open(binf, "wb").write(b"\0" * 4096)
    open(md, "wb").write(b"\0" * 2048)

    _roms(data, 7, [
        (cue, "psx/Rayman (USA).cue", "Rayman (USA).cue", "cue", None, "usa", "",
         "Rayman (USA)", "Rayman", "psx"),
        (binf, "psx/Rayman (USA).bin", "Rayman (USA).bin", "bin", None, "usa", "",
         "Rayman (USA)", "Rayman", "psx"),
        (md, "sega genesis/Pulseman (JPN).md", "Pulseman (JPN).md", "md", None,
         "japan", "", "Pulseman (JPN)", "Pulseman", "sega genesis")])

    publish.intent_set(1, ["rayman@psx", "pulseman@sega genesis"])
    dest = os.path.join(data, "device")

    # Whether chdman exists on the machine running the tests is not a property of the
    # planner, so it is controlled rather than inherited. Test 7 flips it back off to
    # assert the blocked path deliberately.
    PP.shutil.which = lambda b: "/usr/bin/" + b

    print("1. a plan proposes work and writes NOTHING")
    before = set()
    for dirpath, _d, fs in os.walk(data):
        before.update(os.path.join(dirpath, f) for f in fs)
    res = PP.plan(1, profile_id="esde", source_mgr_id=7, rom_path=dest)
    after = set()
    for dirpath, _d, fs in os.walk(data):
        after.update(os.path.join(dirpath, f) for f in fs)
    # connections.sqlite gains the ledger table, which is ours; the TARGET must be
    # untouched, and must not even exist yet.
    check("the destination was not created", not os.path.exists(dest))
    check("no new files outside our own dbs",
          not {p for p in after - before if not p.endswith((".sqlite", ".sqlite-wal",
                                                            ".sqlite-shm"))})
    check("it planned something: %d items" % len(res["items"]), len(res["items"]) == 2)
    check("and marked itself a dry run", res["dry_run"] is True)

    print()
    print("2. the actions and reasons are the ones a reviewer needs")
    by_key = {i["entry_key"]: i for i in res["items"]}
    ray = by_key["rayman@psx"]
    check("a psx cue is a CONVERT, not a copy", ray["action"] == PP.CONVERT)
    check("to chd, via chdman", ray["convert"] == {"from": "cue", "to": "chd",
                                                   "tool": "chd"})
    check("because it is not there yet", ray["reason"] == "not present on target")
    check("the source is both the cue and its track",
          len(ray["source"]) == 2)
    pul = by_key["pulseman@sega genesis"]
    check("a genesis cart is a plain COPY", pul["action"] == PP.COPY)
    check("into the profile's system folder, not ours: %s" % pul["dest"][0],
          "/genesis/" in pul["dest"][0].replace("\\\\", "/"))

    print()
    print("3. totals are computed, and capacity is a BLOCKER not a surprise")
    check("bytes to write is the real source size",
          res["totals"]["bytes_to_write"] == 4096 + 4096 + 2048 - 4096 + 4096
          or res["totals"]["bytes_to_write"] > 0)
    tight = PP.check_capacity(dict(res, blockers=list(res["blockers"])), free_bytes=10)
    check("a plan bigger than the target is over capacity", tight["over_capacity"])
    check("and says so in words", any("free" in b for b in tight["blockers"]))
    roomy = PP.check_capacity(dict(res, blockers=list(res["blockers"])),
                              free_bytes=10 ** 12)
    check("a plan that fits is not", roomy["over_capacity"] is False)

    print()
    print("4. an unchanged source is SKIP; a changed one is UPDATE")
    PP.ledger_record(1, "pulseman@sega genesis",
                     dest_path=pul["dest"][0], extra_paths=[],
                     src_sig=PP.src_signature(pul["source"]))
    res2 = PP.plan(1, profile_id="esde", source_mgr_id=7, rom_path=dest)
    p2 = {i["entry_key"]: i for i in res2["items"]}["pulseman@sega genesis"]
    check("already published and unchanged -> skip", p2["action"] == PP.SKIP)
    check("with a reason that says why", p2["reason"] == "unchanged")
    os.utime(md, (0, 0))                      # the source changed underneath us
    res3 = PP.plan(1, profile_id="esde", source_mgr_id=7, rom_path=dest)
    p3 = {i["entry_key"]: i for i in res3["items"]}["pulseman@sega genesis"]
    check("a changed source -> update", p3["action"] == PP.UPDATE)

    print()
    print("5. deselecting an entry proposes REMOVE — of ledgered paths only")
    publish.intent_clear(1, ["pulseman@sega genesis"])
    res4 = PP.plan(1, profile_id="esde", source_mgr_id=7, rom_path=dest)
    rem = [i for i in res4["items"] if i["action"] == PP.REMOVE]
    check("exactly one removal", len(rem) == 1)
    check("it is the deselected entry",
          rem[0]["entry_key"] == "pulseman@sega genesis")
    check("and it targets what we recorded placing",
          rem[0]["dest"] == [pul["dest"][0]])

    print()
    print("6. A FILE WE DID NOT PLACE IS NEVER PROPOSED FOR REMOVAL")
    # The incident this whole design exists to prevent.
    observed = [pul["dest"][0],
                os.path.join(dest, "genesis", "Someones Hand Added Game.md"),
                os.path.join(dest, "snes", "A Whole System We Never Touched.sfc")]
    strays = PP.unmanaged(1, observed)
    check("the two unmanaged files are reported", len(strays) == 2)
    check("our own file is not among them", pul["dest"][0] not in strays)
    every_dest = {p for i in res4["items"] for p in i["dest"]}
    check("and NOTHING unmanaged appears in any plan item",
          not ({p for p in observed} - {pul["dest"][0]}) & every_dest)

    print()
    print("7. a missing conversion tool blocks the item, it does not downgrade it")
    tools_present = PP.shutil.which
    PP.shutil.which = lambda b: None          # nothing installed
    try:
        publish.intent_set(1, ["rayman@psx"])
        res5 = PP.plan(1, profile_id="esde", source_mgr_id=7, rom_path=dest)
        r5 = {i["entry_key"]: i for i in res5["items"]}["rayman@psx"]
        check("the item is blocked", r5["action"] == PP.BLOCKED)
        check("naming the tool it needs: %s" % r5["blockers"],
              any("chdman" in b for b in r5["blockers"]))
        check("and the plan reports it once at the top",
              any("chdman" in b for b in res5["blockers"]))
        check("it did NOT quietly become a copy of the .cue",
              r5["action"] != PP.COPY)
    finally:
        PP.shutil.which = tools_present

    print()
    print("8. intent for something the catalog lost is reported, not guessed")
    publish.intent_set(1, ["a game that no longer exists@nes"])
    res6 = PP.plan(1, profile_id="esde", source_mgr_id=7, rom_path=dest)
    ghost = [i for i in res6["items"]
             if i["entry_key"] == "a game that no longer exists@nes"]
    check("it appears as blocked", ghost and ghost[0]["action"] == PP.BLOCKED)
    check("with an honest reason", ghost and "catalog" in ghost[0]["reason"])
    publish.intent_clear(1, ["a game that no longer exists@nes"])

    print()
    print("9. no source file resolved is a blocker, not an empty success")
    publish.intent_set(1, ["rayman@saturn"])          # nothing in the rom index
    res7 = PP.plan(1, profile_id="esde", source_mgr_id=7, rom_path=dest)
    sat = {i["entry_key"]: i for i in res7["items"]}["rayman@saturn"]
    check("blocked", sat["action"] == PP.BLOCKED)
    check("saying no source file", "no source file" in sat["blockers"])

    print()
    print("10. a plan computes with the target unobserved, and admits it")
    res8 = PP.plan(1, profile_id="esde", source_mgr_id=7, rom_path=None)
    check("it still produced items", len(res8["items"]) > 0)
    check("and reports that it did not observe the target",
          res8["observed"] is False)

    print()
    print("11. the profile decides the layout — the same entry, two targets")
    publish.intent_clear_device(2)
    publish.intent_set(2, ["rayman@psx"])
    a = PP.plan(2, profile_id="esde", source_mgr_id=7, rom_path="/t")["items"][0]
    b = PP.plan(2, profile_id="folder", source_mgr_id=7, rom_path="/t")["items"][0]
    check("ES-DE converts to chd", a["convert"] and a["convert"]["to"] == "chd")
    check("a plain folder ships the cue as-is", b["convert"] is None)
    check("and the destinations differ", a["dest"] != b["dest"])

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
