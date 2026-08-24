#!/usr/bin/env python3
"""Apply a publish plan — the only code here that writes to a target.

EVERY SAFETY PROPERTY IN THIS FILE EXISTS BECAUSE THE ALTERNATIVE IS SOMEONE'S LIBRARY.

  * A PLAN WITH BLOCKERS IS REFUSED, whole. Applying "the parts that work" produces a
    device holding half a curated set with no record of which half, which is worse than
    doing nothing and saying why.
  * STAGE, VERIFY, THEN SWAP. Every file lands as `.part` and is renamed into place only
    once it is complete. An interrupted apply leaves the OLD file or the NEW one, never
    a truncated ROM that a frontend indexes happily and fails to launch.
  * REMOVAL ONLY EVER TOUCHES LEDGERED PATHS, and re-checks the ledger at the moment of
    deletion rather than trusting a plan computed minutes ago. A path ludodex did not
    place is the user's, permanently.
  * THE LEDGER IS WRITTEN PER ITEM, not at the end. That is what makes an interrupted
    run resumable: the next plan sees what actually landed, not what was intended.

CONVERSION RUNS HERE, in the container, because that is where the tools now are
(chdman from mame-tools, dolphin-tool from dolphin-emu). The cost is that a remote
target pulls its source over the wire once and pushes the converted file back; the
alternative — converting on the device — needs the tools present on every device, which
is exactly the assumption that makes a feature stop working on someone else's hardware.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
sys.path.insert(0, DIR)
import devices              # noqa: E402
import publish_plan         # noqa: E402

# chdman needs to be told what KIND of disc it is holding. A CD image and a DVD image
# are different subcommands, and using the wrong one fails rather than producing a
# subtly bad file — which is the good outcome, but only if we pick correctly.
CHD_DVD_SYSTEMS = {"ps2", "gc", "wii"}


class ApplyError(Exception):
    pass


def _run(cmd, timeout=7200):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise ApplyError("%s failed: %s" % (cmd[0],
                                            (p.stderr or p.stdout or "")[-300:]))
    return p


def _convert(item, staged_dir):
    """Produce the destination file in `staged_dir`. Returns its path.

    The source is never modified — conversion reads it and writes somewhere else, so a
    failed convert costs time and nothing else."""
    conv = item.get("convert") or {}
    tool = conv.get("tool")
    entry = (item.get("source") or [None])[0]
    if not entry:
        raise ApplyError("no source file")
    out = os.path.join(staged_dir, os.path.basename(item["dest"][0]))

    if tool in (None, "copy"):
        shutil.copy2(entry, out)
        return out
    if tool == "chd":
        mode = ("createdvd" if (item.get("system") or "") in CHD_DVD_SYSTEMS
                else "createcd")
        _run(["chdman", mode, "-i", entry, "-o", out])
        return out
    if tool == "rvz":
        _run(["dolphin-tool", "convert", "-f", "rvz", "-i", entry, "-o", out])
        return out
    if tool == "unzip":
        # The emulator cannot read an archive, so unpack and ship what was inside.
        with tempfile.TemporaryDirectory(dir=staged_dir) as tmp:
            _run(["7z", "x", "-y", "-o" + tmp, entry])
            inner = [os.path.join(dp, f) for dp, _d, fs in os.walk(tmp) for f in fs]
            if not inner:
                raise ApplyError("archive was empty")
            biggest = max(inner, key=os.path.getsize)
            out = os.path.join(staged_dir, os.path.basename(biggest))
            shutil.move(biggest, out)
        return out
    raise ApplyError("unknown conversion tool %r" % tool)


def _staging_for(dest, local, fallback):
    """Where to BUILD the file before the atomic swap.

    Building in the data volume meant a plain copy ran twice: once from the source into
    /data, and again when shutil.move crossed the filesystem boundary to the target.
    docs/DOCKER.md tells users /data is small and on the SSD cache, so a 40 GB disc
    image landed there first. Beside the destination, the swap is a rename.

    A REMOTE target has no such option: the file has to exist here before it can be
    pushed, so that one keeps the fallback."""
    if not local:
        return fallback
    d = os.path.join(os.path.dirname(dest) or ".", ".ludodex-staging")
    os.makedirs(d, exist_ok=True)
    return d


def _place_local(staged, dest):
    """Stage-then-swap on a local path. os.replace is atomic within a filesystem, so
    the destination is either the old file or the new one at every instant."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    shutil.move(staged, part)
    os.replace(part, dest)


def _place_remote(dev_id, staged, dest):
    """Push to a device, then swap there. The same discipline, one hop further out."""
    dest_dir = os.path.dirname(dest)
    part_name = os.path.basename(dest) + ".part"
    tmp_local = os.path.join(os.path.dirname(staged), part_name)
    shutil.move(staged, tmp_local)
    devices.push_file(dev_id, tmp_local, dest_dir)
    devices._dev_run(dev_id, "mv -f -- %s %s" % (
        _q(os.path.join(dest_dir, part_name)), _q(dest)))


