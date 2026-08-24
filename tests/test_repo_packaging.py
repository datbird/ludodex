#!/usr/bin/env python3
"""The image must be reproducible, and must contain only what the app runs.

Three failures this pins, all of which shipped:

  1. NOTHING WAS PINNED. Every line of requirements.txt was a `>=` floor, the base
     images were tag-pinned rather than digest-pinned, and there was no lock. Two builds
     a week apart could contain different libraries with nothing in the repo recording
     which. The web half had `pnpm-lock.yaml` + `--frozen-lockfile` from day one; the
     Python half had nothing.

  2. `COPY . /app` SHIPPED THE DEVELOPMENT TREE. .dockerignore dropped local data and
     `*.md`, but not tests/ (over 100 scripts plus a 2 MB image corpus), not
     `ludodex/verify_*.py` (one of which rsync'd /data and rebuilt against the copy),
     not skills/, and not the test runner.

  3. THREE REQUIREMENTS FILES, two of them stale. `requirements-server.txt` was a strict
     subset of `requirements.txt` citing a HANDOFF section that called the FastAPI
     server future work; `requirements.txt` cited `sync.py`, deleted a month earlier.

The lock is the part that rots silently: add a dependency to requirements.txt, forget to
re-lock, and the constraints file simply does not mention it — so it installs unpinned
and nothing says a word. Case 2 below is the guard for exactly that.
"""
import os
import re
import sys

PASS = []


def check(label, cond):
    PASS.append(bool(cond))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _reqs(text):
    """{name: specifier} for the real requirement lines, comments and options dropped."""
    out = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)(\[[^\]]*\])?\s*(.*)$", line)
        if m:
            out[m.group(1).lower().replace("_", "-")] = m.group(3).strip()
    return out


def main():
    print()
    print("1. the lock exists and pins every line exactly")
    lock_txt = _read("requirements.lock")
    lock = _reqs(lock_txt)
    check("requirements.lock has entries: %d" % len(lock), len(lock) >= 20)
    loose = sorted(n for n, spec in lock.items() if not spec.startswith("=="))
    check("every entry is an exact == pin (loose: %s)" % (loose or "none"), not loose)

    print()
    print("2. every direct dependency is IN the lock")
    # The silent-rot guard. A name in requirements.txt with no line here installs
    # unpinned, which is the state this whole file exists to prevent.
    direct = _reqs(_read("requirements.txt"))
    check("requirements.txt names some dependencies: %d" % len(direct), len(direct) >= 5)
    missing = sorted(n for n in direct if n not in lock)
    check("all %d direct deps are locked (unlocked: %s)" % (len(direct), missing or "none"),
          not missing)
    # ...and the lock must not contradict the floor it claims to satisfy.
    bad = []
    for name, spec in direct.items():
        m = re.match(r">=\s*([0-9][0-9.]*)", spec or "")
        if not m:
            continue
        floor = tuple(int(x) for x in m.group(1).split(".") if x.isdigit())
        got = tuple(int(x) for x in lock[name][2:].split(".") if x.isdigit())
        if got[:len(floor)] < floor:
            bad.append("%s: floor %s, locked %s" % (name, m.group(1), lock[name][2:]))
    check("no locked version is below its floor (%s)" % (bad or "none"), not bad)

    print()
    print("3. the Dockerfile actually applies the lock, and pins its base images")
    df = _read("Dockerfile")
    check("pip install passes -c requirements.lock",
          re.search(r"pip install[^\n]*-c\s+requirements\.lock", df))
    check("requirements.lock is COPYed in", re.search(r"COPY[^\n]*requirements\.lock", df))
    # A tag is a moving target; a digest is not. Every FROM must carry one.
    froms = re.findall(r"^FROM\s+(\S+)", df, re.M)
    check("the Dockerfile has stages: %s" % froms, len(froms) >= 2)
    untagged = [f for f in froms if "@sha256:" not in f]
    check("every base image is digest-pinned (tag-only: %s)" % (untagged or "none"),
          not untagged)

    print()
    print("4. .dockerignore keeps development material out of the image")
    di = [ln.strip() for ln in _read(".dockerignore").splitlines()
          if ln.strip() and not ln.strip().startswith("#")]
    for want in ("tests/", "ludodex/verify_*.py", "skills/", "scripts/run_tests.sh"):
        check("excluded: %s" % want, want in di)
    # The corpus is the expensive part, and it is inside tests/ — so prove tests/ is
    # actually there to be excluded rather than trusting the pattern in the abstract.
    check("there IS a tests/ tree with a corpus to exclude",
          os.path.isdir(os.path.join(ROOT, "tests", "corpus")))

    print()
    print("5. the requirements files are consolidated")
    # requirements-server.txt was a strict subset of requirements.txt. Two files with two
    # jobs is fine; three files where one is a subset of another is a bump-and-forget.
    check("requirements-server.txt is gone",
          not os.path.exists(os.path.join(ROOT, "requirements-server.txt")))
    # requirements-firebase.txt stays because ludodex/remote_db.py PRINTS its name when
    # the google-auth import fails. If that message ever goes, so can the file.
    fb = os.path.join(ROOT, "requirements-firebase.txt")
    named = "requirements-firebase.txt" in _read("ludodex/remote_db.py")
    check("requirements-firebase.txt exists exactly while the code names it",
          os.path.exists(fb) == named)

    print()
    print("6. no requirements file cites something that does not exist")
    # requirements.txt cited sync.py for a month after sync.py was deleted, and
    # requirements-server.txt cited a HANDOFF section describing its own contents as
    # future work.
    for rel in ("requirements.txt", "requirements-firebase.txt", "requirements.lock"):
        txt = _read(rel)
        cited = set(re.findall(r"\b([a-z_][a-z0-9_]*\.py)\b", txt))
        gone = sorted(n for n in cited
                      if not any(os.path.exists(os.path.join(ROOT, d, n))
                                 for d in ("ludodex", "", "tests", "server")))
        check("%s cites only real scripts (missing: %s)" % (rel, gone or "none"), not gone)

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
