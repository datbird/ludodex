#!/usr/bin/env python3
"""The isolation guard itself — the one test that must work inside the container.

On 2026-08-02 `test_shape_select.py` erased the live 66,280-row media index because it
used `os.environ.setdefault("LUDODEX_DATA", tempfile.mkdtemp())`. setdefault KEEPS an
existing value, and inside the container `LUDODEX_DATA=/data` is already set — so the
temp dir was never used and the fixture's `DELETE FROM media` hit production.

These cases pin both halves of the fix: the override actually overrides, and a test
pointed at a live directory refuses to run instead of quietly destroying it.
"""
import os
import subprocess
import sys

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ludodex")
sys.path.insert(0, DIR)

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _run(env_data, body, **extra):
    """Run `body` in a fresh interpreter with LUDODEX_DATA=env_data."""
    env = dict(os.environ)
    env.pop("LUDODEX_LIVE_DIRS", None)     # never inherit the operator's list
    if env_data is None:
        env.pop("LUDODEX_DATA", None)
    else:
        env["LUDODEX_DATA"] = env_data
    env.update({k: v for k, v in extra.items() if v is not None})
    return subprocess.run(
        [sys.executable, "-c",
         # The modules under test live at the repo root; test_support lives here.
         "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
         % (DIR, os.path.dirname(os.path.abspath(__file__))) + body],
        env=env, capture_output=True, text=True)


def main():
    print("1. isolate() OVERRIDES an inherited value (the setdefault bug)")
    r = _run("/data", "import test_support, os\n"
                      "d = test_support.isolate()\n"
                      "print('DATA=' + os.environ['LUDODEX_DATA'])\n"
                      "print('SAME' if d == os.environ['LUDODEX_DATA'] else 'DRIFT')\n")
    check("it exits cleanly even though /data was inherited", r.returncode == 0)
    check("LUDODEX_DATA no longer points at /data", "DATA=/data\n" not in r.stdout)
    check("the returned dir is the one that got exported", "SAME" in r.stdout)

    print("2. a test pointed at a LIVE dir refuses to run")
    # /data is guarded unconditionally: it is the container's data dir, so it must
    # refuse with no configuration at all.
    for live in ("/data", "/data/", "/data/subdir"):
        r = _run(live, "import test_support\ntest_support.assert_isolated()\n")
        check("refuses on %s with no config" % live,
              r.returncode != 0 and "REFUSING TO RUN" in (r.stderr + r.stdout))

    print("2b. a deployment's OWN data dir is guarded once it declares it")
    # A host bind-mount lives at a path only that operator knows, so the guard takes
    # it from LUDODEX_LIVE_DIRS instead of hardcoding somebody's filesystem.
    host = "/srv/ludodex-data"
    other = "/mnt/tank/ludodex"
    for live in (host, host + "/", host + "/subdir", other):
        r = _run(live, "import test_support\ntest_support.assert_isolated()\n",
                 LUDODEX_LIVE_DIRS=os.pathsep.join((host, other)))
        check("refuses on declared %s" % live,
              r.returncode != 0 and "REFUSING TO RUN" in (r.stderr + r.stdout))
    # ...and that same path is fine when nobody declared it, which is what makes the
    # list configuration rather than a second hardcoded constant.
    r = _run(host, "import test_support\nprint(test_support.assert_isolated())\n")
    check("an UNdeclared path is not guarded", r.returncode == 0)
    # The unconditional entry cannot be configured away.
    r = _run("/data", "import test_support\ntest_support.assert_isolated()\n",
             LUDODEX_LIVE_DIRS=other)
    check("declaring other dirs does not un-guard /data",
          r.returncode != 0 and "REFUSING TO RUN" in (r.stderr + r.stdout))

    print("3. an unset LUDODEX_DATA refuses too (it would use the default dir)")
    r = _run(None, "import test_support\ntest_support.assert_isolated()\n")
    check("refuses when unset",
          r.returncode != 0 and "REFUSING TO RUN" in (r.stderr + r.stdout))

    print("4. a genuinely disposable dir is allowed")
    r = _run("/tmp/ludodex-not-real-data",
             "import test_support\nprint(test_support.assert_isolated())\n")
    check("a temp path passes", r.returncode == 0)

    print("5. no offline test may use setdefault for LUDODEX_DATA")
    # The guard only helps if nothing reintroduces the pattern. Cheap and total.
    #
    # This swept `ludodex/` — where the tests USED to live — and so swept nothing at
    # all after they moved to tests/. A sweep that cannot fail is not a guard, so it
    # now asserts it actually found the suite before trusting the verdict.
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    names = [fn for fn in sorted(os.listdir(tests_dir))
             if fn.startswith("test_") and fn.endswith(".py")
             # this file names the pattern in prose, which is not the same as using it
             and fn != os.path.basename(__file__)]
    check("the sweep can see the suite: %d test files" % len(names), len(names) >= 10)
    offenders = []
    for fn in names:
        with open(os.path.join(tests_dir, fn), "r", encoding="utf-8") as fh:
            if 'setdefault("LUDODEX_DATA"' in fh.read():
                offenders.append(fn)
    check("no test file calls setdefault on LUDODEX_DATA: %s" % (offenders or "none"),
          not offenders)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
