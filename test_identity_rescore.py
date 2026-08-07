#!/usr/bin/env python3
"""Contract test for provider_ids.rescore — re-deciding identities under today's gate.

Offline, no network, no AI, no live DBs.

The defect it exists for: an acceptance rule that gets STRICTER leaves everything it
would now refuse sitting in the cache, indistinguishable from a match it would make.
Nothing re-judges a decision already written down, so the library keeps binds no fresh
ingest would produce — live, 93 ScreenScraper identities including `Deathmatch Classic`
holding DmC: Devil May Cry.

The dangerous half is the opposite direction. A scrub that judges the WRONG identities
deletes correct data at scale, so the exemptions are the point of this file:

  1. SCRUB     — a name-searched identity today's gate refuses is cleared.
  2. KEEP      — a name-searched identity it still accepts is untouched.
  3. APPID     — `steam_appid` is ownership, never judged on names (a re-titled SKU or a
                 DLC legitimately resolves to its parent record).
  4. MANUAL    — a person's decision is never re-judged (#25).
  5. MISS      — already the absence of a decision; nothing to re-judge.
  6. ALIASES   — a regional title must not be refused for failing to match under the
                 name we happen to store.
  7. DRY RUN   — reports without deleting.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import provider_ids                                     # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def fixture():
    lib = sqlite3.connect(":memory:")
    lib.execute("CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT, "
                "base_key TEXT, canonical_title TEXT, game_key TEXT)")
    lib.execute("CREATE TABLE sources(game_id INT, source TEXT)")
    lib.execute("CREATE TABLE game_attributes(game_id INT, kind TEXT, value TEXT)")
    cache = sqlite3.connect(":memory:")
    provider_ids.ensure_tables(cache)
    return lib, cache


def add(lib, cache, nk, title, pid, how, cand, provider="screenscraper"):
    lib.execute("INSERT INTO games(norm_key,base_key,canonical_title,game_key) "
                "VALUES(?,?,?,?)", (nk, nk, title, "title:" + nk))
    lib.commit()
    table, idcol = provider_ids.PROVIDERS[provider]
    cache.execute("INSERT INTO %s(norm_key,%s,name,matched_by,resolved_at) "
                  "VALUES(?,?,?,?,0)" % (table, idcol), (nk, pid, cand, how))
    cache.commit()


def held(cache, nk, provider="screenscraper"):
    table, idcol = provider_ids.PROVIDERS[provider]
    r = cache.execute("SELECT %s FROM %s WHERE norm_key=?" % (idcol, table),
                      (nk,)).fetchone()
    return r[0] if r else None


def main():
    lib, cache = fixture()
    # 1 + 2: a wrong bind and a right one, both established by search
    add(lib, cache, "deathmatch classic", "Deathmatch Classic", 111, "search",
        "DmC : Devil May Cry - Definitive Edition")
    add(lib, cache, "contra hard corps", "Contra: Hard Corps", 69, "search",
        "Contra : Hard Corps")
    # 3: ownership — a re-titled store SKU pointing at its parent record
    add(lib, cache, "doom plus doom 2", "DOOM + DOOM II", 222, "steam_appid",
        "The Ultimate Doom", provider="igdb")
    # 4: a person decided
    add(lib, cache, "hand picked", "Hand Picked", 333, "manual", "Something Else Here")
    # 5: a recorded miss
    add(lib, cache, "never found", "Never Found", 0, "none", None)

    print("dry run reports without changing anything")
    res = provider_ids.rescore(cache, lib, apply=False)
    names = {nk for _p, nk, _t, _c in res["refused"]}
    check("the wrong bind is reported", "deathmatch classic" in names)
    check("nothing was cleared", res["cleared"] == 0)
    check("and it is still in the cache", held(cache, "deathmatch classic") == 111)

    print("apply clears only what today's gate refuses")
    res = provider_ids.rescore(cache, lib, apply=True)
    check("wrong bind CLEARED", held(cache, "deathmatch classic") is None)
    check("correct name-searched match KEPT", held(cache, "contra hard corps") == 69)
    check("steam_appid identity KEPT — ownership is not judged on names",
          held(cache, "doom plus doom 2", provider="igdb") == 222)
    check("manual identity KEPT — a person decided",
          held(cache, "hand picked") == 333)
    check("a recorded miss is left alone", held(cache, "never found") == 0)
    check("cleared count matches what was reported", res["cleared"] == 1)

    print("idempotent")
    again = provider_ids.rescore(cache, lib, apply=True)
    check("a second pass clears nothing", again["cleared"] == 0)

    print("aliases rescue a regional title")
    lib2, cache2 = fixture()
    add(lib2, cache2, "contra", "Contra", 77, "search", "Probotector")
    bare = provider_ids.rescore(cache2, lib2, apply=False)
    check("without aliases the regional name is refused", len(bare["refused"]) == 1)
    with_alias = provider_ids.rescore(
        cache2, lib2, aliases_for=lambda nk, t: ["Probotector"], apply=True)
    check("with aliases it is accepted", not with_alias["refused"])
    check("and the identity survives", held(cache2, "contra") == 77)

    print("a broken alias resolver cannot delete anything by accident")
    lib3, cache3 = fixture()
    add(lib3, cache3, "contra", "Contra", 77, "search", "Contra")
    def boom(nk, t):
        raise RuntimeError("alias lookup exploded")
    r3 = provider_ids.rescore(cache3, lib3, aliases_for=boom, apply=True)
    check("the title itself still judges the match", held(cache3, "contra") == 77)
    check("and nothing was cleared", r3["cleared"] == 0)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
