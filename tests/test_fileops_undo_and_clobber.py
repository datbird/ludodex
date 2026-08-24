#!/usr/bin/env python3
"""File operations move real ROM files, so "reversible" has to be true.

Two defects made it false, and they compound: the first marks a step successful when it
did nothing, and the second then reverses that step by deleting a file the user already
had.

  * `cp -an` AND `mv -n` EXIT 0 WHEN THEY SKIP. -n means no-clobber, and a destination
    that already exists is not an error to coreutils. The step was recorded `ok` with
    nothing copied, and undo inverts a copy as `rm -rf <dst>` — so undoing a run that
    quietly did nothing DELETES THE FILE THAT WAS ALREADY THERE. Rendering two sources
    to one destination is routine with a `{system}/{filename}` profile over a
    folder-per-game tree, and nothing in planning checks for it.
  * UNDO RAN EVERY INVERSE IN ONE `bash -c` ARGUMENT. Linux caps a single argv string at
    MAX_ARG_STRLEN (128 KiB). Each inverse line carries absolute ROM paths, so a run of
    a few hundred moves exceeds it and the whole undo dies with E2BIG before anything
    executes — while the run was still marked `undone`, unconditionally, even when every
    step printed ERR. The forward runner batches by op COUNT (200), which is the same
    arithmetic and the same failure.

A collision is a thing to report, not to skip: the plan is wrong and the user needs to
see it. Undo is only `undone` when every step actually reverted.

Offline. No network. Operates entirely inside a temp root.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-fileops-undo-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import fileops                                                 # noqa: E402

PASS = []
ROOT = os.path.join(DATA, "roms")


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def write(rel, text):
    p = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def read(rel):
    p = os.path.join(ROOT, rel)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else None


def run(ops):
    rid = fileops.create_runbook(0, ROOT, {"name": "test"}, ops)
    return rid, fileops.execute_runbook(rid)


def main():
    print("file operations are reversible, or they say they are not")
    os.makedirs(ROOT, exist_ok=True)

    # ---- a copy onto an existing file ---------------------------------------- #
    write("src/Sonic.png", "the one being copied")
    write("out/Sonic.png", "the one already there")
    rid, res = run([{"op": "copy", "src": "src/Sonic.png", "dst": "out/Sonic.png"}])
    check("a copy onto an existing file is not reported as done", res["ok"] == 0)
    check("and the file that was already there is untouched",
          read("out/Sonic.png") == "the one already there")

    fileops.undo(rid)
    check("undoing that run does not delete the user's file",
          read("out/Sonic.png") == "the one already there")

    # ---- a move onto an existing file ---------------------------------------- #
    write("src/Mario.png", "the one being moved")
    write("out/Mario.png", "the one already there")
    rid, res = run([{"op": "move", "src": "src/Mario.png", "dst": "out/Mario.png"}])
    check("a move onto an existing file is not reported as done", res["ok"] == 0)
    check("the destination is untouched", read("out/Mario.png") == "the one already there")
    check("and the source is still where it was",
          read("src/Mario.png") == "the one being moved")

    fileops.undo(rid)
    check("undoing that run leaves the destination alone",
          read("out/Mario.png") == "the one already there")

    # ---- a real run still works, and still reverses --------------------------- #
    write("src/Celeste.png", "art")
    rid, res = run([{"op": "move", "src": "src/Celeste.png", "dst": "done/Celeste.png"}])
    check("an ordinary move succeeds", res["ok"] == 1)
    check("the file moved", read("done/Celeste.png") == "art" and read("src/Celeste.png") is None)
    u = fileops.undo(rid)
    check("undo puts it back", read("src/Celeste.png") == "art")
    check("and reports the run as undone", u["status"] == "undone")

    # ---- more inverse steps than fit in one argv ------------------------------ #
    # Long, realistic paths: a few hundred of these clear MAX_ARG_STRLEN in one string.
    deep = "Sony PlayStation/Final Fantasy VIII (USA) (Disc 1) (Rev 1) (Collectors Edition)"
    ops = []
    for i in range(700):
        rel = "%s/track %03d - a reasonably long ROM file name.chd" % (deep, i)
        write("stage/" + rel, "rom %d" % i)
        ops.append({"op": "move", "src": "stage/" + rel, "dst": "library/" + rel})
    rid, res = run(ops)
    check("a 700-step run completes", res["ok"] == 700 and res["failed"] == 0)
    check("the files are where the plan said", read("library/%s/track 000 - a reasonably "
                                                    "long ROM file name.chd" % deep) == "rom 0")
    u = fileops.undo(rid)
    check("undo of a 700-step run reverts every step", u["reverted"] == u["of"])
    check("it is reported as undone", u["status"] == "undone")
    check("and every file is back where it started",
          read("stage/%s/track 699 - a reasonably long ROM file name.chd" % deep) == "rom 699")

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
