#!/usr/bin/env python3
"""A reset is defined by what it does NOT delete, so that is what this asserts.

`reset.run` removes databases, store ownership TSVs, ROM indexes, media blobs and store
token directories from the data dir. It is the most destructive code path in the
install, and the scopes are strict supersets:

  library    what an import produced. Credentials, your login, your devices and every
             hand-curated decision survive. The "let me try that import again" button.
  curation   the above plus what YOU decided about games — tags, pins, merges, splits,
             overrides, framing, ownership.
  factory    the above plus credentials and store logins. Your ACCOUNT and your backup
             archives are still kept, so a factory reset is recoverable and cannot lock
             you out of the box it just wiped.

Three specific things a "delete the databases" implementation gets wrong, all pinned
here because each one silently un-does the reset or destroys something it had no
business touching:

  * A STALE -wal REPLAYS. Removing `game-library.sqlite` and leaving
    `game-library.sqlite-wal` lets SQLite replay the log onto the file that replaced it.
  * THE STORE TSVs REPOPULATE. A rebuild reads `steam_games.tsv` as truth, so a reset
    that leaves them behind gives back the library you just emptied.
  * A MEDIA BACKUP IS NOT MEDIA. The repo is a plausible home for one — a real install
    had a 9.4 GB `.backup-<date>/` in it — and an `rmtree(repo)` would have eaten it.
    Only the content-addressed blobs at the top level and the regenerable `.thumbs`
    cache are ours to delete; every other directory is preserved AND reported.

THIS TEST DELETES FILES, so it proves its own isolation before it creates any. Section 0
is not a formality: `LUDODEX_DATA` and `LUDODEX_MEDIA` are both resolved at import time
by the module under test, and a run that resolved either to a real install would remove
that install's databases and blobs. Everything asserted below lives in a fixture data
dir this file built itself.

Offline. No network, no live instance.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

# LUDODEX_MEDIA is a SECOND path this module resolves, and isolate() does not cover it:
# inherited from a deployment's environment it would point reset.run at a real media
# repo. Clear it before the import that reads it.
os.environ.pop("LUDODEX_MEDIA", None)
DATA = test_support.isolate("ludodex-ported-reset-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import reset                                                   # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def under(path, root):
    a, b = os.path.abspath(path), os.path.abspath(root)
    return a == b or a.startswith(b.rstrip("/") + "/")


def state():
    return set(os.listdir(DATA))


def main():
    print("a reset is defined by what survives it")

    print()
    print("0. PROVE THE ISOLATION BEFORE CREATING ANYTHING TO DELETE")
    test_support.assert_isolated()          # re-checked after the imports resolved paths
    check("this test's data dir is the one isolate() made: %s" % DATA,
          os.path.isdir(DATA)
          and os.path.abspath(DATA) == os.path.abspath(os.environ["LUDODEX_DATA"]))
    check("reset resolved its DATA to that same fixture dir, not a deployment's",
          os.path.abspath(reset.DATA) == os.path.abspath(DATA))
    for live in test_support.live_dirs():
        check("and it is not %s" % live, not under(reset.DATA, live))
    # The media repo is resolved separately (LUDODEX_MEDIA > config > DATA/media) and is
    # the path run() deletes blobs from. If it escaped the fixture dir, stop.
    repo = reset.plan("library")["media_repo"]
    check("the media repo it would clear is inside the fixture dir: %s" % repo,
          under(repo, DATA))
    check("the fixture dir starts empty of everything this test asserts on",
          not [f for f in state() if f.endswith(".sqlite")])

    # ---- now, and only now, build the fixture install ------------------------ #
    seeded = (reset.IMPORT_DBS + reset.CURATION_DBS + reset.CONFIG_DBS
              + sorted(reset.KEEP_ALWAYS)
              + ["steam_games.tsv", "gog_games.tsv", "roms-index-mgr3.sqlite"])
    for f in seeded:
        with open(os.path.join(DATA, f), "w") as fh:
            fh.write("x" * 100)
    with open(os.path.join(DATA, "game-library.sqlite-wal"), "w") as fh:
        fh.write("stale log bytes")
    os.makedirs(repo, exist_ok=True)
    for i in range(5):                                  # content-addressed blobs
        with open(os.path.join(repo, "%040x.jpg" % i), "w") as fh:
            fh.write("img")
    os.makedirs(os.path.join(repo, ".thumbs"), exist_ok=True)
    with open(os.path.join(repo, ".thumbs", "t.jpg"), "w") as fh:
        fh.write("thumb")
    backup_dir = os.path.join(repo, ".backup-2026-07-21")
    os.makedirs(backup_dir, exist_ok=True)
    for i in range(3):
        with open(os.path.join(backup_dir, "b%d.png" % i), "w") as fh:
            fh.write("backup")
    for d in reset.TOKEN_DIRS:
        os.makedirs(os.path.join(DATA, d), exist_ok=True)

    print()
    print("1. plan() shows its work, and plan() is what run() does")
    p = reset.plan("library")
    check("the catalog is in the library plan", "game-library.sqlite" in p["databases"])
    check("credentials are NOT", "config.sqlite" not in p["databases"])
    check("hand-curation is NOT", "tags.sqlite" not in p["databases"])
    check("nor is the way back in", "auth.sqlite" not in p["databases"])
    check("blobs + thumbs are counted, the in-repo backup is not: %d" % p["media_files"],
          p["media_files"] == 6)
    check("and the backup is REPORTED as preserved: %s" % p["media_preserved"],
          p["media_preserved"] == [".backup-2026-07-21"])
    check("the store TSVs are counted: %s" % sorted(p["tsvs"]),
          sorted(p["tsvs"]) == ["gog_games.tsv", "steam_games.tsv"])
    check("the ROM index is counted", p["rom_indexes"] == ["roms-index-mgr3.sqlite"])
    check("store logins are untouched at this scope", not p["token_dirs"])
    check("plan() is pure — nothing has been removed yet",
          os.path.exists(os.path.join(DATA, "game-library.sqlite")))
    check("it reports what it keeps, too", sorted(p["kept"]) == sorted(reset.KEEP_ALWAYS))
    check("and a total to render in the confirmation", p["total_bytes"] > 0)

    print()
    print("2. scope=library — the 'try that import again' button")
    r = reset.run("library")
    s = state()
    check("run reports ok: %s" % (r["failed"] or "no failures"), r["ok"])
    check("the catalog is gone", "game-library.sqlite" not in s)
    check("ITS STALE WAL IS GONE TOO — it would have replayed onto the replacement",
          "game-library.sqlite-wal" not in s)
    check("the store TSVs are gone — the silent-repopulate trap",
          "steam_games.tsv" not in s and "gog_games.tsv" not in s)
    check("the ROM index is gone", "roms-index-mgr3.sqlite" not in s)
    check("credentials survive", "config.sqlite" in s)
    check("your login survives", "auth.sqlite" in s)
    check("your backup archives survive", "backups.sqlite" in s)
    check("your spend history survives", "ai-usage.sqlite" in s)
    check("every curation database survives",
          all(f in s for f in reset.CURATION_DBS))
    check("and every device connection", "connections.sqlite" in s)
    left = sorted(os.listdir(repo))
    check("only the backup and the emptied thumbs cache remain: %s" % left,
          left == [".backup-2026-07-21", ".thumbs"])
    check("regenerable thumbs were cleared", not os.listdir(os.path.join(repo, ".thumbs")))
    check("THE MEDIA BACKUP IS UNTOUCHED — a reset must never eat a backup",
          sorted(os.listdir(backup_dir)) == ["b0.png", "b1.png", "b2.png"])
    check("store token dirs survive a library reset",
          all(os.path.isdir(os.path.join(DATA, d)) for d in reset.TOKEN_DIRS))

    print()
    print("3. scope=curation — your decisions go, your credentials do not")
    p = reset.plan("curation")
    check("curation databases are now in the plan", "tags.sqlite" in p["databases"])
    check("credentials still are not", "config.sqlite" not in p["databases"])
    reset.run("curation")
    s = state()
    check("tags, merges and the rest are gone",
          not any(f in s for f in reset.CURATION_DBS))
    check("credentials STILL survive", "config.sqlite" in s)
    check("and so does your login", "auth.sqlite" in s)

    print()
    print("4. scope=factory — recoverable, and it cannot lock you out")
    p = reset.plan("factory")
    check("credentials are included", "config.sqlite" in p["databases"])
    check("store logins are included",
          sorted(p["token_dirs"]) == sorted(reset.TOKEN_DIRS))
    check("auth.sqlite is STILL not in the plan, at the widest scope there is",
          "auth.sqlite" not in p["databases"])
    reset.run("factory")
    s = state()
    check("credentials are gone", "config.sqlite" not in s)
    check("device connections are gone", "connections.sqlite" not in s)
    check("store token dirs are gone",
          not any(os.path.isdir(os.path.join(DATA, d)) for d in reset.TOKEN_DIRS))
    check("YOUR ACCOUNT SURVIVES — factory cannot lock you out", "auth.sqlite" in s)
    check("and the archive list survives, so it stays recoverable", "backups.sqlite" in s)

    print()
    print("5. the scopes are strict supersets, by construction")
    lib = set(reset.plan("library")["databases"]) | set(reset.IMPORT_DBS)
    cur = set(reset.IMPORT_DBS) | set(reset.CURATION_DBS)
    fac = cur | set(reset.CONFIG_DBS)
    check("library < curation", set(reset.IMPORT_DBS) < cur)
    check("curation < factory", cur < fac)
    check("and nothing in KEEP_ALWAYS appears in any of them",
          not (reset.KEEP_ALWAYS & (lib | cur | fac)))

    print()
    print("6. an unknown scope raises rather than guessing")
    for fn in (reset.plan, reset.run):
        raised = None
        try:
            fn("everything")
        except ValueError as e:                             # noqa: PERF203
            raised = e
        check("%s('everything') raises ValueError" % fn.__name__, raised is not None)
    check("and there are exactly three scopes",
          reset.SCOPES == ("library", "curation", "factory"))

    print()
    print("7. run() on an already-clean dir is a no-op, not an error")
    r = reset.run("factory")
    check("it still reports ok", r["ok"])
    check("your account is STILL there", "auth.sqlite" in state())

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
