#!/usr/bin/env python3
"""The fresh-ingest guarantee: identity arriving LATE must still end up visible.

This is the question datbird actually asked — not "is the repair correct" but "will a
first-run ingest come out right without anyone running a repair by hand". Every wrong-art
report in this project has the same shape underneath: a fact is derived at fetch time,
something changes it afterwards, and nothing re-derives it before the art is chosen.

Two things have to hold, and unit tests on the individual functions prove neither:

  1. BEHAVIOUR — media fetched before a game has an identity must become visible once the
     identity arrives, with no manual step.
  2. ORDERING — the repair has to run before the selection on every path that fetches.
     It does today; this fails the build if someone reorders it, which is exactly how the
     property would be lost.
"""
import os
import re
import sqlite3
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-order-")

import media_choose                              # noqa: E402
import media_fetch                               # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _serve_resolves(m, base, platform, gkey, kind):
    """The serve path's own rule (DESIGN §11.4/§11.9): own-console art, or neutral art
    whose identity matches the entry. If this finds nothing the user sees a monogram."""
    return m.execute(
        "SELECT COUNT(*) FROM media WHERE kind=? AND chosen=1 AND ("
        "(norm_key=? AND COALESCE(system,'')=?) "
        "OR (COALESCE(system,'')='' AND game_key=?))",
        (kind, base, platform, gkey)).fetchone()[0]


