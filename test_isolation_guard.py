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

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _run(env_data, body):
    """Run `body` in a fresh interpreter with LUDODEX_DATA=env_data."""
    env = dict(os.environ)
    if env_data is None:
        env.pop("LUDODEX_DATA", None)
    else:
        env["LUDODEX_DATA"] = env_data
    return subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, %r)\n" % DIR + body],
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
    for live in ("/data", "/data/", "<appdata>/ludodex",
                 "<appdata>/ludodex/subdir"):
        r = _run(live, "import test_support\ntest_support.assert_isolated()\n")
        check("refuses on %s" % live,
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
    offenders = []
    for fn in sorted(os.listdir(DIR)):
        # this file names the pattern in prose, which is not the same as using it
        if not (fn.startswith("test_") and fn.endswith(".py")) or fn == os.path.basename(__file__):
            continue
        with open(os.path.join(DIR, fn), "r", encoding="utf-8") as fh:
            if 'setdefault("LUDODEX_DATA"' in fh.read():
                offenders.append(fn)
    check("no test file calls setdefault on LUDODEX_DATA: %s" % (offenders or "none"),
          not offenders)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
