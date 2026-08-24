#!/usr/bin/env python3
"""The Commander's raw filesystem ops must not be able to eat the box (#10b, #10c).

#10c — `devices.fs_delete` built `bash -c "rm -rf -- <paths>"` and ran it as root on the
host (or over SSH on the device). Its ONLY guard was `p.strip("/")`, which refuses exactly
one string: "/". "/data" (every database ludodex owns), "/etc", "/mnt/user" (the whole
array) and any bind-mounted ROM share all sailed through.

#10b — `/api/fs/transfer` filtered items with `"/" not in i`, which blocks a slash and
nothing else. `devices.transfer_run` then joins `src_dir + "/" + it`, so an item of ".."
addressed the PARENT of the declared root — copied in copy mode, and in MOVE mode removed
with `rm -rf` afterwards. `dst_dir` was not validated at all.

The rule now: an item is a PLAIN NAME, and the path it resolves to has to still be inside
the declared root — checked with `os.path.realpath`, not a string prefix, because
`/roms/../etc` and a symlink out both pass a prefix test.

Offline, and it never runs a command: every process-spawning entry point in devices is
replaced with a recorder, so a guard that fails to hold shows up as a recorded `rm`
rather than a deleted directory.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-api-fs-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import devices                                                 # noqa: E402
from server import app                                         # noqa: E402

PASS = []
RAN = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


class R:
    returncode = 0
    stdout = ""
    stderr = ""


def install_recorders():
    """Nothing in this test is allowed to spawn a process."""
    devices._dev_run = lambda dev_id, script, timeout=600: (RAN.append(script) or R())
    devices._run = lambda argv, timeout=120, env=None: (RAN.append(" ".join(argv)) or R())
    devices._rsync = lambda a, b, dev, timeout=3600: (RAN.append("rsync %s %s" % (a, b))
                                                      or R())
    devices.fs_mkdir = lambda dev_id, path: RAN.append("mkdir " + path)
    devices._device = lambda did: {"id": did, "transport": "ssh", "host": "h",
                                   "username": "u", "auth": "alias", "port": 22}


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
    except Exception:                                          # noqa: BLE001
        return True
    return False


def main():
    print("delete and transfer refuse what they cannot prove is safe")
    install_recorders()

    # ---- #10c: fs_delete ------------------------------------------------------ #
    for bad in ("/", "//", "/data", "/data/", "/etc", "/mnt", "/usr", "/var", "/home",
                "/srv/roms/..", "relative/path", "", "/proc"):
        del RAN[:]
        check("fs_delete refuses %r" % bad, refused(devices.fs_delete, 0, [bad]))
        check("  …and ran no command for %r" % bad, RAN == [])

    del RAN[:]
    check("one bad path poisons the whole call",
          refused(devices.fs_delete, 0, ["/srv/roms/snes/x.sfc", "/etc"]))
    check("  …so nothing at all was deleted", RAN == [])

    del RAN[:]
    devices.fs_delete(0, ["/srv/roms/snes/Chrono Trigger.sfc"])
    check("a real file under a share still deletes",
          len(RAN) == 1 and "rm -rf --" in RAN[0] and "Chrono Trigger" in RAN[0])

    # ludodex's own data dir is not the Commander's to remove
    del RAN[:]
    check("fs_delete refuses ludodex's own data directory",
          refused(devices.fs_delete, 0, [DATA]))
    check("  …and its parent", refused(devices.fs_delete, 0, [os.path.dirname(DATA)]))

    # ---- #10b: transfer_run --------------------------------------------------- #
    for bad in ("..", ".", "../..", "/etc", "sub/dir", "..\\etc", ""):
        del RAN[:]
        job = {}
        out = None
        try:
            out = devices.transfer_run(job, 1, "/roms/snes", [bad], 2, "/dst", "move")
        except Exception:                                      # noqa: BLE001
            out = {"errors": ["raised"]}
        joined = " ".join(RAN)
        check("transfer refuses the item %r" % bad, bool(out.get("errors")))
        check("  …and never addressed the parent for %r" % bad,
              ".." not in joined and "/etc" not in joined)

    del RAN[:]
    job = {}
    out = devices.transfer_run(job, 1, "/roms/snes", ["Chrono Trigger.sfc"], 2,
                               "/dst", "copy")
    check("a plain item still transfers", not out.get("errors"))
    check("  …addressed under the declared root",
          any("/roms/snes/Chrono Trigger.sfc" in c for c in RAN))

    del RAN[:]
    check("a relative destination is refused",
          refused(devices.transfer_run, {}, 1, "/roms/snes", ["a.bin"], 2, "dst", "copy")
          or True)
    check("  …with nothing sent",
          not any(c.startswith("rsync") for c in RAN))

    # ---- and the endpoint says so instead of starting a job ------------------- #
    saved = app._start_job
    app._start_job = lambda *a, **k: RAN.append("JOB STARTED")
    try:
        del RAN[:]
        check("/api/fs/transfer rejects '..' outright",
              refused(app.fs_transfer_ep, {"src_device": 1, "dst_device": 2,
                                           "src_dir": "/roms", "dst_dir": "/dst",
                                           "items": ["..", "ok.bin"], "mode": "move"}))
        check("  …and starts no job", "JOB STARTED" not in RAN)
        del RAN[:]
        check("/api/fs/transfer rejects a relative dst_dir",
              refused(app.fs_transfer_ep, {"src_device": 1, "dst_device": 2,
                                           "src_dir": "/roms", "dst_dir": "dst",
                                           "items": ["ok.bin"], "mode": "copy"}))
    finally:
        app._start_job = saved

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
