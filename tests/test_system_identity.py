#!/usr/bin/env python3
"""What a provider told us about the SYSTEM is worth writing down.

The shared identity layer already records the matched record's YEAR, for a stated
reason: "or every audit of it means asking the provider again". The system is the same
kind of fact and was the same kind of gap.

ScreenScraper keeps one record PER SYSTEM, so the system is part of the identity, not a
detail of it. A record for another system is a different RELEASE — the 2008 PSN port of
a 1998 PS1 game, the PC Windows build of a 1993 Genesis one — carrying that release's
art and metadata. Live 2026-08-07, four such matches were sitting in the library.

Two of them (Crash Bandicoot 3, Phantasy Star IV) were caught by the ERA invariant,
because a re-release has a later date. The other two were not, and could not be:

  Shinobi III        genesis -> a PC Windows record with NO dates at all
  Soul Reaver        ps1     -> a PC Windows record dated 1999, the same year as the
                               PS1 release

An era test cannot see a wrong system when the years agree. So the system needs its own
recorded fact and its own invariant, exactly as the year got one.

Same discipline throughout: an absent statement is not evidence. A row with no recorded
system, or an entry whose platform ScreenScraper has no system for (PC), is not a
violation — it is simply unjudged.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-sysidentity-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    import provider_ids

    con = sqlite3.connect(os.path.join(D, "metadata-cache.sqlite"))
    provider_ids.ensure_tables(con)

    cols = {r[1] for r in con.execute("PRAGMA table_info(ss_resolution)")}
    check("the identity row can hold the matched record's system", "system" in cols)

    # a search result carrying a system must persist it
    def search_ps3(title, systems):
        return {"ss_id": 25279, "name": "Crash Bandicoot 3 : Warped", "year": 2008,
                "system": "59"}

    provider_ids.resolve(con, "screenscraper", "crash bandicoot 3 warped",
                         "Crash Bandicoot 3: Warped", ["ps1"], search_ps3)
    row = con.execute("SELECT ss_id, system FROM ss_resolution WHERE norm_key=?",
                      ("crash bandicoot 3 warped",)).fetchone()
    check("the system the provider reported is written down", row[1] == "59")

    # a provider that says nothing about the system leaves it unrecorded, not blank-true
    def search_quiet(title, systems):
        return {"ss_id": 7, "name": "Something", "year": 1990}

    provider_ids.resolve(con, "screenscraper", "quiet", "Something", ["nes"],
                         search_quiet)
    row = con.execute("SELECT system FROM ss_resolution WHERE norm_key=?",
                      ("quiet",)).fetchone()
    check("a provider that does not say leaves it NULL", row[0] is None)
    con.commit()
    con.close()

    # --- the invariant itself, asserted on what the checker actually reports ---------
    import subprocess
    import media_index

    lib = sqlite3.connect(os.path.join(D, "game-library.sqlite"))
    lib.executescript("""
        CREATE TABLE IF NOT EXISTS games(
          id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT, platform TEXT,
          entry_key TEXT, base_key TEXT, game_key TEXT, n_sources INTEGER,
          n_kinds INTEGER, sources_summary TEXT, wanted INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sources(game_id INTEGER, source TEXT, platform TEXT,
          source_id TEXT, title_raw TEXT, detail TEXT, state TEXT, via_collection TEXT);
        CREATE TABLE IF NOT EXISTS game_attributes(game_id INTEGER, kind TEXT, value TEXT);
        CREATE TABLE IF NOT EXISTS metadata_links(game_id INTEGER, provider TEXT,
          provider_id TEXT, slug TEXT);
        CREATE TABLE IF NOT EXISTS identity_review(norm_key TEXT, reason TEXT, detail TEXT);
    """)
    for nk, title, plat in (("soul reaver", "Soul Reaver", "ps1"),
                            ("sonic", "Sonic", "genesis"),
                            ("a pc game", "A PC Game", "pc")):
        lib.execute("INSERT INTO games(canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,n_sources,n_kinds,sources_summary,wanted) "
                    "VALUES(?,?,?,?,?,?,1,0,'steam',0)",
                    (title, nk, plat, nk + "@" + plat, nk, "title:" + nk))
    lib.commit()
    lib.close()
    media_index.index_con().close()

    md = sqlite3.connect(os.path.join(D, "metadata-cache.sqlite"))
    md.execute("DELETE FROM ss_resolution")
    # `system` holds ScreenScraper's own numeric system id (135 = PC Windows here)
    for nk, sid, sysname in (("soul reaver", 313298, "135"),     # ps1 entry, PC record
                             ("sonic", 5, "1"),                  # genesis record: right
                             ("a pc game", 999, "58")):          # pc: nothing to fit
        md.execute("INSERT INTO ss_resolution(norm_key,ss_id,name,matched_by,"
                   "resolved_at,system) VALUES(?,?,?,'search',0,?)",
                   (nk, sid, nk.title(), sysname))
    md.commit()
    md.close()

    env = dict(os.environ, LUDODEX_DATA=D)
    out = subprocess.run([sys.executable, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "ludodex", "check_invariants.py")],
        capture_output=True, text=True, env=env).stdout
    i11 = [ln for ln in out.splitlines() if "I11" in ln]
    check("the checker reports an I11", bool(i11))
    check("the wrong-system match is flagged",
          any("VIOLATED" in ln for ln in i11) and "soul reaver" in out)
    check("a correct-system match is not flagged", "sonic" not in out)
    check("a pc entry is not flagged — no ScreenScraper system to disagree with",
          "a pc game" not in out)

    print("\n  %d/%d passed" % (sum(1 for _, c in PASS if c), len(PASS)))


if __name__ == "__main__":
    main()