def main():
    print("1. media fetched BEFORE identity becomes visible once identity arrives")
    lib = sqlite3.connect(os.path.join(D, "game-library.sqlite"))
    lib.execute("CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT, "
                "base_key TEXT, platform TEXT, game_key TEXT, canonical_title TEXT)")
    lib.execute("INSERT INTO games(norm_key,base_key,platform,game_key,canonical_title) "
                "VALUES('late','late','pc','title:late','Late Identity')")
    lib.commit()

    m = sqlite3.connect(os.path.join(D, "media-index.sqlite"))
    m.execute("CREATE TABLE media(id INTEGER PRIMARY KEY, norm_key TEXT, system TEXT, "
              "game_key TEXT, kind TEXT, provider TEXT, ref TEXT, ref_type TEXT, "
              "ext TEXT, matched INT DEFAULT 1, sha1 TEXT, width INT, height INT, "
              "filler INT, ai_pick INT, meta TEXT, chosen INT DEFAULT 0, hidden INT DEFAULT 0)")
    # a fetch that happened while the game was still unidentified: neutral, title-keyed
    m.execute("INSERT INTO media(norm_key,system,game_key,kind,provider,ref,ref_type,"
              "width,height) VALUES('late','','title:late','cover','igdb',"
              "'http://x/c.jpg','url',600,900)")
    m.execute("INSERT INTO media(norm_key,system,game_key,kind,provider,ref,ref_type) "
              "VALUES('late','','title:late','screenshot','igdb','http://x/s.jpg','url')")
    m.commit()

    media_fetch._backfill_game_key(m)
    media_choose.select(m)
    check("while unidentified, the entry can see its own cover",
          _serve_resolves(m, "late", "pc", "title:late", "cover") == 1)

    # identity arrives — a wand match, an accepted finding, a member ingest
    lib.execute("UPDATE games SET game_key='igdb:4242' WHERE base_key='late'")
    lib.commit()

    # the pipeline's own tail, in the order every media path runs it
    media_fetch._backfill_game_key(m)
    media_choose.select(m)
    check("after identity moves, the cover is STILL visible",
          _serve_resolves(m, "late", "pc", "igdb:4242", "cover") == 1)
    check("and so are the screenshots",
          m.execute("SELECT COUNT(*) FROM media WHERE norm_key='late' AND "
                    "kind='screenshot' AND game_key='igdb:4242'").fetchone()[0] == 1)

    print("2. and it survives identity moving a SECOND time")
    lib.execute("UPDATE games SET game_key='igdb:5555' WHERE base_key='late'")
    lib.commit()
    media_fetch._backfill_game_key(m)
    media_choose.select(m)
    check("art follows the entry again", _serve_resolves(m, "late", "pc", "igdb:5555",
                                                         "cover") == 1)

    print("3. and when identity is REVOKED back to a title key")
    # bundle refusal, or a match the user rejected
    lib.execute("UPDATE games SET game_key='title:late' WHERE base_key='late'")
    lib.commit()
    media_fetch._backfill_game_key(m)
    media_choose.select(m)
    check("art follows the entry back", _serve_resolves(m, "late", "pc", "title:late",
                                                        "cover") == 1)
    print("3b. a catalog BUILD reconciles media identity with no manual step")
    # Sections 1-3 call the repair by hand, which proves it works but not that anything
    # RUNS it. The event that moves an identity library-wide is the catalog build:
    # build_library computes the bundle and many-to-one refusals, so the instant it swaps
    # the new catalog in, every media row stamped from `igdb_resolution` may disagree
    # with the entry it belongs to. The repair was wired only to MEDIA events (fetch,
    # finish, hydrate), so for a game nobody re-fetches afterwards the stamp stayed stale
    # forever. Live, that was Fallout 76 + its Public Test Server: two owned Steam apps
    # on IGDB 103020, refused as many_to_one, 10 media rows left on `igdb:103020` while
    # both entries read `title:<nk>` — the PTS holding a cover it could not show.
    lib.execute("UPDATE games SET game_key='igdb:9001' WHERE base_key='late'")
    lib.commit()
    m.execute("UPDATE media SET game_key='igdb:8888' WHERE norm_key='late'")
    m.commit()
    # a candidate that landed AFTER the run's last selection — nothing has elected it
    m.execute("INSERT INTO media(norm_key,system,game_key,kind,provider,ref,ref_type,"
              "chosen) VALUES('late','','title:late','logo','steam','http://x/l.png',"
              "'url',0)")
    m.commit()
    m.close()                                   # the build reconciles on its OWN handle
    media_fetch.reconcile_after_build()         # <- no repair or select call by hand
    m = sqlite3.connect(os.path.join(D, "media-index.sqlite"))
    check("after a build moves identity, media follows without anyone calling the repair",
          _serve_resolves(m, "late", "pc", "igdb:9001", "cover") == 1)
    check("and no row is left on the identity the build revoked",
          m.execute("SELECT COUNT(*) FROM media WHERE norm_key='late' AND "
                    "game_key='igdb:8888'").fetchone()[0] == 0)
    check("a lone candidate that arrived after the last selection is elected",
          m.execute("SELECT chosen FROM media WHERE norm_key='late' AND "
                    "kind='logo'").fetchone()[0] == 1)

    lib.close()
    m.close()

    print("3c. and a build with no media index yet is a no-op, not a crash")
    # First run: the catalog exists before anything has ever fetched art.
    _idx = os.path.join(D, "media-index.sqlite")
    _stash = _idx + ".stashed"
    os.rename(_idx, _stash)
    try:
        media_fetch.reconcile_after_build()
        check("fresh install: reconcile with no media index does nothing quietly", True)
    finally:
        os.rename(_stash, _idx)

    print("4. every media path repairs identity BEFORE it chooses")
    # The behaviour above only holds because the repair runs first. Nothing enforces
    # that but convention, and convention is what drifted in the first place.
    src = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read().splitlines()
    offenders = []
    for i, line in enumerate(src):
        if not re.search(r"media_choose\.select\(", line):
            continue
        window = "\n".join(src[max(0, i - 12):i])
        if "_backfill_game_key" in window:
            continue
        # A re-rank after measuring existing rows is not a fetch: no new rows exist, so
        # there is no new stamp to reconcile. Two forms are exempt — a scoped re-rank
        # (`only=`), and one explicitly declared with a NO-STAMP: comment above it. The
        # marker is deliberate: an exemption should be something someone wrote down and a
        # reviewer can see, not something that fell out of how far apart two lines are.
        if "only=" in line:
            continue
        if "NO-STAMP:" in "\n".join(src[max(0, i - 4):i]):
            continue
        offenders.append("server/app.py:%d  %s" % (i + 1, line.strip()[:70]))
    check("no fetch path selects without repairing identity first: %s"
          % (offenders or "none"), not offenders)

    print("5. the catalog build reconciles media AFTER the swap")
    # Source-level for the same reason section 4 is: the failure mode is a code path that
    # never learned about a function. It has to come after `os.replace(TMP, OUT)` — before
    # the swap the repair would read the OLD catalog and faithfully re-key media to the
    # identities the build is in the middle of replacing.
    bl = open(os.path.join(DIR, "build_library.py"), encoding="utf-8").read()
    swap = bl.find("os.replace(TMP, OUT)")
    rec = bl.find("reconcile_after_build")
    check("build_library calls reconcile_after_build", rec != -1)
    check("and calls it AFTER the atomic swap, not before",
          swap != -1 and rec > swap)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
