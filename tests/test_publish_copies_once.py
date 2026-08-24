#!/usr/bin/env python3
"""Publishing a ROM must copy it once, and must know if it will fit.

TWO COPIES THROUGH THE WRONG VOLUME. `apply_plan` built every file in
`tempfile.mkdtemp(dir=DATA)`, so a plain copy did `shutil.copy2(source, staging)` into
the data volume and then `_place_local` did `shutil.move(staging, dest + '.part')`,
which is a SECOND full copy whenever the target is on another filesystem — and it always
is, or there would be nothing to publish. docs/DOCKER.md tells users `/data` is small and
on the SSD cache, so a 40 GB disc image landed there first. Building beside the
destination makes the swap a rename instead.

CAPACITY WAS CHECKED ONLY IF THE BROWSER VOLUNTEERED THE NUMBER. `check_capacity` runs
from the API handler and only when the client sends `free_bytes`, and `over_capacity`
defaults to absent, which is falsy — so the "BLOCKED before anything is written" promise
was a fail-open: any caller that omitted the field filled the card. The planner can see
a local target itself, so it measures rather than asking.

Offline. No network, local device only.
"""
import os
import shutil
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-publish-copies-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import publish_apply                                           # noqa: E402
import publish_plan                                            # noqa: E402

PASS = []
TARGET = os.path.join(DATA, "device-roms")
SRC = os.path.join(DATA, "source")


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    print("publishing copies the file once and measures the card")
    os.makedirs(TARGET, exist_ok=True)
    os.makedirs(SRC, exist_ok=True)
    rom = os.path.join(SRC, "Chrono Trigger.sfc")
    with open(rom, "wb") as f:
        f.write(b"\x00" * 4096)

    dest = os.path.join(TARGET, "snes", "Chrono Trigger.sfc")
    plan = {"device_id": 0, "items": [{
        "entry_key": "chrono trigger@snes", "action": publish_plan.COPY,
        "source": [rom], "dest": [dest], "system": "snes", "convert": None,
        "bytes_in": 4096, "blockers": []}]}

    copies = []
    real_copy2 = shutil.copy2
    shutil.copy2 = lambda a, b, **k: (copies.append((a, b)), real_copy2(a, b, **k))[1]
    try:
        report = publish_apply.apply_plan(0, plan)
    finally:
        shutil.copy2 = real_copy2

    check("the file is published", os.path.exists(dest) and report["copied"] == 1)
    check("the ROM bytes are copied exactly once", len(copies) == 1)
    check("no staging directory is left beside the destination",
          [f for f in os.listdir(os.path.dirname(dest))] == ["Chrono Trigger.sfc"])

    # The copy COUNT above cannot prove the fix here: this test's data dir and its
    # fake device are on one filesystem, so shutil.move is a rename either way. The
    # property that makes a second copy impossible is WHERE the file is built, so
    # assert that directly.
    staged_dir = publish_apply._staging_for(dest, local=True, fallback=DATA)
    check("a local target is built beside its destination, not in the data volume",
          os.stat(staged_dir).st_dev == os.stat(os.path.dirname(dest)).st_dev)
    check("and the data volume is only the fallback for a remote target",
          publish_apply._staging_for(dest, local=False, fallback=DATA)
          .startswith(DATA))
    shutil.rmtree(staged_dir, ignore_errors=True)

    # ---- capacity is measured, not asked for --------------------------------- #
    p = publish_plan.plan(0, rom_path=TARGET, observe=True)
    check("a plan against a reachable local target reports the free space",
          p.get("free_bytes") is not None)
    check("and says whether it fits", "over_capacity" in p)

    p["items"] = [{"action": publish_plan.COPY, "bytes_in": 1 << 60,
                   "entry_key": "huge@snes", "source": [rom], "dest": [dest]}]
    p["totals"] = publish_plan._totals(p["items"])
    publish_plan.check_capacity(p, p["free_bytes"])
    check("a plan that cannot fit is marked over capacity", p["over_capacity"] is True)
    raised = None
    try:
        publish_apply.apply_plan(0, p)
    except Exception as e:                                     # noqa: BLE001
        raised = e
    check("and applying it is refused", raised is not None)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
