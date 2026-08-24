#!/usr/bin/env python3
"""What publishing WOULD do — computed, never performed.

Publishing writes to someone else's disk. So the plan is a first-class artifact rather
than a side effect of applying: it is computed by reading, it is what the UI shows, and
Apply is a separate, explicit act that consumes it. Nothing in this module writes to a
target. That is a property the tests assert, not a convention.

A PLAN IS A PURE FUNCTION of (catalog, intent, profile, ledger, observed target state).
That is what makes it testable with no device present — and it is why the observed part
is optional and reported: a plan built without looking at the target is still useful and
must say so, rather than quietly presenting stale ledger data as fact.

THE LEDGER IS WHAT MAKES REMOVAL SAFE. publish_state records what ludodex placed, so:

  * the diff is against REALITY, not against last intent — devices are not ours, people
    delete things, cards corrupt, other tools write to the same folders;
  * removal only ever touches files WE placed. A file on the target with no ledger row
    belongs to the user, and Publish must never delete it. Un-publishing an entry
    removes its ledger paths and nothing else.

The second one is the difference between a useful feature and one that eats somebody's
hand-curated ROM folder, so it is enforced here and tested directly.
"""
import json
import os
import shutil
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
sys.path.insert(0, DIR)
import devices               # noqa: E402
import devicesync            # noqa: E402
import publish               # noqa: E402
import publish_profiles      # noqa: E402

DB = os.path.join(DATA, "connections.sqlite")

# Actions, in the order a reviewer wants to read them.
COPY, CONVERT, UPDATE, REMOVE, SKIP, BLOCKED = (
    "copy", "convert", "update", "remove", "skip", "blocked")

# Which external tool each conversion needs. A conversion whose tool is absent is a
# BLOCKER on that item, never a silent downgrade to copying the wrong format.
TOOLS = {"chd": "chdman", "rvz": "dolphin-tool", "unzip": "7z"}


def _con():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS publish_state(
        device_id  INTEGER,
        entry_key  TEXT,
        dest_path  TEXT,      -- the entry file we wrote
        extra_paths TEXT,     -- json: member tracks, m3u, media, metadata
        src_sig    TEXT,      -- source size+mtime, to notice a changed source
        dest_sig   TEXT,      -- what we wrote, to notice someone changing it
        converted  TEXT,      -- 'cue->chd', so a re-plan does not redo it
        meta_rev   TEXT,
        published_at REAL,
        PRIMARY KEY(device_id, entry_key))""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_ps_dev ON publish_state(device_id)")
    con.commit()
    return con


# --- ledger ------------------------------------------------------------------ #
def ledger(device_id):
    con = _con()
    rows = {r["entry_key"]: dict(r) for r in con.execute(
        "SELECT * FROM publish_state WHERE device_id=?", (int(device_id),))}
    con.close()
    for r in rows.values():
        try:
            r["extra_paths"] = json.loads(r["extra_paths"] or "[]")
        except ValueError:
            r["extra_paths"] = []
    return rows


def ledger_record(device_id, entry_key, dest_path, extra_paths=None, src_sig=None,
                  dest_sig=None, converted=None, meta_rev=None):
    """Called by Apply (phase 5). Present here because the ledger's shape and the
    planner's reading of it must not drift apart."""
    con = _con()
    con.execute(
        "INSERT INTO publish_state(device_id,entry_key,dest_path,extra_paths,src_sig,"
        "dest_sig,converted,meta_rev,published_at) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(device_id,entry_key) DO UPDATE SET dest_path=excluded.dest_path, "
        "extra_paths=excluded.extra_paths, src_sig=excluded.src_sig, "
        "dest_sig=excluded.dest_sig, converted=excluded.converted, "
        "meta_rev=excluded.meta_rev, published_at=excluded.published_at",
        (int(device_id), entry_key, dest_path, json.dumps(extra_paths or []),
         src_sig, dest_sig, converted, meta_rev, time.time()))
    con.commit()
    con.close()


def ledger_forget(device_id, entry_keys):
    con = _con()
    con.executemany("DELETE FROM publish_state WHERE device_id=? AND entry_key=?",
                    [(int(device_id), k) for k in (entry_keys or [])])
    con.commit()
    con.close()


def _target_is_reachable(device_id, rom_path):
    """True when we can actually look at the target's ROM directory right now."""
    if not rom_path:
        return False
    if _is_local(device_id):
        return os.path.isdir(rom_path)
    try:
        return bool(devices.fs_stat(int(device_id), rom_path))
    except Exception:                                   # noqa: BLE001
        return False                                    # unreachable is not "empty"


def _is_local(device_id):
    try:
        d = devices._device(int(device_id)) if device_id else None
    except Exception:                                   # noqa: BLE001
        return True
    return not d or (d.get("transport") or "local") == "local"


