#!/usr/bin/env python3
"""Publish intent is per (game, platform), and that is the whole point.

The properties worth protecting, none of which a happy-path add/list would show:

  * THE RAYMAN CASE. Marking the Saturn entry must not mark the PS1 one. Keyed by
    norm_key — which is what device_wants did — this is unsayable, and it is the
    ordinary case for anyone whose ROM library overlaps their store libraries.
  * EXCLUDE IS NOT ABSENCE. "Everything SNES except these four" has to survive
    re-evaluating "everything SNES", so a no is recorded rather than inferred.
  * MIGRATION IS IDEMPOTENT AND NON-DESTRUCTIVE. Re-running it must not resurrect an
    entry the user removed afterwards, and must not drop a queue it cannot expand.
  * A MISSING CATALOG IS NOT AN EMPTY ONE. Expanding a title needs the catalog; if it
    is absent, migration must report that rather than quietly discarding intent.
"""
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
    """A minimal catalog: one title on three platforms, one title on one."""
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE games(
        id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT, platform TEXT,
        entry_key TEXT, base_key TEXT, game_key TEXT, has_emulation INTEGER)""")
    rows = [(1, "Rayman", "rayman", "psx", "rayman@psx", "rayman", "rayman", 1),
            (2, "Rayman", "rayman", "saturn", "rayman@saturn", "rayman", "rayman", 1),
            (3, "Rayman", "rayman", "steam", "rayman@steam", "rayman", "rayman", 0),
            (4, "Pulseman", "pulseman", "genesis", "pulseman@genesis", "pulseman",
             "pulseman", 1)]
    con.executemany("INSERT INTO games VALUES(?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()


def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "ludodex"))
    import test_support
    test_support.isolate("ludodex-publish-")
    import publish

    _catalog(publish.LIBRARY_DB)

    print("1. a title resolves to every platform it exists on")
    ents = publish.entries_for("rayman")
    check("three entries: %s" % [e["platform"] for e in ents], len(ents) == 3)
    check("and they carry the platform", {e["platform"] for e in ents} ==
          {"psx", "saturn", "steam"})
    check("an unknown title resolves to nothing", publish.entries_for("nope") == [])

    print()
    print("2. THE RAYMAN CASE — marking one platform marks only that platform")
    publish.intent_set(1, ["rayman@saturn"])
    keys = publish.intent_keys(1)
    check("exactly one entry marked: %s" % keys, keys == ["rayman@saturn"])
    check("the PS1 entry is NOT marked", "rayman@psx" not in keys)
    check("the device shows up for the saturn entry",
          publish.intent_for_entry("rayman@saturn") == [1])
    check("but not for the psx entry", publish.intent_for_entry("rayman@psx") == [])

    print()
    print("3. the title view reports WHICH platforms, not just how many devices")
    publish.intent_set(2, ["rayman@psx", "rayman@steam"])
    by_dev = publish.intent_for_title("rayman")
    check("two devices want some Rayman", set(by_dev) == {1, 2})
    check("device 1 wants the saturn one", by_dev[1] == ["rayman@saturn"])
    check("device 2 wants the other two", sorted(by_dev[2]) ==
          ["rayman@psx", "rayman@steam"])

    print()
    print("4. exclude is a recorded decision, not a missing row")
    publish.intent_set(1, ["rayman@psx"], state=publish.EXCLUDE)
    check("it is not in the include set",
          "rayman@psx" not in publish.intent_keys(1))
    check("it IS in the exclude set",
          publish.intent_keys(1, state=publish.EXCLUDE) == ["rayman@psx"])
    # ...and clearing is different from excluding: one forgets, the other refuses.
    publish.intent_clear(1, ["rayman@psx"])
    check("clearing removes the opinion entirely",
          publish.intent_keys(1, state=publish.EXCLUDE) == [])

    print()
    print("5. an explicit call always wins over what is already there")
    publish.intent_set(1, ["rayman@saturn"], state=publish.EXCLUDE)
    check("include flipped to exclude", publish.intent_keys(1) == [])
    publish.intent_set(1, ["rayman@saturn"], state=publish.INCLUDE)
    check("and back again", publish.intent_keys(1) == ["rayman@saturn"])

    print()
    print("6. migrating device_wants expands each title to its entries")
    con = publish._con()
    con.execute("""CREATE TABLE IF NOT EXISTS device_wants(
        id INTEGER PRIMARY KEY AUTOINCREMENT, device_id INTEGER, norm_key TEXT,
        added REAL, UNIQUE(device_id, norm_key))""")
    con.executemany("INSERT INTO device_wants(device_id,norm_key,added) VALUES(?,?,?)",
                    [(3, "rayman", 100.0), (3, "pulseman", 100.0),
                     (3, "a game the catalog has never heard of", 100.0)])
    con.commit(); con.close()

    res = publish.migrate()
    check("it expanded the two it could: %d" % res["titles_expanded"],
          res["titles_expanded"] == 2)
    check("into four entries (3 rayman + 1 pulseman): %d" % res["entries_written"],
          res["entries_written"] == 4)
    check("and reported the one it could not", res["unresolved"] == 1)
    check("device 3 now has all three rayman platforms",
          len([k for k in publish.intent_keys(3) if k.startswith("rayman@")]) == 3)

    print()
    print("7. re-running migration does NOT resurrect a removed entry")
    # The trap: a user migrates, then removes the Steam entry because their handheld
    # cannot run it. A second migration must not put it back.
    publish.intent_clear(3, ["rayman@steam"])
    check("removed", "rayman@steam" not in publish.intent_keys(3))
    publish.migrate()
    check("still removed after a second migration",
          "rayman@steam" not in publish.intent_keys(3))
    check("and the others survived", "rayman@psx" in publish.intent_keys(3))

    print()
    print("8. migration never drops a queue it cannot expand")
    con = publish._con()
    left = con.execute("SELECT COUNT(*) FROM device_wants").fetchone()[0]
    con.close()
    check("device_wants is left intact for a later rebuild to resolve: %d" % left,
          left == 3)

    print()
    print("9. a missing catalog is reported, not treated as an empty one")
    os.rename(publish.LIBRARY_DB, publish.LIBRARY_DB + ".away")
    try:
        r = publish.migrate()
        check("it refuses rather than expanding to nothing",
              r.get("blocked") and r.get("entries") == 0)
        # Only ONE is still outstanding: the other two were expanded earlier and are
        # marked migrated, so they are not re-reported as work remaining.
        check("and counts only what is still outstanding: %s" % r.get("unresolved"),
              r.get("unresolved") == 1)
    finally:
        os.rename(publish.LIBRARY_DB + ".away", publish.LIBRARY_DB)

    print()
    print("10. the title-shaped view still works for callers that only know a name")
    import devices
    check("wants_keys reports distinct titles",
          sorted(devices.wants_keys(2)) == ["rayman"])
    check("wants_for_key lists devices wanting any entry",
          devices.wants_for_key("rayman") == [1, 2, 3])
    counts = devices.wants_counts()
    check("counts are of ENTRIES, which is what gets published: %s" % counts,
          counts.get(2) == 2)

    print()
    print("11. a rule is a saved SELECTION; explicit marks outrank it")
    # The precedence that makes "everything SNES except these four" expressible. The
    # rule's matches come in from the caller (the server evaluates the filter); what is
    # under test is how they combine with what the user said by hand.
    publish.intent_clear_device(9)
    rule_hits = ["rayman@psx", "rayman@saturn", "pulseman@genesis"]
    e = publish.effective(9, rule_hits)
    check("with no manual opinions, the rule IS the set",
          e["entries"] == sorted(rule_hits))

    publish.intent_set(9, ["rayman@saturn"], state=publish.EXCLUDE)
    e = publish.effective(9, rule_hits)
    check("an exclude removes it even though the rule matched",
          "rayman@saturn" not in e["entries"])
    check("and it is reported as an override, not silently dropped",
          e["excluded_from_rules"] == ["rayman@saturn"])

    publish.intent_set(9, ["rayman@steam"])          # not matched by the rule
    e = publish.effective(9, rule_hits)
    check("a manual include joins the set", "rayman@steam" in e["entries"])
    check("the parts are reported separately: rules=%d manual=%d excluded=%d"
          % (e["from_rules"], e["explicit_includes"], e["explicit_excludes"]),
          e["from_rules"] == 3 and e["explicit_includes"] == 1
          and e["explicit_excludes"] == 1)

    print()
    print("12. rules persist, list in order, and can be removed")
    rid = publish.rule_set(9, "platform:snes", label="All SNES")
    publish.rule_set(9, "platform:genesis", label="All Genesis", ord=1)
    rules = publish.rules_list(9)
    check("both saved", len(rules) == 2)
    check("in order", [r["label"] for r in rules] == ["All SNES", "All Genesis"])
    publish.rule_rm(9, rid)
    check("and one can be removed", len(publish.rules_list(9)) == 1)
    check("a device with no rules has none", publish.rules_list(999) == [])

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
