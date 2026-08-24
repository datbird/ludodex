#!/usr/bin/env python3
"""Art fetched for an identity that is no longer current must not survive (#31).

A game gets re-identified all the time — a wand match, an accepted finding, a member
ingest. `fetch_igdb` only ever UPSERTED, so the PREVIOUS igdb id's rows stayed in the
index under the same norm_key (contrast `fetch_screenscraper`, which does a scoped
delete before it re-emits). The "catalog decides, the stamp follows" repair in
`_backfill_game_key` then re-keyed every NEUTRAL row for that norm_key to the entry's
new game_key, regardless of provider and regardless of `meta` — which for an igdb row
holds the very igdb id the art was fetched for.

So the OLD game's cover was handed the NEW game's identity and became a legitimate
candidate. It does not merely sit there: it competes, and `res_band` sits above provider
priority, so the wrong game's 600x900 cover beats the right game's 264x352 one and is
served as the cover.

Two changes, one rule — the row's own provider evidence outranks a blanket re-stamp:

  * a fetch drops the rows whose igdb id is no longer one of this title's, and
  * the repair refuses to adopt (and drops) a row whose `meta` names a DIFFERENT igdb
    id than the entry's identity.

Positive evidence only. A row with no `meta` at all proves nothing about its identity,
so it is still adopted — a miss must not be read as consent in EITHER direction.

Offline. IGDB is a stub module; no network, no creds.
"""
import os
import sqlite3
import sys
import time
import types

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-staleid-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import config                                                  # noqa: E402
import media_fetch                                             # noqa: E402
import media_index                                             # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


NK = "contra hard corps"

# What IGDB would answer for each id. 1001 is the game this title used to be thought to
# be; 2002 is what it actually is.
GAMES = {
    1001: {"id": 1001, "cover": {"image_id": "oldcover"},
           "screenshots": [{"image_id": "oldshot"}]},
    2002: {"id": 2002, "cover": {"image_id": "newcover"},
           "screenshots": [{"image_id": "newshot"}]},
}


def _stub_igdb():
    m = types.ModuleType("igdb")
    m.get_token = lambda cid, csec: ("tok", 0)

    def query(endpoint, body, cid, tok):
        import re as _re
        want = _re.search(r"where id=\(([^)]*)\)", body)
        ids = [int(x) for x in (want.group(1).split(",") if want else []) if x.strip()]
        return [GAMES[i] for i in ids if i in GAMES]
    m.query = query
    sys.modules["igdb"] = m


def _resolution(iid):
    p = os.path.join(DATA, "metadata-cache.sqlite")
    mc = sqlite3.connect(p)
    mc.execute("CREATE TABLE IF NOT EXISTS igdb_resolution(norm_key TEXT PRIMARY KEY, "
               "igdb_id INTEGER)")
    mc.execute("INSERT OR REPLACE INTO igdb_resolution VALUES(?,?)", (NK, iid))
    mc.commit()
    mc.close()
    media_fetch.invalidate_resmap()


def rows(con):
    return [dict(zip(("id", "kind", "provider", "meta", "game_key", "ref"), r))
            for r in con.execute("SELECT id,kind,provider,meta,game_key,ref FROM media "
                                 "ORDER BY id")]


def main():
    _stub_igdb()
    config.igdb_creds = lambda: ("cid", "csec")

    print("1. a re-identified game does not keep the old id's art")
    con = media_index.index_con()
    now = int(time.time())
    _resolution(1001)
    media_fetch.fetch_igdb(con, now)
    con.commit()
    metas = {r["meta"] for r in rows(con)}
    check("the first fetch stored art for igdb 1001", metas == {"1001"})
    check("its cover url names the old image",
          any("oldcover" in r["ref"] for r in rows(con)))

    # the wand re-identifies the title
    _resolution(2002)
    media_fetch.fetch_igdb(con, now + 1, only={NK})
    con.commit()
    after = rows(con)
    check("the new identity's art arrived",
          any(r["meta"] == "2002" and "newcover" in r["ref"] for r in after))
    check("NOTHING from the superseded identity survives",
          not [r for r in after if r["meta"] == "1001"])
    check("and that includes its screenshots, not just the cover",
          not [r for r in after if "oldshot" in r["ref"]])

    print("2. an unrelated title's art is untouched by a scoped fetch")
    con.execute("INSERT INTO media(norm_key,system,kind,provider,ref_type,ref,ext,"
                "meta,matched,indexed_at) VALUES('other game','','cover','igdb','url',"
                "'https://images.igdb.com/x/othercover.jpg','jpg','1001',1,0)")
    con.commit()
    media_fetch.fetch_igdb(con, now + 2, only={NK})
    con.commit()
    check("a game outside `only` keeps its rows",
          any("othercover" in r["ref"] for r in rows(con)))
    con.close()

    print("3. the identity repair will not adopt art that names another igdb id")
    lib = sqlite3.connect(os.path.join(DATA, "game-library.sqlite"))
    lib.execute("CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT, "
                "base_key TEXT, platform TEXT, game_key TEXT)")
    lib.execute("INSERT INTO games(norm_key,base_key,platform,game_key) "
                "VALUES(?,?,'pc','igdb:2002')", (NK, NK))
    lib.commit()
    lib.close()

    con = media_index.index_con()
    con.execute("DELETE FROM media")

    def put(provider, meta, gk, ref, system=""):
        con.execute("INSERT INTO media(norm_key,system,kind,provider,ref_type,ref,ext,"
                    "meta,game_key,matched,indexed_at) VALUES(?,?,'cover',?,'url',?,"
                    "'jpg',?,?,1,0)", (NK, system, provider, ref, meta, gk))
        return con.execute("SELECT last_insert_rowid()").fetchone()[0]

    stale = put("igdb", "1001", "igdb:1001", "https://x/old.jpg")
    current = put("igdb", "2002", "title:" + NK, "https://x/new.jpg")
    silent = put("screenscraper", None, "title:" + NK, "https://x/ss.jpg")
    console = put("igdb", "1001", "igdb:1001", "https://x/oldc.jpg", system="genesis")
    con.commit()

    media_fetch._backfill_game_key(con)

    def gk(i):
        r = con.execute("SELECT game_key FROM media WHERE id=?", (i,)).fetchone()
        return r[0] if r else "<gone>"

    check("the row naming the superseded id is dropped, not re-stamped",
          gk(stale) == "<gone>")
    check("the row naming the CURRENT id is stamped with the entry's key",
          gk(current) == "igdb:2002")
    check("a row with no id evidence is still adopted (a miss is not consent)",
          gk(silent) == "igdb:2002")
    check("own-console art is left alone — it never consults game_key",
          gk(console) == "igdb:1001")

    print("4. idempotent")
    media_fetch._backfill_game_key(con)
    check("current stable", gk(current) == "igdb:2002")
    check("silent stable", gk(silent) == "igdb:2002")
    con.close()

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
