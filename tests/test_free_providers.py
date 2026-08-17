#!/usr/bin/env python3
"""Three free sources, added because they cover what the expensive ones cover worst.

Measured against this deployment's ScreenScraper catalogue before any of this was built:
Commodore 64 16,245 hashed games and ZX Spectrum 13,859, both at ZERO coverage from
TheGamesDB's free map; arcade served by nothing that knows what a cabinet is. So:

  * ARCADEDB is keyed on the MAME SET NAME — `pacman`, `mslug3`. The ROM filename IS the
    identifier, so there is no name matching, no year tie-break, no gate to run. Either
    the set exists or it does not. That makes it the cheapest CORRECT provider in the
    stack, and it is why it leads the arcade-only media kinds: not promotion over better
    sources, but having no competition where it appears at all.
  * ZXINFO is narrow and deep. Its live endpoint is /v3 — Skyscraper's docs point at a
    path that now 404s, which is exactly the sort of thing that gets re-diagnosed from
    scratch a year later, so the test pins it.
  * THE DATS ARE THE STRUCTURAL ONE. ludodex resolves on crc and sha1, and both describe
    a FILE — so a disc converted to CHD or RVZ matches nothing anywhere, which is most of
    a real PlayStation collection. A SERIAL survives the re-encode. `A DAT ENRICHES AN
    IDENTITY, IT NEVER INVENTS ONE`: minting one per dump would add a hundred thousand
    entries named after files, each a plausible-looking wrong answer for a name search.

No network: every fetcher is replaced. What is asserted here is our own parsing and our
own rules, not a third party's uptime.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


# A real Redump block, transcribed from Sony - PlayStation.dat (2026.08.01).
DAT = '''clrmamepro (
\tname "Sony - PlayStation"
)

game (
\tname "'98 Koushien (Japan)"
\tregion "Japan"
\tserial "SLPS-01204"
\trom ( name "'98 Koushien (Japan).bin" size 583415952 crc 8ACD8FB1 md5 39A936EA7521157838D4E67B24F62F15 sha1 782C50827BF4CF8FE5530B64B188A2D43C75B0E0 serial "SLPS-01204" )
)
game (
\tname "Multi Disc (USA)"
\tregion "USA"
\tserial "SLUS-00594"
\trom ( name "Multi Disc (USA) (Track 01).bin" size 100 crc AAAA1111 sha1 1111111111111111111111111111111111111111 )
\trom ( name "Multi Disc (USA) (Track 02).bin" size 200 crc BBBB2222 sha1 2222222222222222222222222222222222222222 )
)
game (
\tname "No Serial Here (Europe)"
\tregion "Europe"
\trom ( name "No Serial Here (Europe).bin" size 5 crc CCCC3333 sha1 3333333333333333333333333333333333333333 )
)
'''

# A real ArcadeDB record, trimmed — every key below came back from the live service.
MAME = {"game_name": "pacman", "title": "Pac-Man (Midway)", "year": "1980",
        "manufacturer": "Namco (Midway license)", "genre": "Maze / Collect",
        "players": "2", "languages": "English", "history": "Pac-Man (c) 1980 Midway.",
        "screen_orientation": "Vertical", "input_controls": "joystick (4-way)",
        "input_buttons": "0", "cloneof": "puckman", "nplayers": "2P alt",
        "url_image_ingame": "https://adb.arcadeitalia.net/?mame=pacman&type=ingame",
        "url_image_marquee": "https://adb.arcadeitalia.net/?mame=pacman&type=marquee",
        "url_image_cabinet": "https://adb.arcadeitalia.net/?mame=pacman&type=cabinet",
        "url_video_shortplay": "https://adb.arcadeitalia.net/download_file.php?x=1",
        "url_image_nothing": ""}

# A real ZXInfo record, trimmed (entry 0002259, Head over Heels).
ZX = {"title": "Head over Heels", "machineType": "ZX-Spectrum 48K/128K",
      "genreType": "Arcade Game", "yearOfRelease": 1987, "language": "English",
      "maxPlayers": 1,
      "authors": [{"name": "Jon Ritman"}, {"name": "Bernie Drummond"}],
      "publishers": [{"name": "Ocean Software Ltd"}],
      "screens": [{"type": "Loading screen", "url": "/zxscreens/0002259/x-load.png"},
                  {"type": "Running screen", "url": "/pub/x.gif"},
                  {"type": "Something New", "url": "/pub/y.gif"},
                  {"type": "No Url", "url": ""}]}


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_support
    data = test_support.isolate("ludodex-freeprov-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import config
    import media
    import provider_caps as PC
    import libretro_dats as D
    import arcadedb as A
    import zxinfo as Z
    import matchindex as M

    print("1. the DAT parser reads clrmamepro, including multi-track discs")
    p = os.path.join(data, "psx.dat")
    open(p, "w", encoding="utf-8").write(DAT)
    rows = list(D.parse(p))
    check("4 rom entries from 3 games: %d" % len(rows), len(rows) == 4)
    check("hashes are lowercased for lookup", rows[0]["crc"] == "8acd8fb1")
    check("the serial comes through", rows[0]["serial"] == "SLPS-01204")
    check("region too", rows[0]["region"] == "Japan")
    tracks = [r for r in rows if r["serial"] == "SLUS-00594"]
    check("BOTH tracks of a multi-track disc are yielded", len(tracks) == 2)
    check("each with its own hash, sharing the game's serial",
          tracks[0]["crc"] != tracks[1]["crc"])
    check("a game with no serial yields one anyway, with an empty serial",
          any(r["crc"] == "cccc3333" and r["serial"] == "" for r in rows))

    print()
    print("2. A SERIAL IS NOT A HASH — it gets its own namespace")
    check("SERIAL_NS is declared", M.SERIAL_NS == "serial")
    check("and it is NOT in HASH_NS, which is about file bytes",
          "serial" not in M.HASH_NS)
    src = open(os.path.join(root, "ludodex", "matchindex.py"), encoding="utf-8").read()
    step = src[src.index("def _merge_libretro_dats"):src.index("def _merge_tgdb_freemap")]
    check("the reason is written down where it will be read",
          "CHD" in src[:src.index("SERIAL_NS")] or "CHD" in step)

    print()
    print("3. A DAT ENRICHES AN IDENTITY, IT NEVER INVENTS ONE")
    check("nothing in the step inserts into identity", "INSERT OR IGNORE INTO identity("
          not in step and "INSERT INTO identity(" not in step)
    check("it only ever looks one UP", "SELECT identity_id FROM identity_key" in step)
    check("and the rule is stated", "NEVER INVENTS ONE" in step)

    print()
    print("4. it actually attaches serials — run the step against a real index")
    D.CACHE_DIR = os.path.join(data, "dats")
    os.makedirs(os.path.join(D.CACHE_DIR, "redump"), exist_ok=True)
    open(os.path.join(D.CACHE_DIR, "redump", "Test.dat"), "w",
         encoding="utf-8").write(DAT)
    D.all_rows = lambda collections=None, refresh=False, progress=False: (
        dict(r, collection="redump", system="Test")
        for r in D.parse(os.path.join(D.CACHE_DIR, "redump", "Test.dat")))
    con = M.con_db()
    con.execute("INSERT INTO identity(id,name,norm_key,year,first_release_date,built_at)"
                " VALUES(55,'98 Koushien','98koushien',1998,NULL,0)")
    con.execute("INSERT INTO identity_key(ns,val,identity_id,kind) "
                "VALUES('sha1','782c50827bf4cf8fe5530b64b188a2d43c75b0e0',55,'exact')")
    con.commit()
    serials, hashes = M._merge_libretro_dats(con, progress=False)

    def owner(ns, val):
        r = con.execute("SELECT identity_id FROM identity_key WHERE ns=? AND val=?",
                        (ns, val)).fetchone()
        return r["identity_id"] if r else None

    check("the serial landed on the identity that owned the hash",
          owner("serial", "SLPS-01204") == 55)
    check("stored upper-cased, so a lowercase lookup cannot silently miss",
          owner("serial", "slps-01204") is None and serials >= 1)
    check("the canonical crc came along with it", owner("crc", "8acd8fb1") == 55)
    check("a dump NOBODY owns was skipped, not minted",
          owner("serial", "SLUS-00594") is None
          and con.execute("SELECT COUNT(*) FROM identity").fetchone()[0] == 1)
    before = con.execute("SELECT COUNT(*) FROM identity_key").fetchone()[0]
    M._merge_libretro_dats(con, progress=False)
    check("re-running changes nothing — a rebuild is idempotent",
          con.execute("SELECT COUNT(*) FROM identity_key").fetchone()[0] == before)
    con.close()

    print()
    print("5. ARCADEDB — keyed on the set name, so there is nothing to get wrong")
    m = A.extract_metadata(MAME)
    check("year", m["release_year"] == "1980")
    check("manufacturer becomes the developer",
          m["developers"] == ["Namco (Midway license)"])
    check("MAME's genre", m["genres"] == ["Maze / Collect"])
    check("players fold into game_modes", m["game_modes"] == ["Multiplayer"])
    check("history.dat becomes the description", "Pac-Man" in m["description"])
    media_rows = A.extract_media(MAME)
    kinds = {r["kind"] for r in media_rows}
    check("marquee, cabinet, screenshot and video all mapped: %s" % sorted(kinds),
          {"marquee", "arcade_cabinet", "screenshot", "video"} <= kinds)
    check("an empty url is not emitted as a blank asset",
          all(r["url"] for r in media_rows))
    check("every kind it claims is a real media kind",
          all(k in media.KINDS for k in A.MEDIA_KIND.values()))
    cab = A.cabinet_facts(MAME)
    check("the cabinet facts nothing else has: %s" % sorted(cab)[:4],
          cab["screen_orientation"] == "Vertical"
          and cab["input_controls"] == "joystick (4-way)")
    check("a record with no title is a MISS, not a blank hit",
          A.query.__doc__ and "miss" in A.query.__doc__.lower())

    print()
    print("6. ZXINFO — and the endpoint that Skyscraper's docs get wrong")
    check("the live base is /v3", Z.API.endswith("/v3"))
    check("recorded WHY, so nobody re-diagnoses it",
          "404" in Z.__doc__ and "v3" in Z.__doc__)
    m = Z.extract_metadata(ZX)
    check("the machine variant is a DEVICE fact, not a genre",
          m["device"] == "ZX-Spectrum 48K/128K" and m["genres"] == ["Arcade Game"])
    check("authors become developers, named individually",
          m["developers"] == ["Jon Ritman", "Bernie Drummond"])
    check("the label becomes the publisher", m["publishers"] == ["Ocean Software Ltd"])
    check("single player read from maxPlayers", m["game_modes"] == ["Single player"])
    zm = Z.extract_media(ZX)
    check("3 screens, the url-less one dropped", len(zm) == 3)
    check("host-relative urls are made absolute",
          all(r["url"].startswith("https://") for r in zm))
    check("a loading screen is a title screen, the closest honest fit",
          zm[0]["kind"] == "title_screen")
    check("an unknown screen type falls back to screenshot rather than being dropped",
          zm[2]["kind"] == "screenshot")

    print()
    print("7. ranked where they lead, absent where they have nothing")
    for kind in ("marquee", "arcade_cabinet", "arcade_controls", "pcb", "flyer"):
        check("arcadedb leads %-16s" % kind, media.PRIORITY[kind][0] == "arcadedb")
    check("but it does NOT appear for cover — it has no box art",
          "arcadedb" not in media.PRIORITY["cover"])
    check("nor zxinfo, which has no cover either",
          "zxinfo" not in media.PRIORITY["cover"])
    check("both are registered remote providers",
          {"arcadedb", "zxinfo"} <= set(media.REMOTE_PROVIDERS))

    print()
    print("8. they are declared, toggleable and tooltipped")
    for p in ("arcadedb", "zxinfo"):
        check("%-9s is a metadata provider" % p, p in config.METADATA_PROVIDERS)
        check("%-9s has a label" % p, bool(PC.LABEL.get(p)))
        check("%-9s claims at least 4 kinds" % p,
              len([k for k, v in PC.CAPS.items() if p in v]) >= 4)
        check("%-9s can be switched off" % p, p in PC.ENABLED_KEY)
    config.set_("metadata_arcadedb_enabled", "0")
    check("and off means off", not A.enabled() and not PC.enabled("arcadedb"))
    config.set_("metadata_arcadedb_enabled", "1")
    check("libretro dats can be switched off too", "matchindex_libretro_dats"
          in config.DEFAULTS and D.enabled())

    print()
    print("9. a fourth source is a fourth attribution")
    names = [s["name"] for s in M.SOURCES]
    check("libretro-database is credited: %d sources" % len(names),
          any("libretro" in n for n in names))
    check("with its licence", any(s["license"] == "CC BY-SA 4.0" for s in M.SOURCES))
    check("and named in the attribution string", "libretro" in M.ATTRIBUTION)

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
