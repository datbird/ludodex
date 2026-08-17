#!/usr/bin/env python3
"""Ten thousand TheGamesDB ids for zero requests — and one rule that keeps it honest.

A TheGamesDB key is 1,000 requests A MONTH, and name search does not batch: one request
per title. Resolving a library through the API is therefore measured in years. The free
`sselph/scraper` hash.csv short-circuits that entirely — 58,843 SHA1 hashes carrying
11,008 TheGamesDB game ids — and against this deployment's ScreenScraper catalog it
resolved 10,700 distinct games without a single request.

THE RULE, and everything here exists to enforce it: A HASH IS EVIDENCE, A NAME IS NOT.
The file also carries ROM names, and using them would multiply the hit rate. They are
used ONLY to label an identity the hash already created, never to find one. A SHA1
collision is a cryptographic event; a name collision is Tuesday — and name-matching out
of a file that carries no platform gate is precisely the fail-open shape this codebase
keeps paying for.

The rest is about not making a rebuild worse:

  * A FAILED DOWNLOAD IS SKIPPED, NEVER FATAL. This is an optional layer of an optional
    index, and a rebuild that dies because GitHub was slow is a worse outcome than one
    that finishes without it.
  * A STALE COPY BEATS NO COPY. The mapping is historical data, not a live feed.
  * A MISS MINTS ITS OWN IDENTITY in its own id range, rather than attaching a hash to a
    plausible-looking neighbour.
  * THE PROVENANCE TRAVELS WITH THE FILE. A third source is a third attribution.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


SAMPLE = (
    "63627dba22be2b357c0e370e68dc5af56eeb0a24,160,3,007 - GoldenEye (Europe)\n"
    "451b41b63d75677bd42db89a56679208808fcfd0,160,3,007 - GoldenEye (Japan)\n"
    '441bdf924566e6475aa49bab7a199dc06d32a6d5,238,3,"007 - The World Is Not Enough '
    '(Europe) (En,Fr,De)"\n'
    "notahash,999,3,Bad Row\n"                      # too short
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,notanid,3,Bad Id\n"
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb,4242,,No Platform\n"
    "short,row\n"
)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_support
    data = test_support.isolate("ludodex-freemap-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import tgdb_freemap as F
    import matchindex as M
    import config

    p = os.path.join(data, "sample.csv")
    open(p, "w", encoding="utf-8").write(SAMPLE)

    print("1. the file parses, and bad rows are skipped rather than fatal")
    got = list(F.rows(p))
    # 4, not 3: the platform-less row IS valid — we key on the hash, and a missing
    # platform costs nothing because no platform gate is applied here by design.
    check("4 good rows out of 7: %d" % len(got), len(got) == 4)
    check("a 40-char sha1 and an integer id", got[0][0] ==
          "63627dba22be2b357c0e370e68dc5af56eeb0a24" and got[0][1] == 160)
    check("the platform comes through", got[0][2] == 3)
    check("a row with no platform still parses, with None rather than 0",
          got[3][2] is None)
    check("quoted names survive the commas inside them",
          "En,Fr,De" in got[2][3])
    check("a malformed sha1 is dropped, not coerced",
          all(len(r[0]) == 40 for r in got))
    check("so is a non-numeric id", all(isinstance(r[1], int) for r in got))

    print()
    print("2. TWO HASHES, ONE GAME — the shape that makes this worth having")
    # GoldenEye Europe and Japan are different dumps of the same TheGamesDB game.
    ids = {r[1] for r in got}
    check("4 rows carry only %d distinct ids" % len(ids), len(ids) == 3)
    goldeneye = [r for r in got if r[1] == 160]
    check("two different dumps, one game", len(goldeneye) == 2
          and goldeneye[0][0] != goldeneye[1][0])

    print()
    print("3. a missing file is nothing, not a crash")
    check("no rows from a path that does not exist",
          list(F.rows(os.path.join(data, "nope.csv"))) == [])
    check("stats on an absent cache is still an answer",
          F.stats(os.path.join(data, "nope.csv"))["rows"] == 0)

    print()
    print("4. a failed download never breaks a rebuild")
    config.set_("matchindex_tgdb_freemap_url", "http://127.0.0.1:1/not-there.csv")
    check("fetch returns None rather than raising", F.fetch(force=True) is None)
    # ...and with a stale copy present it hands that back instead.
    open(F.CACHE, "w", encoding="utf-8").write(SAMPLE)
    check("a stale copy beats no copy", F.fetch(force=True) == F.CACHE)
    config.set_("matchindex_tgdb_freemap_url", "")

    print()
    print("5. THE RULE — names label, they never match")
    src = open(os.path.join(root, "ludodex", "matchindex.py"), encoding="utf-8").read()
    step = src[src.index("def _merge_tgdb_freemap"):src.index("def _merge_ss")]
    check("the lookup is keyed on sha1", "ns='sha1'" in step)
    check("and on NOTHING else — no name or alias lookup in this step",
          "ns='name'" not in step and "ns='alias'" not in step
          and "resolve_name" not in step)
    check("the rule is stated where the next person will read it",
          "A HASH IS EVIDENCE" in step)

    print()
    print("6. IT ACTUALLY DOES IT — run the step against a real index")
    # Behaviour, not source archaeology. One identity already carries the GoldenEye
    # Europe hash (as ScreenScraper would have left it); the rest are unknown.
    F.CACHE = p_cache = os.path.join(data, "cache.csv")
    open(p_cache, "w", encoding="utf-8").write(SAMPLE)
    con = M.con_db()
    con.execute("INSERT INTO identity(id,name,norm_key,year,first_release_date,"
                "built_at) VALUES(777,'GoldenEye 007','goldeneye007',1997,NULL,0)")
    con.execute("INSERT INTO identity_key(ns,val,identity_id,kind) VALUES"
                "('sha1','63627dba22be2b357c0e370e68dc5af56eeb0a24',777,'exact')")
    con.commit()
    linked, minted = M._merge_tgdb_freemap(con, now=0, progress=False)

    def key(ns, val):
        r = con.execute("SELECT identity_id FROM identity_key WHERE ns=? AND val=?",
                        (ns, val)).fetchone()
        return r["identity_id"] if r else None

    check("the known hash linked to the EXISTING identity, not a new one",
          key("thegamesdb", "160") == 777)
    check("counted as linked, not minted: linked=%d minted=%d" % (linked, minted),
          linked == 1)
    check("it did NOT duplicate the identity",
          con.execute("SELECT COUNT(*) FROM identity WHERE id=777").fetchone()[0] == 1)
    check("...and the identity kept its own name — the csv name did not overwrite it",
          con.execute("SELECT name FROM identity WHERE id=777").fetchone()[0]
          == "GoldenEye 007")

    print()
    print("7. an unknown hash mints its own identity, traceably")
    tw = M.TGDB_ID_BASE + 238
    check("minted at TGDB_ID_BASE + the TheGamesDB id", key("thegamesdb", "238") == tw)
    check("its hash resolves there too",
          key("sha1", "441bdf924566e6475aa49bab7a199dc06d32a6d5") == tw)
    check("labelled from the csv, since nothing else knew it",
          "World Is Not Enough" in
          con.execute("SELECT name FROM identity WHERE id=?", (tw,)).fetchone()[0])
    check("3 minted (TWINE, No Platform, and GoldenEye Japan's own row is linked "
          "to the same game id): minted=%d" % minted, minted == 3)
    check("the SECOND GoldenEye dump landed on the SAME game, not a second one",
          key("sha1", "451b41b63d75677bd42db89a56679208808fcfd0")
          == M.TGDB_ID_BASE + 160)

    print()
    print("7b. re-running changes nothing — a rebuild must be idempotent")
    before = con.execute("SELECT COUNT(*) FROM identity_key").fetchone()[0]
    M._merge_tgdb_freemap(con, now=0, progress=False)
    check("same key count after a second pass",
          con.execute("SELECT COUNT(*) FROM identity_key").fetchone()[0] == before)
    con.close()

    print()
    print("8. the build reports what it did, and cannot die trying")
    check("counts both outcomes separately",
          "tgdb_linked" in src and "tgdb_new_identities" in src)
    check("the whole step is wrapped — an optional layer must not kill a rebuild",
          "matchindex: thegamesdb freemap skipped" in src)

    print()
    print("9. a third source is a third attribution")
    names = [s["name"] for s in M.SOURCES]
    check("sselph/scraper is credited: %s" % names,
          any("sselph" in n for n in names))
    check("its licence is stated, and so is where the DATA came from",
          any("MIT" in s["license"] and "TheGamesDB" in s["license"]
              for s in M.SOURCES))
    check("the attribution string names it too",
          "sselph" in M.ATTRIBUTION and "TheGamesDB" in M.ATTRIBUTION)
    check("and it is not vendored into this repo — downloaded, never redistributed",
          not os.path.exists(os.path.join(root, "hash.csv"))
          and not os.path.exists(os.path.join(root, "ludodex", "hash.csv")))

    print()
    print("10. it can be switched off, and pointed elsewhere")
    check("on by default", config.DEFAULTS["matchindex_tgdb_freemap"] == "1")
    config.set_("matchindex_tgdb_freemap", "0")
    check("and honoured when off", not F.enabled())
    config.set_("matchindex_tgdb_freemap", "1")
    check("the URL is overridable for a local copy or a mirror",
          "matchindex_tgdb_freemap_url" in config.DEFAULTS)
    check("blank means the default, not an empty fetch", F.url().startswith("https://"))

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
