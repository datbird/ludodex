#!/usr/bin/env python3
"""The media stamp must follow the ENTRY's identity — one derivation, not two.

Neutral (platform-agnostic) art only serves when `media.game_key = games.game_key`
(DESIGN §11.9). Two independent places decided what that key should be:

  * the catalog, which stamps the entry, and
  * `_backfill_game_key`, which re-derived identity from `igdb_resolution` and applied
    its OWN policy — refusing to stamp anything whose IGDB `game_type` is 3/13 (bundle
    or pack).

They disagreed, and the disagreement was invisible. Live, 41 entries carried
`game_key='igdb:<id>'` while every one of their 990 neutral media rows was still stamped
`title:<base_key>`: Halo MCC, Crash Bandicoot N. Sane Trilogy, Contra and Castlevania
Anniversary Collections, the D&D and Forgotten Realms series. The serve resolver hid all
of it, so those entries showed Screenshots 0 / Videos 0 / Manuals 0 while holding 15-40
screenshots — and still rendered a cover, because own-console ScreenScraper art matches on
norm_key+system and never consults game_key at all.

The fix is not a better policy, it is one fewer policy: read the entry. The bundle
refusal then needs no special case here, because the entry already encodes it.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-gamekey-")

import media_fetch                               # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    lib = sqlite3.connect(os.path.join(D, "game-library.sqlite"))
    lib.execute("CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT, "
                "base_key TEXT, platform TEXT, game_key TEXT)")

    def entry(bk, gk, plat="pc"):
        lib.execute("INSERT INTO games(norm_key,base_key,platform,game_key) "
                    "VALUES(?,?,?,?)", (bk, bk, plat, gk))

    entry("mcc", "igdb:7348")            # a COLLECTION that took its own identity
    entry("refused", "title:refused")    # a bundle whose identity the catalog refused
    entry("plain", "igdb:99")            # an ordinary identified game
    entry("nameless", None)              # no identity at all
    entry("split", "igdb:1", "pc")       # two entries that DISAGREE -> ambiguous
    entry("split", "igdb:2", "genesis")
    lib.commit()
    lib.close()

    mcache = sqlite3.connect(os.path.join(D, "metadata-cache.sqlite"))
    mcache.execute("CREATE TABLE igdb_resolution(norm_key TEXT PRIMARY KEY, "
                   "igdb_id INTEGER, slug TEXT, matched_by TEXT, resolved_at INTEGER)")
    mcache.execute("CREATE TABLE igdb_meta(igdb_id INTEGER PRIMARY KEY, payload_json TEXT)")
    # 7348 is a BUNDLE by IGDB's reckoning — the exact rows the old policy refused
    mcache.execute("INSERT INTO igdb_meta VALUES(7348, '{\"game_type\": 3}')")
    mcache.execute("INSERT INTO igdb_meta VALUES(99, '{\"game_type\": 0}')")
    mcache.execute("INSERT INTO igdb_resolution VALUES('mcc',7348,NULL,'name',1)")
    mcache.execute("INSERT INTO igdb_resolution VALUES('refused',7348,NULL,'name',1)")
    mcache.execute("INSERT INTO igdb_resolution VALUES('plain',99,NULL,'name',1)")
    mcache.commit()
    mcache.close()

    con = sqlite3.connect(os.path.join(D, "media-index.sqlite"))
    con.execute("CREATE TABLE media(id INTEGER PRIMARY KEY, norm_key TEXT, system TEXT, "
                "game_key TEXT, kind TEXT, provider TEXT, ref TEXT, ref_type TEXT, "
                "sha1 TEXT, width INT, height INT, filler INT, ai_pick INT, "
                "chosen INT DEFAULT 0, hidden INT DEFAULT 0)")

    def put(nk, gk, system="", kind="screenshot"):
        con.execute("INSERT INTO media(norm_key,system,game_key,kind,provider,ref,"
                    "ref_type) VALUES(?,?,?,?,'igdb','http://x/a.jpg','url')",
                    (nk, system, gk, kind))
        return con.execute("SELECT last_insert_rowid()").fetchone()[0]

    i_mcc = put("mcc", "title:mcc")
    i_mcc_own = put("mcc", "title:mcc", system="pc")     # own-console: never uses game_key
    i_refused = put("refused", "title:refused")
    i_plain = put("plain", "title:plain")
    i_nameless = put("nameless", "title:nameless")
    i_split = put("split", "title:split")
    i_null = put("orphan", None)
    con.commit()

    media_fetch._backfill_game_key(con)

    def gk_of(i):
        return con.execute("SELECT game_key FROM media WHERE id=?", (i,)).fetchone()[0]

    print("1. a COLLECTION that took an igdb identity gets its media moved with it")
    check("the bundle exclusion no longer strands 990 rows", gk_of(i_mcc) == "igdb:7348")

    print("2. an entry that REFUSED a bundle identity keeps the title key")
    # Same igdb_id 7348, same game_type 3 — but the CATALOG said title:refused, and the
    # catalog is the one source now, so no special case is needed to honour that.
    check("the refusal is respected because the entry is what's read",
          gk_of(i_refused) == "title:refused")

    print("3. an ordinary identified game is stamped as before")
    check("plain game takes its igdb key", gk_of(i_plain) == "igdb:99")

    print("4. an entry with no identity keeps the title bucket")
    check("nameless stays title:", gk_of(i_nameless) == "title:nameless")

    print("5. entries that DISAGREE are left alone rather than guessed at")
    # One stamp per norm_key cannot satisfy two identities; picking one would silently
    # hide the other entry's art. Today no base_key is ambiguous, but the rule has to
    # hold the moment one is.
    check("an ambiguous base_key is skipped", gk_of(i_split) == "title:split")

    print("6. own-console art is untouched (it matches on norm_key+system)")
    check("system-scoped row keeps its stamp", gk_of(i_mcc_own) == "title:mcc")

    print("7. an unstamped row with no catalog entry still gets the title bucket")
    check("orphan filled", gk_of(i_null) == "title:orphan")

    print("8. idempotent")
    media_fetch._backfill_game_key(con)
    media_fetch._backfill_game_key(con)
    check("mcc stable", gk_of(i_mcc) == "igdb:7348")
    check("refused stable", gk_of(i_refused) == "title:refused")
    check("split stable", gk_of(i_split) == "title:split")

    con.close()
    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
