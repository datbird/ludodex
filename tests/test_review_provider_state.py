#!/usr/bin/env python3
"""The review page must say WHICH providers matched, not just "no match" (#36).

EVGA Precision X1 came out of the reset with a SteamGridDB id and recorded misses for
IGDB and ScreenScraper — the right answer, since neither catalogues a GPU utility. The
review card said only that no match was found, which invites a reviewer to go fix
something already correct.

Three buckets, because they are three different claims:

  matched      a provider returned an id
  missed       a provider was asked and returned nothing — a recorded MISS, retried
               after its TTL. "We looked" is not "it does not exist."
  unattempted  never asked at all

Collapsing those is the same mistake the negative-cache work was about, one layer up in
the UI instead of in the cache.

Offline.
"""
import os
import sqlite3
import sys

import test_support

PASS = []


def check(l, c):
    PASS.append(c); print("  %s   %s" % ("ok " if c else "FAIL", l))
    if not c:
        sys.exit("FAILED: " + l)


def main():
    d = test_support.isolate("ludodex-revprov-")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "ludodex"))
    from server import app as srv

    mc = sqlite3.connect(os.path.join(d, "metadata-cache.sqlite"))
    mc.executescript("""
    CREATE TABLE ss_resolution(norm_key TEXT PRIMARY KEY, ss_id INT);
    CREATE TABLE sgdb_resolution(norm_key TEXT PRIMARY KEY, sgdb_id INT);
    CREATE TABLE igdb_resolution(norm_key TEXT PRIMARY KEY, igdb_id INT);
    CREATE TABLE tgdb_resolution(norm_key TEXT PRIMARY KEY, tgdb_id INT);
    """)
    # the live EVGA shape: SGDB has it, the two game databases correctly do not
    mc.execute("INSERT INTO sgdb_resolution VALUES('evga', 3580)")
    mc.execute("INSERT INTO ss_resolution VALUES('evga', 0)")
    mc.execute("INSERT INTO igdb_resolution VALUES('evga', 0)")
    mc.execute("INSERT INTO tgdb_resolution VALUES('evga', 0)")
    # a game nobody has been asked about yet
    mc.execute("INSERT INTO ss_resolution VALUES('fresh', 0)")
    mc.commit(); mc.close()

    st = srv._provider_match_state("evga")
    check("a provider that returned an id is reported as matched",
          [m["provider"] for m in st["matched"]] == ["steamgriddb"])
    check("its id comes with it, so the card can link out",
          st["matched"][0]["id"] == "3580")
    check("providers that were asked and found nothing are listed apart",
          sorted(st["missed"]) == ["igdb", "screenscraper", "thegamesdb"])
    # Deliberately derived from provider_ids rather than written out: this assertion is
    # the one that catches a NEW provider being registered without the review page
    # learning to ask about it, and a hardcoded list would stop catching that the moment
    # someone updated it to make the suite green.
    check("no game DATABASE is claimed unattempted when every one was asked",
          [p for p in st["unattempted"] if p != "steam"] == [])

    st = srv._provider_match_state("fresh")
    check("a provider with NO row is unattempted, not a miss",
          sorted(st["unattempted"]) == ["igdb", "steamgriddb", "thegamesdb"]
          and st["missed"] == ["screenscraper"])

    # STEAM is a provider as well as a source — it supplies the appid, the store
    # attributes and the CDN art, so omitting it describes a game as less known than it
    # is. It identifies by appid, never by name search, so there is no Steam "miss".
    lib = sqlite3.connect(os.path.join(d, "game-library.sqlite"))
    lib.executescript("""
    CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY, norm_key TEXT);
    CREATE TABLE IF NOT EXISTS sources(game_id INT, source TEXT, source_id TEXT);
    """)
    # named columns: the real schema may already exist here (the server initialises it
    # on first open), and positional inserts break the moment it gains a column.
    lib.execute("INSERT INTO games(id,norm_key) VALUES(1,'evga')")
    lib.execute("INSERT INTO sources(game_id,source,source_id) VALUES(1,'steam','1043180')")
    lib.execute("INSERT INTO games(id,norm_key) VALUES(2,'romonly')")
    lib.execute("INSERT INTO sources(game_id,source,source_id) VALUES(2,'emulation','x.md')")
    lib.commit(); lib.close()

    st = srv._provider_match_state("evga")
    check("Steam is listed as an identifying provider",
          "steam" in [m["provider"] for m in st["matched"]])
    check("and it is identified BY THE APPID",
          [m["id"] for m in st["matched"] if m["provider"] == "steam"] == ["1043180"])
    check("providers are listed in a stable order",
          [m["provider"] for m in st["matched"]] == sorted(
              m["provider"] for m in st["matched"]))

    st = srv._provider_match_state("romonly")
    check("a game not owned on Steam is never a MISS there",
          "steam" not in st["missed"])
    check("nor UNATTEMPTED — that would promise a lookup ludodex will not make",
          "steam" not in st["unattempted"])
    check("it is INELIGIBLE, with the reason attached",
          [x["provider"] for x in st["ineligible"]] == ["steam"]
          and "not owned" in st["ineligible"][0]["why"])

    st = srv._provider_match_state("never-heard-of-it")
    import provider_ids
    everyone = sorted(set(provider_ids.PROVIDERS) | {"igdb"})
    check("an unknown game is unattempted everywhere, never 'no match'",
          sorted(st["unattempted"]) == everyone
          and not st["matched"] and not st["missed"])
    check("...and Steam is ineligible for it rather than pending",
          [x["provider"] for x in st["ineligible"]] == ["steam"])

    ui2 = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "web", "src", "App.tsx")).read()
    check("the card renders the ineligible reason", "pv?.ineligible?.length" in ui2)

    # the UI must actually render both, and must not have kept the old blanket wording
    ui = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "web", "src", "App.tsx")).read()
    check("the card shows what already matched", "Already matched:" in ui)
    check("the card names what was searched without success",
          "Could not match against:" in ui)
    # the "could not identify" panel is a SECOND place that states this, and it stated
    # it wrongly: EVGA sits in that list holding a SteamGridDB id.
    check("the stuck panel no longer asserts a blanket 'no match, no attributes'",
          "— no match, no attributes." not in ui)
    # scoped to the panel: the phrase legitimately appears in unrelated help text, and
    # a check that cannot tell one screen from another is not a check.
    panel = ui[ui.index("const stuckPanel ="):]
    panel = panel[:panel.index("const allIds")]
    check("nor calls an identified game one the AI 'could not identify'",
          "could not identify" not in panel)
    check("it leads with what IS known, not with a failure",
          "Already identified." in ui and "Identified by" in ui)
    check("identified and unidentified games are separated, not lumped",
          "stuckIdent" in ui and "stuckBare" in ui)
    check("the stuck panel shows each game's provider state instead",
          "g.f.context?.providers" in ui)

    check("the matched chip carries the provider ids in its tooltip",
          "providerLabel(m.provider)}" in ui and "m.id" in ui)

    # --- the cap that hid all of this ------------------------------------------
    # The review response used to attach context only when `len(findings) <= 60`. The
    # first reset produced 68, so every card lost every fact — filename, platform,
    # folder, provenance, current match — at the exact moment a reviewer needed them,
    # because a large batch is harder to judge than a small one, not easier.
    srv_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "server", "app.py")).read()
    # the STATEMENT, not the substring — the fix's own comment quotes the old line, and
    # this is the third time that has tripped a guard here. Check for executable code.
    check("context is no longer withheld from batches over 60 findings",
          not any(l.strip().startswith("if len(findings) <=")
                  for l in srv_src.splitlines()))
    check("the bound is on distinct GAMES, which is where the cost actually is",
          "len(ctx_cache) < CONTEXT_GAME_CAP" in srv_src)
    check("the cap is generous enough for a real reset batch",
          srv.CONTEXT_GAME_CAP >= 200)
    check("and hitting it is REPORTED, not silent",
          '"context_truncated"' in srv_src)

    print("\n%d/%d passed" % (sum(PASS), len(PASS)))


main()
