#!/usr/bin/env python3
"""Where ludodex keeps its durable state. The ONLY place that reads LUDODEX_DATA.

    import paths
    DATA = paths.DATA

`ROOT` is the repository root above this package. `DATA` is the data directory:
`$LUDODEX_DATA` when it is set and non-empty, else `ROOT`, which is where a plain
checkout has always kept its databases (the container sets `LUDODEX_DATA=/data`).

Every module used to compute this itself, about forty copies of one line, and they did
not quite agree: most used `os.environ.get("LUDODEX_DATA", default)`, which takes an
EMPTY value as a real path (the current directory), while a few used `or`, which falls
back. One line here settles it, and a test fails if a second reader appears.

This module imports nothing from ludodex, so any module can import it first.

RESOLVED ONCE, AT FIRST IMPORT. A process that wants a different data directory sets
`LUDODEX_DATA` before importing any ludodex module (the container's environment, the
test runner's per-test scratch dir, `test_support.isolate()`). Nothing re-reads it later,
on purpose: a data directory that moved halfway through a process would split its writes
across two libraries. A test that repoints one module after import assigns that
module's `DATA` directly.
"""
import os

# this package's directory, and the repo root above it
PKG = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG)

DATA = os.environ.get("LUDODEX_DATA") or ROOT
