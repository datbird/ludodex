#!/usr/bin/env python3
"""Runs the browser render check, so `run_tests.sh` can reach it like any other test.

The assertions live in `tests/browser/detail-render.mjs`, because driving a browser is
Playwright's job and Playwright is JavaScript. This is the wrapper that puts it in the
suite: same gate, same output shape, same exit code.

It needs three things and skips cleanly without any of them, so a routine sweep never
tries to open a browser:

    LUDODEX_PLAYWRIGHT     a node_modules directory that has playwright
    LUDODEX_BROWSER_WS     a Playwright endpoint (ws://…), local or on another machine
    LUDODEX_URL / USER / PASS   a running ludodex and a login

Deliberately NOT in this repo: Playwright itself, and any host name. A browser stack is
a big dependency for a project that does not otherwise need one, and the browser is
allowed to live somewhere else entirely.
"""
import os
import shutil
import subprocess
import sys

if os.environ.get("LUDODEX_LIVE_TESTS") != "1":
    sys.exit("SKIPPED: live test. It drives a REAL browser against a RUNNING ludodex. "
             "Re-run with LUDODEX_LIVE_TESTS=1 plus LUDODEX_PLAYWRIGHT, "
             "LUDODEX_BROWSER_WS, LUDODEX_URL, LUDODEX_USER and LUDODEX_PASS.")

for var in ("LUDODEX_BROWSER_WS", "LUDODEX_URL", "LUDODEX_USER", "LUDODEX_PASS"):
    if not os.environ.get(var):
        sys.exit("SKIPPED: %s is not set." % var)
if not shutil.which("node"):
    sys.exit("SKIPPED: node is not on PATH.")

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "browser", "detail-render.mjs")


def main():
    proc = subprocess.run([shutil.which("node"), SCRIPT], text=True,
                          capture_output=True, timeout=600)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        sys.exit("FAILED: the browser check reported %d failure(s)" % proc.returncode)
    if "SKIPPED:" in proc.stdout:
        sys.exit(proc.stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    main()