def dest_missing(device_id, rom_path=None):
    """Entry keys the ledger says we placed whose file is NOT on the target now.

    THIS IS THE 'REALITY' HALF OF THE DIFF. The module header promises the plan is
    diffed against the device and not against last intent, because "devices are not
    ours, people delete things". Nothing actually stated a destination, so a ROM the
    user deleted stayed `skip / unchanged` forever while the plan reported
    `observed: true`. An unreachable target returns [] and the caller must treat the
    plan as unobserved: "I could not look" is never "nothing is missing"."""
    if not _target_is_reachable(device_id, rom_path):
        return []
    led = ledger(device_id)
    local = _is_local(device_id)
    remote_have = None
    if not local:
        try:                                            # one listing, not one stat per file
            remote_have = set(devices.list_names(int(device_id), rom_path) or [])
        except Exception:                               # noqa: BLE001
            return []
    gone = []
    for ek, row in sorted(led.items()):
        paths = [row["dest_path"]] + list(row.get("extra_paths") or [])
        paths = [x for x in paths if x]
        if not paths:
            continue
        if local:
            here = all(os.path.exists(x) for x in paths)
        else:
            here = all(os.path.basename(x) in remote_have for x in paths)
        if not here:
            gone.append(ek)
    return gone


def src_signature(paths):
    """A cheap, stable signature for a source set: size+mtime per file.

    Not a hash. Hashing is the right answer for correctness and the wrong one for a
    573,000-file library; size+mtime catches every realistic change to a ROM and costs
    a stat. A file edited in place without changing either is not a case worth paying
    for here."""
    parts = []
    for p in sorted(paths or []):
        try:
            st = os.stat(p)
            parts.append("%s:%d:%d" % (os.path.basename(p), st.st_size, int(st.st_mtime)))
        except OSError:
            parts.append("%s:missing" % os.path.basename(p))
    return "|".join(parts)


# --- planning ---------------------------------------------------------------- #
def _tool_available(tool):
    if tool in (None, "", "copy"):
        return True
    binary = TOOLS.get(tool)
    return bool(binary and shutil.which(binary))


def plan(device_id, profile_id=None, source_mgr_id=None, rom_path=None,
         media_path=None, observe=True, media_repo=None, limit=None):
    """Compute what publishing to this device would do. Reads only.

    `observe` controls whether the target is inspected. When it is False — or when the
    target simply cannot be reached — the plan is still produced, from the ledger, and
    says so in `observed`. A caller that treats an unobserved plan as ground truth will
    happily "copy" files that are already there; the flag exists so it cannot.
    """
    prof = publish_profiles.get(profile_id or publish_profiles.DEFAULT_PROFILE)
    led = ledger(device_id)
    # OBSERVED MEANS OBSERVED. This flag used to be `bool(observe and rom_path)` with
    # nothing behind it: no destination was ever stated, so the action came from the
    # ledger alone and a file the user deleted on the device read as "unchanged".
    reachable = bool(observe) and _target_is_reachable(device_id, rom_path)
    absent = set(dest_missing(device_id, rom_path)) if reachable else set()
    want = set(publish.intent_keys(device_id))
    rows = {r["entry_key"]: r for r in publish.entry_rows(sorted(want))}

    items = []
    blockers = []
    missing_tools = set()

    for ek in sorted(want):
        meta = rows.get(ek)
        if not meta:
            # Intent for something the catalog no longer has. Report it; do not guess.
            items.append(_item(ek, None, None, BLOCKED,
                               "entry is not in the catalog", blockers=["unknown entry"]))
            continue
        system = publish_profiles.system_for(prof, meta["platform"])
        hits = []
        if source_mgr_id is not None:
            hits = devicesync.resolve_roms(source_mgr_id, meta["platform"],
                                           meta["title"])
        if not hits:
            items.append(_item(ek, meta, system, BLOCKED, "no ROM file resolved",
                               blockers=["no source file"]))
            continue

        discs = devicesync.pick_rom_files(hits, system)
        for d in discs:
            src_files = d["files"]
            ext = os.path.splitext(d["entry"])[1].lstrip(".")
            target_ext, tool = publish_profiles.convert_plan(prof, system, ext)
            dest_name = "%s.%s" % (d["basename"], target_ext or ext)
            dest = os.path.join(rom_path or "", system, dest_name)

            sig = src_signature(src_files)
            prev = led.get(ek)
            item_blockers = []
            if not _tool_available(tool):
                item_blockers.append("%s not available" % TOOLS.get(tool, tool))
                missing_tools.add(TOOLS.get(tool, tool))

            if prev is None:
                action = CONVERT if tool != "copy" else COPY
                reason = "not present on target"
            elif ek in absent:
                action = CONVERT if tool != "copy" else COPY
                reason = "we published it, but it is not on the target now"
            elif prev.get("src_sig") != sig:
                action, reason = UPDATE, "source changed since it was published"
            else:
                action, reason = SKIP, "unchanged"

            if item_blockers:
                action = BLOCKED

            items.append(_item(ek, meta, system, action, reason,
                               source=src_files, dest=[dest],
                               convert=({"from": ext, "to": target_ext, "tool": tool}
                                        if tool != "copy" else None),
                               bytes_in=_size(src_files),
                               blockers=item_blockers, disc=d.get("disc")))
        if limit and len(items) >= limit:
            break

    # --- removals: ledgered entries no longer wanted --------------------------- #
    for ek, row in sorted(led.items()):
        if ek in want:
            continue
        paths = [row["dest_path"]] + list(row.get("extra_paths") or [])
        if ek in absent:
            # already gone from the device; the ledger row is what needs clearing
            items.append(_item(ek, rows.get(ek), None, SKIP,
                               "no longer selected, and already absent from the target",
                               dest=[p for p in paths if p]))
            continue
        items.append(_item(ek, rows.get(ek), None, REMOVE,
                           "no longer selected for this device",
                           dest=[p for p in paths if p]))

    totals = _totals(items)
    if missing_tools:
        blockers.append("conversion tools not available here: %s"
                        % ", ".join(sorted(missing_tools)))
    out = {"device_id": int(device_id), "profile": prof["id"],
           "observed": reachable,
           "items": items, "totals": totals, "blockers": blockers,
           "dry_run": True}
    # MEASURE THE CARD, DO NOT ASK THE BROWSER FOR ITS SIZE. check_capacity used to run
    # only when the API handler was handed a `free_bytes` field, and `over_capacity` was
    # absent otherwise, which is falsy — so the "BLOCKED before anything is written"
    # promise was a fail-open any caller could step past by omitting one key. When the
    # target is reachable the planner can see the free space for itself.
    if reachable and _is_local(device_id):
        try:
            check_capacity(out, shutil.disk_usage(rom_path).free)
        except OSError:
            pass
    return out


