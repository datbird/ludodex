#!/usr/bin/env python3
"""The hand-written media-provider fallback in config.py must not drift from media.py.

`config.MEDIA_PROVIDERS` is what `config.py enable <provider>` and every per-provider
scope setting are checked against: a provider missing from it cannot be turned on or
scoped, while still writing rows and still filling slots. The main path imports the list
from `media`, so it cannot drift — but the CLI also runs on machines without media's
dependencies (Pillow, requests), and there the hard-coded literal is the whole list.

That literal was three names short of `media.MEDIA_PROVIDERS`: `gamelist`,
`screenscraper` and `web` — the exact three that a previous audit had already had to add
to `media.REMOTE_PROVIDERS`/`LOCAL_PROVIDERS` for the same reason. A copy that has to be
kept in sync by hand is a copy that drifts, so this test is the thing keeping it honest.

Offline. No network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-cfgfb-")
DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import config                                                  # noqa: E402
import media                                                   # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    print("1. the deps-present path is media's list, unmodified")
    check("config.MEDIA_PROVIDERS is media.MEDIA_PROVIDERS",
          tuple(config.MEDIA_PROVIDERS) == tuple(media.MEDIA_PROVIDERS))

    print("2. the no-deps FALLBACK names every provider media knows")
    fb = set(config._MEDIA_PROVIDERS_FALLBACK)
    real = set(media.MEDIA_PROVIDERS)
    check("nothing media has is missing from the fallback (%r)"
          % sorted(real - fb), not (real - fb))
    check("and the fallback invents nothing media does not have (%r)"
          % sorted(fb - real), not (fb - real))

    print("3. the three the audit found are enable-able either way")
    for name in ("gamelist", "screenscraper", "web"):
        check("%r can be enabled with media importable" % name,
              name in config.MEDIA_PROVIDERS)
        check("%r can be enabled without it" % name,
              name in config._MEDIA_PROVIDERS_FALLBACK)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
