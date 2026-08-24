#!/usr/bin/env python3
"""scripts/ssmirror-loop.sh is the only thing that ever re-runs the mirror, so its two
decisions are the whole job: when may the targeted fetch stop being retried, and when
may the loop stop looping.

Both were wrong in the same direction — they took the ABSENCE of bad news as good news:

  * The completion marker was written whenever the output did not contain "skipped". A
    day on which all 739 ids FAILED says nothing about being skipped, so the marker was
    written, and the one job the script exists for never ran again. Its own header says
    "a retry that only happens once is not a retry".
  * `break` on the first "walk complete", then `sleep infinity`. New ids are appended
    above the ceiling continuously, so after exhaustion neither the walk nor the
    targeted fetch ever ran again until someone restarted the container.

The script is driven here with a stub `python3` on PATH: no network, no ludodex import,
and the loop bounded by SSMIRROR_MAX_CYCLES so it can be observed and still terminate.
"""
import os
import subprocess
import sys
import tempfile

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


STUB = r"""#!/bin/sh
# A stand-in for python3. Records every call and answers from a scripted list of
# outcomes, one per cycle, so the loop's decisions can be observed.
echo "$@" >> "$CALLS"
case "$*" in
  *--ids*)
     n=$(grep -c -- '--ids' "$CALLS")
     if [ "$n" = "1" ]; then
       echo 'ss_mirror: {"stale_examined": 739, "refreshed": 0, "failed": 739, "complete": false}'
     else
       echo 'ss_mirror: {"stale_examined": 739, "refreshed": 739, "failed": 0, "complete": true}'
     fi
     ;;
  *--walk*) echo 'ss_mirror: {"requests": 60, "stopped": "exhausted"}' ;;
  *-c*)     cat "$DONEFLAG" ;;
esac
exit 0
"""


def run(script, tmp, cycles, done="1"):
    calls = os.path.join(tmp, "calls.txt")
    open(calls, "w").close()
    open(os.path.join(tmp, "done"), "w").write(done + "\n")
    env = dict(os.environ)
    env.update({
        "PATH": tmp + os.pathsep + env["PATH"],
        "CALLS": calls, "DONEFLAG": os.path.join(tmp, "done"),
        "SSMIRROR_IDS": os.path.join(tmp, "ids.json"),
        "SSMIRROR_MARK": os.path.join(tmp, "ids.done"),
        "SSMIRROR_LOG": os.path.join(tmp, "loop.log"),
        "SSMIRROR_APP": tmp,
        "SSMIRROR_SLEEP": "0", "SSMIRROR_DONE_SLEEP": "0",
        "SSMIRROR_MAX_CYCLES": str(cycles),
    })
    p = subprocess.run(["/bin/sh", script], env=env, timeout=60,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p, open(calls).read().splitlines()


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_support
    test_support.isolate("ludodex-prov-ssloop-")      # nothing here touches real data
    script = os.path.join(root, "scripts", "ssmirror-loop.sh")

    tmp = tempfile.mkdtemp(prefix="ludodex-ssloop-")
    stub = os.path.join(tmp, "python3")
    open(stub, "w").write(STUB)
    os.chmod(stub, 0o755)
    open(os.path.join(tmp, "ids.json"), "w").write("[1,2,3]")
    mark = os.path.join(tmp, "ids.done")

    print("1. THE LOOP DOES NOT EXIT WHEN THE WALK SAYS 'COMPLETE'")
    # The catalogue gains ids above the ceiling continuously. "Complete" is true of a
    # moment, not forever, and the header's stated goal is to keep walking.
    p, calls = run(script, tmp, cycles=3, done="1")
    check("it terminated on its own cycle bound: rc=%d" % p.returncode,
          p.returncode == 0)
    walks = [c for c in calls if "--walk" in c]
    check("the walk ran on EVERY cycle, not just until the first completion: %d"
          % len(walks), len(walks) == 3)

    print()
    print("2. A FAILED TARGETED FETCH IS NOT A COMPLETED ONE")
    # 739 ids, 739 failures, and no mention of the word "skipped" anywhere. The old rule
    # (mark it done unless the output says "skipped") wrote the marker here.
    idruns = [c for c in calls if "--ids" in c]
    check("the targeted fetch ran again after the failing cycle: %d runs"
          % len(idruns), len(idruns) >= 2)
    check("and the marker was only written once it actually completed",
          os.path.exists(mark))
    log = open(os.path.join(tmp, "loop.log")).read()
    check("the failure is stated in the log, not swallowed",
          "did not complete" in log.lower() or "retry" in log.lower())

    print()
    print("3. a run that never completes never writes the marker")
    tmp2 = tempfile.mkdtemp(prefix="ludodex-ssloop2-")
    open(os.path.join(tmp2, "python3"), "w").write(STUB.replace(
        'if [ "$n" = "1" ]', 'if [ "$n" != "" ]'))       # every cycle fails
    os.chmod(os.path.join(tmp2, "python3"), 0o755)
    open(os.path.join(tmp2, "ids.json"), "w").write("[1,2,3]")
    p2, calls2 = run(script, tmp2, cycles=3, done="0")
    check("it kept retrying the targeted fetch: %d attempts"
          % len([c for c in calls2 if "--ids" in c]),
          len([c for c in calls2 if "--ids" in c]) == 3)
    check("and never claimed it was done",
          not os.path.exists(os.path.join(tmp2, "ids.done")))

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
