#!/usr/bin/env python3
"""Where state lives, what an estimate counts, and what leaves the machine.

Six unrelated modules, one shape of defect in each: a number, a path or a message that
was WRONG IN A WAY NOBODY COULD SEE.

  * `config.library_db` seeded an ABSOLUTE path at first init, so a config.sqlite created
    on a checkout and later mounted at /data kept pointing at the checkout. The container
    then wrote its catalog to a directory that does not exist there, and the library came
    up empty with nothing reporting an error — although the docs say every piece of state
    lives in the data dir.
  * `estimate.plan` subtracted `COUNT(*) FROM ss_resolution` from the game count, but a
    recorded MISS is a row in that table. A library whose last match pass mostly missed
    therefore estimated the next one at zero work, and the UI offered "under a minute"
    for an hour of matching — under a project rule that paid work must never surprise.
  * `check_invariants` I11 read ONE arbitrary platform row per norm_key on a catalog that
    keeps one row per (game, platform), then judged the match against it. That is the
    structural reason I9/I10/I11 stay red: the check reports noise, not mismatches.
  * `cf_access.verify_email` swallowed every exception into None, so a JWKS fetch failure
    or a typo'd team_domain was indistinguishable from "nobody is logged in" — an admin
    debugging SSO saw 401s and no log line anywhere.
  * `reset` scope `curation` deletes sync_cache.sqlite, the merge shadow, so with a
    backing store configured the next automatic sync sees local empty, shadow empty and
    remote full, and pulls everything back. There is no "reset the remote", and nothing
    said so.
  * `aimeta._rom_file_context` shipped 500 bytes of ANY small .txt/.nfo/.json/.ini/.md
    sitting next to a ROM into a PAID prompt. A ROM folder is a place users put arbitrary
    files.

Offline. Fixtures only; no network, no live data dir.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-state-paths-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import aimeta                                                  # noqa: E402
import config                                                  # noqa: E402
import estimate                                                # noqa: E402
import reset                                                   # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def main():
    print("state lives in the data dir, and the numbers mean what they say")

    # ---- config: a stale absolute path heals, a real custom one is honoured ------ #
    config.init()
    check("a fresh install puts the catalog in the data dir",
          config.get("library_db") == os.path.join(DATA, "game-library.sqlite"))
    check("no absolute path is baked into the seeded row",
          dict(config._con().execute("SELECT key, value FROM config WHERE key='library_db'")
               )["library_db"] == "")

    config.set_("library_db", "/nowhere/that/exists/on/this/box/game-library.sqlite")
    check("a path from another machine is not used",
          config.get("library_db") == os.path.join(DATA, "game-library.sqlite"))

    custom_dir = os.path.join(DATA, "elsewhere")
    os.makedirs(custom_dir, exist_ok=True)
    custom = os.path.join(custom_dir, "mine.sqlite")
    config.set_("library_db", custom)
    check("a deliberate custom path whose directory exists is honoured",
          config.get("library_db") == custom)
    config.set_("library_db", "")

    # ---- config's media-provider list is media's, not a hand-copied one ---------- #
    # The comment CLAIMED it was "kept in sync with media.MEDIA_PROVIDERS" while listing
    # four fewer, so thegamesdb/arcadedb/zxinfo/mobygames were invisible to
    # `config.py enable <provider>` and to the per-provider scope settings.
    import media                                               # noqa: E402
    check("config offers exactly the providers media defines",
          tuple(config.MEDIA_PROVIDERS) == tuple(media.MEDIA_PROVIDERS))
    check("including the four that used to be missing",
          {"thegamesdb", "arcadedb", "zxinfo", "mobygames"} <= set(config.MEDIA_PROVIDERS))

    # ---- estimate: a recorded MISS is not a match -------------------------------- #
    lib = sqlite3.connect(os.path.join(DATA, "game-library.sqlite"))
    lib.execute("CREATE TABLE games (norm_key TEXT, platform TEXT)")
    lib.executemany("INSERT INTO games VALUES (?,?)",
                    [("g%d" % i, "pc") for i in range(100)])
    lib.commit()
    lib.close()

    mc = sqlite3.connect(os.path.join(DATA, "metadata-cache.sqlite"))
    mc.execute("CREATE TABLE ss_resolution (norm_key TEXT PRIMARY KEY, ss_id INTEGER)")
    mc.execute("CREATE TABLE sgdb_resolution (norm_key TEXT PRIMARY KEY, sgdb_id INTEGER)")
    # 10 real identities, 90 recorded misses — a library that badly needs a match pass
    mc.executemany("INSERT INTO ss_resolution VALUES (?,?)",
                   [("g%d" % i, i + 1 if i < 10 else 0) for i in range(100)])
    mc.commit()
    mc.close()

    p = estimate.plan("algo")
    match = [ph for ph in p["phases"] if ph["phase"] == "match"][0]
    check("90 recorded misses still count as 90 games to match", match["games"] == 90)
    check("so the estimate is not 'under a minute' for an hour of work",
          estimate.summary(p) != "under a minute")

    # ---- check_invariants: I11 judges against EVERY platform of the game ---------- #
    # A game owned on Genesis and Windows has two catalog rows and ONE ss_resolution
    # record. Picking an arbitrary row and comparing against it is a coin flip.
    src = open(os.path.join(DIR, "ludodex", "check_invariants.py"),
               encoding="utf-8").read()
    check("I11 no longer takes an arbitrary platform row",
          "SELECT platform, canonical_title FROM games WHERE norm_key=?" not in src)
    check("it reads every platform the norm_key is owned on",
          "SELECT DISTINCT platform FROM games WHERE norm_key=?" in src)
    check("and only flags a match that fits NONE of them", "got not in want" in src)
    check("the module no longer hardcodes /data", '"/data"' not in src)
    check("or /app", '"/app"' not in src)

    # ---- cf_access: a broken setup says so, a bad token does not ------------------ #
    import cf_access                                            # noqa: E402
    cf_access._logged.clear()
    import io
    import contextlib

    import jwt

    # A JWKS endpoint that cannot be reached. NOT a network call: _client is replaced
    # with the failure the real one would raise. PyJWKClientConnectionError inherits
    # from PyJWTError, so an `except PyJWTError` written first would file this under
    # "bad token" and the silence would be back.
    real_client = cf_access._client

    def unreachable(team_domain):
        raise jwt.exceptions.PyJWKClientConnectionError("Fail to fetch data from the url")

    cf_access._client = unreachable
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = cf_access.verify_email("a.b.c", "typo.cloudflareaccess.invalid", "aud")
        check("a verification failure still returns None (never fail open)", got is None)
        said = err.getvalue()
        check("but a broken setup is reported", bool(said.strip()))
        check("naming the team domain the admin has to fix",
              "typo.cloudflareaccess.invalid" in said)
        check("and it does not call an unreachable key set a bad token",
              "rejected" not in said)

        err2 = io.StringIO()
        with contextlib.redirect_stderr(err2):
            cf_access.verify_email("a.b.c", "typo.cloudflareaccess.invalid", "aud")
        check("and it is said ONCE, not on every unauthenticated request",
              err2.getvalue().strip() == "")
    finally:
        cf_access._client = real_client

    # An ordinary bad token is still just "not this user".
    err3 = io.StringIO()
    with contextlib.redirect_stderr(err3):
        check("a malformed token returns None",
              cf_access.verify_email("not.a.jwt", "team.cloudflareaccess.invalid",
                                     "aud") is None)
    check("reported as a token problem, not a setup one",
          "rejected" in err3.getvalue())

    # ---- reset: it cannot reset the remote, and now says so ---------------------- #
    config.set_("backingstore_backend", "")
    check("with no backing store there is nothing to warn about",
          reset.plan("curation")["warnings"] == [])
    config.set_("backingstore_backend", "pocketbase")
    config.set_("backingstore_auto_minutes", "15")
    warn = reset.plan("curation")["warnings"]
    check("a configured backing store produces a warning", len(warn) == 1)
    check("it names the shadow whose loss causes the re-pull",
          "sync_cache.sqlite" in warn[0])
    check("and says the rows come back on the timer", "15 minute" in warn[0])
    check("the library scope is not warned about — it curates nothing",
          reset.plan("library")["warnings"] == [])
    config.set_("backingstore_backend", "")

    # ---- aimeta: what a sidecar may put into a paid prompt ------------------------ #
    d = os.path.join(DATA, "roms")
    os.makedirs(d, exist_ok=True)
    ok = write(os.path.join(d, "readme.txt"), "Sonic the Hedgehog 2 (USA) — Rev 01 dump")
    check("a release note is still included",
          (aimeta._sidecar_text(ok, "readme.txt") or "").startswith("Sonic"))

    secret_name = write(os.path.join(d, "api_key.txt"), "just some text")
    check("a file whose NAME announces a secret is skipped",
          aimeta._sidecar_text(secret_name, "api_key.txt") is None)

    secret_body = write(os.path.join(d, "notes.txt"),
                        "scraper setup\napi_key = abcdef123456\n")
    check("a credential-shaped line rejects the whole snippet",
          aimeta._sidecar_text(secret_body, "notes.txt") is None)

    blob = write(os.path.join(d, "info.txt"),
                 "token: " + "A" * 40)
    check("a long opaque value is treated as a credential",
          aimeta._sidecar_text(blob, "info.txt") is None)

    with open(os.path.join(d, "fake.txt"), "wb") as f:
        f.write(b"\x00\x01\x02binary")
    check("a binary file wearing .txt is not quoted",
          aimeta._sidecar_text(os.path.join(d, "fake.txt"), "fake.txt") is None)

    long = write(os.path.join(d, "long.nfo"), "Mega Man X" + ("!" * 2000))
    check("the quoted slice is bounded",
          len(aimeta._sidecar_text(long, "long.nfo") or "") <= 500)

    check("configuration extensions are no longer sidecars",
          not ({"json", "ini", "xml", "dat"} & aimeta._SIDECAR_EXTS))
    check("the describing ones still are",
          {"nfo", "txt", "md"} <= aimeta._SIDECAR_EXTS)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
