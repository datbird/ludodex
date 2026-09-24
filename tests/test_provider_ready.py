#!/usr/bin/env python3
"""One answer to "is this provider configured and ready?" (config.ready).

The CLI status column, the server's sync menu + Services page, and the provider
capability matrix each used to carry their own if-chain, and they disagreed: the CLI
called EA ready on a remid or any token file (expired or not) and Epic ready on any
user.json, while the server checked for a live token and a named login. All three now
read config.READY, so they cannot drift apart again.

Token values here are fixtures and are never printed.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
DATA = test_support.isolate("ludodex-ready-")
os.environ["HOME"] = tempfile.mkdtemp(prefix="ludodex-ready-home-")   # legendary's user.json

import config                                    # noqa: E402
import provider_caps as PC                       # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))


def _integration(pid):
    return next(i for i in config.INTEGRATIONS if i["id"] == pid)


def main():
    print("1. nothing configured")
    for pid in ("steam", "itch", "gog", "ea", "epic", "psn", "xbox", "nintendo"):
        check("%s is not ready" % pid, not config.ready(pid))
    check("an unknown id is not ready", not config.ready("no-such-provider"))

    print("2. EA: only a live token counts (the CLI adopted the server's check)")
    tok = os.path.join(DATA, ".ea", "token.json")
    config.set_("ea_remid", "fixture-remid")
    check("a remid alone is not ready", not config.ready("ea"))
    check("and the CLI agrees", not config._has_cred(_integration("ea")))
    _write(tok, {"access_token": "fixture", "expires_at": time.time() - 60})
    check("an expired token is not ready", not config.ready("ea"))
    _write(tok, "not json")
    check("an unreadable token file is not ready", not config.ready("ea"))
    _write(tok, {"access_token": "fixture", "expires_at": time.time() + 3600})
    check("a live token is ready", config.ready("ea"))
    check("and the CLI agrees", config._has_cred(_integration("ea")))

    print("3. Epic: a login with a display name, not just a file")
    uf = os.path.expanduser("~/.config/legendary/user.json")
    _write(uf, {})
    check("an empty user.json is not ready", not config.ready("epic"))
    check("and the CLI agrees", not config._has_cred(_integration("epic")))
    _write(uf, {"displayName": "fixture"})
    check("a named login is ready", config.ready("epic"))

    print("4. the cached-token stores")
    for sub in ("gog", "psn", "xbox"):
        _write(os.path.join(DATA, "." + sub, "tokens.json"), {})
        check("%s ready once tokens.json exists" % sub, config.ready(sub))
    _write(os.path.join(DATA, ".nintendo", "cookies.json"), {})
    check("nintendo ready once cookies.json exists", config.ready("nintendo"))

    print("5. key-based sources")
    config.set_("steam_api_key", "fixture")
    check("steam needs the id as well as the key", not config.ready("steam"))
    config.set_("steam_id", "76561190000000000")
    check("steam with both is ready", config.ready("steam"))

    print("6. ScreenScraper: 'has creds' and 'has a devid' are one question")
    creds = config.screenscraper_creds()
    check("creds present exactly when a devid is",
          bool(creds) == bool(creds.get("devid")) == config.ready("screenscraper"))

    print("7. the capability matrix asks only about credentialled providers")
    check("steam is 'configured' for attributes whatever its ownership login",
          PC.configured("steam"))
    check("thegamesdb follows config.ready", PC.configured("thegamesdb")
          == config.ready("thegamesdb"))
    config.set_("thegamesdb_api_key", "0123456789abcdef")
    check("and flips with its key", PC.configured("thegamesdb")
          and config.ready("thegamesdb"))

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
