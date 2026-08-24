#!/usr/bin/env python3
"""A publish plan claims it looked at the device. It has to actually look.

`plan()` returns `"observed": bool(observe and rom_path)` and its docstring explains why
the flag matters: "A caller that treats an unobserved plan as ground truth will happily
'copy' files that are already there; the flag exists so it cannot." The module header
goes further: "the diff is against REALITY ... people delete things."

Neither was true. Nothing in `plan()` ever stats a destination. The action came only from
the ledger: no row means COPY, a changed source signature means UPDATE, otherwise SKIP.
So a ROM the user deleted on the device stayed `skip / unchanged` forever, because the
ledger still remembered placing it, and `observed: true` told the caller that had been
checked. `ledger_record` never wrote `dest_sig` either, so there was nothing to compare
against even in principle.

The removal side fails the other way. `_remove` collects each delete error into the
report and then calls `ledger_forget` UNCONDITIONALLY, counting `removed += 1`. A file
that could not be deleted is thereby forgotten: the planner no longer has a record of
placing it, and by the module's own rule ("Anything the plan named that our ledger does
not is somebody else's file") it can never be removed again. It is stranded on the
device permanently, and the report says it was removed.

Offline. No network, local device only.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-publish-observe-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import publish_apply                                           # noqa: E402
import publish_plan                                            # noqa: E402

PASS = []
TARGET = os.path.join(DATA, "device-roms")


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def place(rel, text="rom"):
    p = os.path.join(TARGET, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def main():
    print("a publish plan looks at the device it claims to have looked at")
    os.makedirs(TARGET, exist_ok=True)

    # ---- the ledger says it is there; the device disagrees -------------------- #
    dest = place("snes/Chrono Trigger.sfc")
    publish_plan.ledger_record(1, "chrono trigger@snes", dest, src_sig="sig-1")
    led = publish_plan.ledger(1)
    check("the ledger remembers what was published", "chrono trigger@snes" in led)

    gone = publish_plan.dest_missing(1, rom_path=TARGET)
    check("with the file present, nothing is reported missing", gone == [])

    os.remove(dest)                                   # the user deleted it on the device
    gone = publish_plan.dest_missing(1, rom_path=TARGET)
    check("a ledgered file deleted on the device is seen as missing",
          gone == ["chrono trigger@snes"])

    # ---- observed must mean observed ----------------------------------------- #
    p = publish_plan.plan(1, rom_path=TARGET, observe=True)
    check("a plan that inspected the target says so", p["observed"] is True)
    p = publish_plan.plan(1, rom_path=TARGET, observe=False)
    check("a plan that did not inspect the target does not claim it did",
          p["observed"] is False)
    p = publish_plan.plan(1, rom_path=os.path.join(DATA, "not-mounted"), observe=True)
    check("nor does one whose target could not be reached", p["observed"] is False)

    # ---- a failed delete is not a removal ------------------------------------ #
    kept = place("snes/Super Metroid.sfc")
    publish_plan.ledger_record(2, "super metroid@snes", kept, src_sig="sig-2")
    report = {"removed": 0, "failed": 0, "errors": []}
    item = {"entry_key": "super metroid@snes", "dest": [kept]}

    real_remove = os.remove

    def refuse(path):
        if path == kept:
            raise OSError(13, "Permission denied")
        return real_remove(path)

    os.remove = refuse
    try:
        publish_apply._remove(2, item, report, local=True)
    finally:
        os.remove = real_remove

    check("a delete that failed is not counted as a removal", report["removed"] == 0)
    check("it is counted as a failure", report["failed"] == 1)
    check("and the error is reported", len(report["errors"]) == 1)
    check("the file is still on the device", os.path.exists(kept))
    check("and the ledger still remembers placing it, so it can be removed later",
          "super metroid@snes" in publish_plan.ledger(2))

    # ---- a delete that worked still forgets ---------------------------------- #
    report = {"removed": 0, "failed": 0, "errors": []}
    publish_apply._remove(2, item, report, local=True)
    check("a successful delete removes the file", not os.path.exists(kept))
    check("counts as a removal", report["removed"] == 1)
    check("and clears the ledger row",
          "super metroid@snes" not in publish_plan.ledger(2))

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
