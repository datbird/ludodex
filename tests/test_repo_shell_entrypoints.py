#!/usr/bin/env python3
"""Every path a shell entry point hands to python3 (or bash, or scp) must exist.

THIS IS THE SECOND TIME THIS CLASS OF BUG SHIPPED. `tests/test_script_paths.py` pins
the SERVER side of it — the scripts moved into `ludodex/` and `server/app.py` kept
spawning `python3 steam_owned.py` against /app. The shell side broke the same week and
nobody noticed for ten days:

    608e201 moved setup.sh / update.sh / auth_status.sh from the repo root into
    scripts/. All three begin `cd "$(dirname "$0")"`, which USED to mean the repo root
    and now means scripts/. Every `python3 ludodex/config.py ...` in all three then
    resolved to `scripts/ludodex/config.py`, which does not exist.

The symptom was not a crash. `auth_status.sh` prints "<source>: BROKEN" whenever a
command produces no output, so a python3 that could not open its file looked exactly
like an expired credential: the script cheerfully reported EVERY source broken, and
dropped its .gogchk/.itchchk/.eachk scratch files into scripts/ on the way out.

A missing script is a runtime failure inside a subprocess whose stderr the caller
throws away. Statically resolving the paths costs nothing and fails the moment a file
moves again.

Checked here:
  1. each entry point resolves its own directory to the REPO ROOT, not to scripts/
  2. every `python3 <x>.py` in every shell script names a file that exists
  3. every `bash <x>.sh` likewise
  4. every file `scp`'d to a remote likewise
  5. a `python3 -c 'import config'` one-liner can actually find the module
  6. nothing invokes the retired sync.py
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
SCRIPTS = os.path.join(ROOT, "scripts")

# The three user-facing entry points. Each one is documented, each one is invoked by a
# skill, and each one was dead.
ENTRYPOINTS = ("setup.sh", "update.sh", "auth_status.sh")


def _shell_files():
    return sorted(f for f in os.listdir(SCRIPTS) if f.endswith(".sh"))


def _resolve(ref):
    """Map a path as written in a shell script onto this checkout.

    Absolute /app/... paths are the in-container form of the repo root (Dockerfile
    does `COPY . /app`), so they are checked against the checkout too. Everything else
    is relative to the repo root, because that is where all three entry points cd to
    and this test pins that they do.
    """
    if ref.startswith("/app/"):
        return os.path.join(ROOT, ref[len("/app/"):])
    if ref.startswith("/"):
        return None                      # some other absolute path; not ours to judge
    return os.path.join(ROOT, ref)


def _strip_comments(text):
    """Drop whole-line comments. Prose in a header must not count as an invocation."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def main():
    print()
    print("1. each entry point resolves its own dir to the REPO ROOT, not scripts/")
    # The exact regression: `cd "$(dirname "$0")"` from inside scripts/. Matched by
    # shape rather than by literal, so re-spelling it does not slip past.
    for fn in ENTRYPOINTS:
        src = open(os.path.join(SCRIPTS, fn), encoding="utf-8").read()
        body = _strip_comments(src)
        naive = re.search(r'cd\s+"\$\(dirname\s+"\$(?:0|\{?BASH_SOURCE\[0\]\}?)"\)"\s*(?:\|\||$)',
                          body, re.M)
        check("%s does not cd to its own directory" % fn, not naive)
        check("%s climbs out of scripts/ (finds `..`)" % fn,
              re.search(r'dirname\s+"\$(?:0|\{?BASH_SOURCE\[0\]\}?)"\)/\.\.', body))

    print()
    print("2. every `python3 <x>.py` names a file that exists")
    # EVERY shell script, not just the three that broke — the next one to break will
    # be a different one.
    bad = []
    seen = 0
    for fn in _shell_files():
        body = _strip_comments(open(os.path.join(SCRIPTS, fn), encoding="utf-8").read())
        for ref in re.findall(r'python3?\s+((?:[\w./$-]+/)?[\w.-]+\.py)\b', body):
            if "$" in ref:               # a variable, not a literal path
                continue
            seen += 1
            path = _resolve(ref)
            if path is not None and not os.path.exists(path):
                bad.append("%s -> %s" % (fn, ref))
    check("the scripts run some python: %d invocations" % seen, seen >= 20)
    check("all %d resolve (missing: %s)" % (seen, bad or "none"), not bad)

    print()
    print("3. every `bash <x>.sh` names a file that exists")
    bad = []
    seen = 0
    for fn in _shell_files():
        body = _strip_comments(open(os.path.join(SCRIPTS, fn), encoding="utf-8").read())
        for ref in re.findall(r'(?:^|\s)(?:bash|sh)\s+((?:[\w./$-]+/)?[\w.-]+\.sh)\b', body):
            if "$" in ref:
                continue
            seen += 1
            path = _resolve(ref)
            if path is not None and not os.path.exists(path):
                bad.append("%s -> %s" % (fn, ref))
    check("setup.sh calls auth_status.sh, so there is at least one", seen >= 1)
    check("all %d resolve (missing: %s)" % (seen, bad or "none"), not bad)

    print()
    print("4. every file scp'd to a remote exists")
    # update.sh --roms ships build_romdb.py + romtags.py to the ROM host. Both moved
    # into ludodex/ with everything else and the scp was still naming them bare.
    bad = []
    seen = 0
    for fn in _shell_files():
        body = _strip_comments(open(os.path.join(SCRIPTS, fn), encoding="utf-8").read())
        for line in re.findall(r'^\s*scp\s+(.*)$', body, re.M):
            for tok in line.split():
                if not tok.endswith(".py") or "$" in tok or ":" in tok:
                    continue
                seen += 1
                path = _resolve(tok)
                if path is not None and not os.path.exists(path):
                    bad.append("%s -> %s" % (fn, tok))
    check("all %d scp'd files resolve (missing: %s)" % (seen, bad or "none"), not bad)

    print()
    print("5. a `python3 -c 'import <mod>'` one-liner can find the module")
    # Same breakage, different mechanism: the package is not installed, so `import
    # config` from the repo root is a ModuleNotFoundError unless the script puts
    # ludodex/ on the path. update.sh has four of these gating whole pipeline phases;
    # each one printed an empty string and silently skipped its phase.
    pkg = os.path.join(ROOT, "ludodex")
    bad = []
    seen = 0
    for fn in _shell_files():
        src = open(os.path.join(SCRIPTS, fn), encoding="utf-8").read()
        body = _strip_comments(src)
        # BOTH quote styles. The first version of this check only matched single
        # quotes, so `python3 -c "import config; ..."` — the same bug, one keystroke
        # away — walked straight past it.
        mods = set()
        for oneliner in re.findall(r"""python3?\s+-c\s+(['"])(.*?)\1""", body, re.S):
            text = oneliner[1]
            # a one-liner that fixes sys.path itself is self-sufficient
            if "sys.path" in text:
                continue
            mods.update(re.findall(r'\bimport\s+([a-z_][a-z0-9_]*)', text))
        # Only PACKAGE modules are our problem. `import google.oauth2` is a third-party
        # dependency: pip's job, not the path's. `import config` is ludodex/config.py,
        # which is on no path unless the script puts it there.
        ours = sorted(m for m in mods if os.path.exists(os.path.join(pkg, m + ".py")))
        if not ours:
            continue
        exports_path = re.search(r'PYTHONPATH=[^\n]*ludodex', body)
        for m in ours:
            seen += 1
            if not exports_path:
                bad.append("%s -> imports %s without putting ludodex/ on PYTHONPATH"
                           % (fn, m))
    check("some one-liners import the package: %d" % seen, seen >= 1)
    check("all %d are importable (broken: %s)" % (seen, bad or "none"), not bad)

    print()
    print("6. nothing invokes the retired sync.py")
    # sync.py — the one-way "publish catalog" mirror — was retired 2026-07-21 and the
    # file deleted. update.sh kept calling it for a month behind `|| echo FAILED`.
    check("sync.py really is gone", not os.path.exists(os.path.join(pkg, "sync.py"))
          and not os.path.exists(os.path.join(ROOT, "sync.py")))
    offenders = []
    for fn in _shell_files():
        body = _strip_comments(open(os.path.join(SCRIPTS, fn), encoding="utf-8").read())
        if re.search(r'python3?\s+(?:\S*/)?sync\.py\b', body):
            offenders.append(fn)
    check("no shell script runs it: %s" % (offenders or "none"), not offenders)

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
