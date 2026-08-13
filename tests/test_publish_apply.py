#!/usr/bin/env python3
"""Apply is the only code that writes to a target, so every test here is about damage.

  * A PLAN WITH BLOCKERS IS REFUSED WHOLE. Applying "the parts that work" leaves a
    device holding half a curated set with no record of which half.
  * AN INTERRUPTED APPLY LEAVES THE OLD FILE OR THE NEW ONE — never a truncated ROM
    that a frontend indexes happily and fails to launch.
  * REMOVAL RE-CHECKS THE LEDGER AT THE MOMENT OF DELETION. A plan proposing a removal
    is not authority; it was computed against a ledger read minutes ago.
  * A FILE WE DID NOT PLACE IS NEVER DELETED, even when a plan names it. This is
    simulated directly by handing apply a doctored plan, because that is precisely the
    shape a bug elsewhere would take.
  * ONE BAD ITEM DOES NOT STRAND THE REST.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import test_support
    data = test_support.isolate("ludodex-apply-")
    import publish_plan as PP
    import publish_apply as PA

    dev = 0                     # falsy device id == this machine, per devices.push_file
    src = os.path.join(data, "src")
    dst = os.path.join(data, "target", "genesis")
    os.makedirs(src, exist_ok=True)
    rom = os.path.join(src, "Pulseman.md")
    open(rom, "wb").write(b"ROMDATA" * 100)

    def item(action=PP.COPY, name="Pulseman.md", key="pulseman@genesis", conv=None,
             source=None, dest=None, blockers=None):
        return {"entry_key": key, "title": "Pulseman", "platform": "sega genesis",
                "system": "genesis", "action": action, "reason": "t",
                "source": source if source is not None else [rom],
                "dest": dest if dest is not None else [os.path.join(dst, name)],
                "convert": conv, "bytes_in": 700, "disc": None,
                "blockers": blockers or []}

    def plan(items, blockers=None, over=False):
        return {"device_id": dev, "profile": "esde", "observed": True, "dry_run": True,
                "items": items, "totals": {"items": len(items), "bytes_to_write": 700},
                "blockers": blockers or [], "over_capacity": over}

    print("1. a plan with blockers is refused WHOLE — nothing is written")
    try:
        PA.apply_plan(dev, plan([item()], blockers=["chdman not available"]))
        check("it raised", False)
    except PA.ApplyError as e:
        check("it refused: %s" % str(e)[:40], "blockers" in str(e))
    check("and wrote nothing", not os.path.exists(dst))

    print()
    print("2. a plan that does not fit is refused before any write")
    try:
        PA.apply_plan(dev, plan([item()], over=True))
        check("it raised", False)
    except PA.ApplyError as e:
        check("it refused on capacity", "fit" in str(e))
    check("still nothing written", not os.path.exists(dst))

    print()
    print("3. a clean copy lands, and is recorded in the ledger")
    rep = PA.apply_plan(dev, plan([item()]))
    placed = os.path.join(dst, "Pulseman.md")
    check("the file is there", os.path.exists(placed))
    check("with the right bytes", open(placed, "rb").read() == b"ROMDATA" * 100)
    check("counted as copied", rep["copied"] == 1 and rep["failed"] == 0)
    led = PP.ledger(dev)
    check("the ledger records it", led.get("pulseman@genesis", {}).get("dest_path")
          == placed)
    check("with a source signature, so a re-plan can say 'unchanged'",
          bool(led["pulseman@genesis"]["src_sig"]))

    print()
    print("4. no .part files survive a successful apply")
    leftovers = [f for dp, _d, fs in os.walk(data) for f in fs if f.endswith(".part")]
    check("none left behind: %s" % leftovers, not leftovers)

    print()
    print("5. an interrupted apply leaves the OLD file, not a truncated one")
    # Simulate a converter that dies mid-write by making the staging step throw AFTER
    # the destination already holds a good file.
    original = open(placed, "rb").read()
    real_convert = PA._convert

    def dying_convert(it, staged):
        p = real_convert(it, staged)
        raise PA.ApplyError("converter died half way")
    PA._convert = dying_convert
    try:
        rep2 = PA.apply_plan(dev, plan([item(action=PP.UPDATE)]))
        check("the item failed", rep2["failed"] == 1)
        check("the destination still holds the ORIGINAL file",
              open(placed, "rb").read() == original)
        check("and no .part was left in place",
              not os.path.exists(placed + ".part"))
    finally:
        PA._convert = real_convert

    print()
    print("6. REMOVAL DELETES ONLY WHAT THE LEDGER SAYS WE PLACED")
    stray = os.path.join(dst, "Someone Elses Game.md")
    open(stray, "wb").write(b"NOT OURS")
    # A doctored plan that names both our file and theirs — exactly what a bug
    # elsewhere would produce.
    evil = item(action=PP.REMOVE, dest=[placed, stray])
    rep3 = PA.apply_plan(dev, plan([evil]))
    check("our file was removed", not os.path.exists(placed))
    check("THEIRS WAS NOT", os.path.exists(stray))
    check("and the run said so rather than silently skipping",
          any("no record" in e["error"] for e in rep3["errors"]))
    check("the ledger row is gone", "pulseman@genesis" not in PP.ledger(dev))

    print()
    print("7. a removal for something we never placed is refused, not attempted")
    open(placed, "wb").write(b"back again")
    rep4 = PA.apply_plan(dev, plan([item(action=PP.REMOVE)]))
    check("it did not delete the file", os.path.exists(placed))
    check("it was counted as failed", rep4["failed"] == 1)
    check("with an honest reason",
          any("ledger" in e["error"] for e in rep4["errors"]))
    os.remove(placed)

    print()
    print("8. one bad item does not strand the rest")
    good = item(key="a@genesis", name="A.md")
    bad = item(key="b@genesis", name="B.md", source=["/does/not/exist.md"])
    good2 = item(key="c@genesis", name="C.md")
    rep5 = PA.apply_plan(dev, plan([good, bad, good2]))
    check("two landed", os.path.exists(os.path.join(dst, "A.md"))
          and os.path.exists(os.path.join(dst, "C.md")))
    check("one failed", rep5["failed"] == 1)
    check("and it is named in the report",
          rep5["errors"][0]["entry_key"] == "b@genesis")
    check("the failure did not poison the ledger",
          "b@genesis" not in PP.ledger(dev))

    print()
    print("9. re-applying is resumable — a SKIP does nothing at all")
    before = os.path.getmtime(os.path.join(dst, "A.md"))
    rep6 = PA.apply_plan(dev, plan([item(action=PP.SKIP, key="a@genesis", name="A.md")]))
    check("counted as skipped", rep6["skipped"] == 1)
    check("and the file was not rewritten",
          os.path.getmtime(os.path.join(dst, "A.md")) == before)

    print()
    print("10. a blocked ITEM inside an allowed run is failed, never guessed at")
    rep7 = PA.apply_plan(dev, plan([item(action=PP.BLOCKED, key="d@genesis",
                                         name="D.md", blockers=["chdman missing"])]),
                         allow_blocked=True)
    check("it did not write the file", not os.path.exists(os.path.join(dst, "D.md")))
    check("it was failed with the blocker as the reason",
          rep7["failed"] == 1 and "chdman" in rep7["errors"][0]["error"])

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
