#!/usr/bin/env python3
"""Store credentials are written owner-only, and store output survives odd titles.

TWO RULES, ONE PER HALF.

1. A CACHED TOKEN IS A PASSWORD. Five of the store scripts wrote theirs with
   `json.dump(tok, open(TOKFILE, "w"))` and no chmod, so the file landed at whatever
   the process umask allowed — group-readable on a normal shell, 0644 under Docker,
   where every other container sharing /data can read it. PSN's refresh token is good
   for about two months; GOG's rotates but is equally live; Xbox's mints Xbox Live
   tokens on demand. Meanwhile `ea_owned.save_cookies`, `ea_owned.save_token` and
   `nintendo_owned.save_cookies` all set 0o600 and nintendo's writes atomically, so
   the project already knew the rule — it was just applied three times out of eight.
   One helper, used everywhere, is what makes that stay true for the ninth store.

   Atomic matters as much as the mode: `open(path, "w")` truncates first, so a crash
   or a full disk between truncate and write leaves an EMPTY token file, and the next
   run reports "not connected" for a session that was perfectly valid.

2. A TAB IN A TITLE IS NOT A COLUMN SEPARATOR. Every store script printed
   `"%s\\t%s" % (gid, title)` with no escaping, and build_library's loader splits on
   \\t and strips only \\n. A title containing a tab therefore loses its tail (the
   real one: Steam and itch let publishers put whitespace in a name), and a stray
   \\r rides into the catalog title, where it becomes part of the norm_key and
   quietly forks the game into two entries.

Offline. No network, no real credentials.
"""
import json
import os
import stat
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-store-secrets-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import config                                                  # noqa: E402

PASS = []
LUDODEX = os.path.join(DIR, "ludodex")


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def mode_of(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def src(name):
    return open(os.path.join(LUDODEX, name), encoding="utf-8").read()


def main():
    print("store credentials are owner-only and store output is tab-safe")

    # ---- the shared helper ------------------------------------------------------ #
    p = os.path.join(DATA, "creds", "tokens.json")
    config.write_private_json(p, {"refresh_token": "not-a-real-token"})
    check("the helper creates the directory it was given", os.path.exists(p))
    check("the token file is owner-only (got 0%o)" % mode_of(p), mode_of(p) == 0o600)
    check("the containing directory is owner-only too",
          mode_of(os.path.dirname(p)) == 0o700)
    check("and it round-trips",
          json.load(open(p))["refresh_token"] == "not-a-real-token")

    # a rewrite must never leave a truncated file behind if it dies mid-write
    config.write_private_json(p, {"refresh_token": "second"})
    check("a rewrite replaces the contents", json.load(open(p))["refresh_token"] == "second")
    check("and keeps the mode", mode_of(p) == 0o600)
    check("leaving no temp file beside it",
          [f for f in os.listdir(os.path.dirname(p)) if f != "tokens.json"] == [])

    # ---- every store script that caches a credential uses it -------------------- #
    for name in ("gog_owned.py", "gog_wishlist.py", "psn_owned.py", "xbox_owned.py",
                 "ea_owned.py", "nintendo_owned.py"):
        s = src(name)
        check("%s writes its credential through the shared helper" % name,
              "write_private_json" in s)
        check("%s no longer json.dumps straight into an open() " % name,
              'json.dump(tok, open(' not in s)

    # ---- and the ones that can be imported actually produce 0600 ---------------- #
    import psn_owned                                           # noqa: E402
    import xbox_owned                                          # noqa: E402
    import ea_owned                                            # noqa: E402
    import nintendo_owned                                      # noqa: E402

    psn_owned._save({"refresh_token": "x", "access_token": "y", "expires_in": 3600})
    check("psn caches its ~2-month refresh token owner-only",
          mode_of(psn_owned.TOKFILE) == 0o600)
    xbox_owned._save({"refresh_token": "x", "access_token": "y"})
    check("xbox caches its refresh token owner-only",
          mode_of(xbox_owned.TOKFILE) == 0o600)
    ea_owned.save_token("browser-token")
    check("ea keeps its browser token owner-only", mode_of(ea_owned.TOKEN) == 0o600)
    ea_owned.save_cookies({"remid": "x"})
    check("ea keeps its remid cookie owner-only", mode_of(ea_owned.COOKIES) == 0o600)
    nintendo_owned.save_cookies("a=1; b=2")
    check("nintendo keeps its session cookie owner-only",
          mode_of(nintendo_owned.COOKIEFILE) == 0o600)

    # ---- TSV output survives a title with whitespace in it ---------------------- #
    row = config.tsv_row("12345", "Half-Life\t2\r", "windows")
    check("a tab inside a field never becomes a column break",
          row.count("\t") == 2)
    check("and the tail of the title survives", "2" in row.split("\t")[1])
    check("a stray carriage return is gone too", "\r" not in row)
    check("a newline cannot split one game into two rows", "\n" not in
          config.tsv_row("1", "A\nB"))
    check("ordinary titles are untouched",
          config.tsv_row("7", "Chrono Trigger") == "7\tChrono Trigger")

    # what the loader would make of it — build_library splits on \t and takes [1]
    fields = row.split("\t")
    check("the loader would see three fields, not four", len(fields) == 3)
    check("and read the platform from the third", fields[2] == "windows")

    for name in ("gog_owned.py", "steam_owned.py", "itch_owned.py", "ea_owned.py",
                 "psn_owned.py", "xbox_owned.py", "nintendo_owned.py",
                 "epic_owned.py", "steam_wishlist.py", "gog_wishlist.py"):
        s = src(name)
        check("%s writes its rows through tsv_row" % name, "tsv_row" in s)
        check("%s has no raw tab-joined print left" % name,
              '"%s\\t%s"' not in s and "'%s\\t%s'" not in s)

    # ---- epic writes where the loader reads ------------------------------------- #
    # It derived its output dir from the PACKAGE directory, so without LUDODEX_DATA it
    # wrote epic_games.tsv into ludodex/ while build_library looks in the repo root.
    s = src("epic_owned.py")
    check("epic resolves its output dir the way every sibling does",
          "os.path.dirname(DIR)" in s)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