def _q(p):
    import shlex
    return shlex.quote(p)


def _is_local(dev_id):
    d = devices._device(dev_id)
    return not d or (d.get("transport") or "local") == "local"


def apply_plan(device_id, plan, progress=None, allow_blocked=False, limit=None):
    """Execute a plan. Returns a report; raises only on refusals, not on item failures.

    An item that fails is recorded and the run CONTINUES, because one unreadable source
    should not strand the other four hundred games. A refusal — blockers, over
    capacity — stops before anything is written at all."""
    if not allow_blocked and plan.get("blockers"):
        raise ApplyError("plan has blockers; refusing to apply: %s"
                         % "; ".join(plan["blockers"]))
    if plan.get("over_capacity"):
        raise ApplyError("plan does not fit on the target; refusing to apply")

    local = _is_local(device_id)
    report = {"copied": 0, "converted": 0, "updated": 0, "removed": 0,
              "skipped": 0, "failed": 0, "errors": [], "started": time.time()}
    items = plan.get("items") or []
    if limit:
        items = items[:limit]

    staging = tempfile.mkdtemp(prefix="ludodex-publish-", dir=DATA)
    _stages_used = set()
    try:
        for i, item in enumerate(items):
            act = item.get("action")
            if progress:
                progress(i, len(items), item)
            if act == publish_plan.SKIP:
                report["skipped"] += 1
                continue
            if act == publish_plan.BLOCKED:
                report["failed"] += 1
                report["errors"].append({"entry_key": item["entry_key"],
                                         "error": "; ".join(item.get("blockers") or
                                                            ["blocked"])})
                continue
            if act == publish_plan.REMOVE:
                _remove(device_id, item, report, local)
                continue

            try:
                dest = item["dest"][0]
                item_stage = _staging_for(dest, local, staging)
                _stages_used.add(item_stage)
                staged = _convert(item, item_stage)
                if local:
                    _place_local(staged, dest)
                else:
                    _place_remote(device_id, staged, dest)
                publish_plan.ledger_record(
                    device_id, item["entry_key"], dest_path=dest,
                    extra_paths=[], src_sig=publish_plan.src_signature(item["source"]),
                    converted=(("%s->%s" % (item["convert"]["from"],
                                            item["convert"]["to"]))
                               if item.get("convert") else None))
                key = {"copy": "copied", "convert": "converted",
                       "update": "updated"}.get(act, "copied")
                report[key] += 1
            except Exception as e:                          # noqa: BLE001
                report["failed"] += 1
                report["errors"].append({"entry_key": item["entry_key"],
                                         "error": str(e)[:300]})
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        for _d in _stages_used:
            if _d != staging:
                shutil.rmtree(_d, ignore_errors=True)
    report["elapsed"] = round(time.time() - report["started"], 1)
    return report


def _remove(device_id, item, report, local):
    """Delete only what the LEDGER says we placed, re-checked now.

    The plan proposing a removal is not sufficient authority: it was computed against a
    ledger read minutes ago, and the only safe question at the moment of deletion is
    'does our record still say we put this here'."""
    led = publish_plan.ledger(device_id)
    row = led.get(item["entry_key"])
    if not row:
        report["errors"].append({"entry_key": item["entry_key"],
                                 "error": "not in the ledger — refusing to delete"})
        report["failed"] += 1
        return
    ours = [row["dest_path"]] + list(row.get("extra_paths") or [])
    ours = [p for p in ours if p]
    # Anything the plan named that our ledger does not is somebody else's file.
    strays = [p for p in (item.get("dest") or []) if p not in ours]
    if strays:
        report["errors"].append({
            "entry_key": item["entry_key"],
            "error": "plan named %d path(s) we have no record of placing; skipped them"
                     % len(strays)})
    stuck = []
    for p in ours:
        try:
            if local:
                if os.path.exists(p):
                    os.remove(p)
            else:
                devices.remove_paths(device_id, [p])
        except Exception as e:                              # noqa: BLE001
            stuck.append(p)
            report["errors"].append({"entry_key": item["entry_key"],
                                     "error": "delete %s: %s" % (p, str(e)[:150])})
    if stuck:
        # KEEP THE RECORD OF WHAT WE PLACED. Forgetting a file we could not delete
        # strands it: by this module's own rule a path with no ledger row "belongs to
        # the user" and Publish must never touch it, so the next run would refuse to
        # try again. The row is the only thing that makes a retry possible.
        report["failed"] += 1
        return
    publish_plan.ledger_forget(device_id, [item["entry_key"]])
    report["removed"] += 1