def _item(entry_key, meta, system, action, reason, source=None, dest=None,
          convert=None, bytes_in=0, blockers=None, disc=None):
    return {"entry_key": entry_key,
            "title": (meta or {}).get("title"),
            "platform": (meta or {}).get("platform"),
            "system": system, "action": action, "reason": reason,
            "source": source or [], "dest": dest or [],
            "convert": convert, "bytes_in": bytes_in, "disc": disc,
            "blockers": blockers or []}


def _size(paths):
    n = 0
    for p in paths or []:
        try:
            n += os.path.getsize(p)
        except OSError:
            pass
    return n


def _totals(items):
    t = {a: 0 for a in (COPY, CONVERT, UPDATE, REMOVE, SKIP, BLOCKED)}
    written = 0
    for it in items:
        t[it["action"]] = t.get(it["action"], 0) + 1
        if it["action"] in (COPY, CONVERT, UPDATE):
            written += it.get("bytes_in") or 0
    t["bytes_to_write"] = written
    t["items"] = len(items)
    return t


def check_capacity(plan_result, free_bytes):
    """Fold a capacity verdict into a plan. A plan larger than the target is BLOCKED
    before anything is written, rather than failing partway through and leaving a device
    with half a library and no free space."""
    need = plan_result["totals"]["bytes_to_write"]
    plan_result["free_bytes"] = free_bytes
    if free_bytes is not None and need > free_bytes:
        plan_result["blockers"].append(
            "plan needs %.1f GB but the target has %.1f GB free"
            % (need / 1e9, free_bytes / 1e9))
        plan_result["over_capacity"] = True
    else:
        plan_result["over_capacity"] = False
    return plan_result


def unmanaged(device_id, observed_paths):
    """Files seen on the target that ludodex did not place.

    Reported so the user can see them; NEVER proposed for removal. A path with no
    ledger row is the user's, and a publisher that cannot tell the difference is a
    publisher that eventually deletes someone's collection."""
    led = ledger(device_id)
    ours = set()
    for r in led.values():
        ours.add(r["dest_path"])
        ours.update(r.get("extra_paths") or [])
    return sorted(p for p in (observed_paths or []) if p not in ours)


def main(argv):
    dev = int(argv[argv.index("--device") + 1]) if "--device" in argv else None
    if dev is None:
        print("usage: publish_plan.py --device <id> [--profile esde] [--rom-path P]")
        return 2
    prof = argv[argv.index("--profile") + 1] if "--profile" in argv else None
    rp = argv[argv.index("--rom-path") + 1] if "--rom-path" in argv else None
    mgr = int(argv[argv.index("--source-mgr") + 1]) if "--source-mgr" in argv else None
    res = plan(dev, profile_id=prof, source_mgr_id=mgr, rom_path=rp)
    print(json.dumps({k: v for k, v in res.items() if k != "items"}, indent=2))
    for it in res["items"][:40]:
        print("  %-8s %-28s %-10s %s" % (it["action"], (it["title"] or it["entry_key"])[:28],
                                         it["platform"] or "", it["reason"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
