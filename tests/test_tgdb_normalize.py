#!/usr/bin/env python3
"""TheGamesDB is keyed FINER than ludodex is, and that is the whole risk.

Its game row is (title, platform, region); ours is (game, platform). So a lookup for
"Sonic 2 on Genesis" returns TWO rows that are both correct and identical on every term a
matcher scores — same title, same platform, same year, same developer. A scorer handed
that picks whichever sorted first, and reports a confident answer.

THAT IS THE FAIL-OPEN SHAPE. Not a miss read as consent this time, but an AMBIGUITY read
as consent, which fails the same way and for the same reason: the deciding term was never
looked at. So the rules this file holds the normalizer to are:

  * THE FILE OUTRANKS THE PREFERENCE. `Sonic 2 (Europe).md` takes the PAL row even when
    the configured preference is NTSC-U. The filename is evidence; the preference is a
    default, and a default must never overrule evidence.
  * A FILE THAT ASKS FOR A REGION NOBODY HAS IS A MISS. Falling back to the preferred
    region there would file a Japanese dump under the American release — silently, and
    with a confident-looking score.
  * ROWS THAT DIFFER BY MORE THAN REGION ARE NOT A REGIONAL SPLIT. Different platforms
    are different entries; resolving them as one is the bug, not the feature.
  * MARKERS ARE NOT GENRES AND ARE NOT RUBBISH. Seven of TheGamesDB's thirty "genres"
    are release-type flags. Filing them as genres pollutes a facet the library filters
    on; dropping them discards signal nongame/homebrew want.

Fixtures are the REAL rows, ids and all, read off the live API on 2026-08-16.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


# The live answer for "Sonic the Hedgehog 2", verbatim.
GEN_US = {"id": 142, "game_title": "Sonic the Hedgehog 2", "platform": 18,
          "region_id": 2, "country_id": 50, "release_date": "1992-11-24",
          "players": 2, "coop": "Yes", "rating": "E - Everyone",
          "overview": "Sonic the Hedgehog 2 is a 1992 Genesis video game"}
GEN_PAL = {"id": 124507, "game_title": "Sonic the Hedgehog 2", "platform": 18,
           "region_id": 6, "country_id": 20, "release_date": "1992-11-24",
           "players": 2, "coop": "Yes"}
GG_JP = {"id": 113847, "game_title": "Sonic the Hedgehog 2", "platform": 20,
         "region_id": 4, "country_id": 28, "release_date": "1992-11-21"}
GG_PAL = {"id": 109078, "game_title": "Sonic the Hedgehog 2", "platform": 20,
          "region_id": 6, "country_id": 18, "release_date": "1992-12-01"}
CRYSIS = {"id": 2, "game_title": "Crysis", "platform": 1, "region_id": 0,
          "country_id": 0, "release_date": "2007-11-13", "players": 4, "coop": "No",
          "rating": "M - Mature 17+", "os": "Windows XP/Vista",
          "processor": "2.8GHz (XP), 3.2GHz (Vista)", "ram": "1GB (XP), 1.5GB (Vista)",
          "hdd": "12GB", "video": "256MB, DirectX 9.0c",
          "sound": "DirectX 9.0c compatible", "youtube": "abc123"}


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import tgdb_normalize as N

    print("1. the two region axes are read apart, not collapsed")
    check("NTSC-U -> USA", N.region_of(GEN_US) == ("NTSC-U", "USA"))
    check("PAL -> Europe", N.region_of(GEN_PAL) == ("PAL", "Europe"))
    check("NTSC-J -> Japan", N.region_of(GG_JP) == ("NTSC-J", "Japan"))
    check("region 0 is empty, not a guess", N.region_of(CRYSIS) == ("", ""))
    # The USA and Japan are both NTSC, so bare NTSC identifies no market. Guessing USA
    # here is exactly how a Japanese release ends up under an American filename.
    check("bare NTSC names NO market rather than assuming the USA",
          N.region_of({"region_id": 1}) == ("NTSC", ""))

    print()
    print("2. the TV standard is recovered from tags romtags drops on the floor")
    import romtags
    check("(PAL) currently parses as a FLAG, not a region — the gap this fills",
          romtags.parse_name("Sonic 2 (Europe) (PAL)")[1] == "Europe"
          and "PAL" in romtags.parse_name("Sonic 2 (Europe) (PAL)")[6])
    check("(NTSC-J) parses as NOTHING today",
          not romtags.parse_name("Sonic 2 (Japan) (NTSC-J)")[6])
    check("...and we read it anyway", N.tv_standard("(Japan) (NTSC-J)") == "NTSC-J")
    check("longest match wins — NTSC-J is not eaten as NTSC",
          N.tv_standard("(NTSC-J)") == "NTSC-J")
    check("PAL-B too", N.tv_standard("(Europe) (PAL-B)") == "PAL-B")
    check("and a file with no standard says so", N.tv_standard("(USA)") == "")

    print()
    print("3. what a FILENAME is asking for — explicit before implied")
    check("(Japan) implies NTSC-J", N.wanted_regions("Sonic 2 (Japan).md")[0] == "NTSC-J")
    check("(Europe) implies PAL", N.wanted_regions("Sonic 2 (Europe).md")[0] == "PAL")
    check("an explicit (NTSC-J) is stated, so it leads",
          N.wanted_regions("Sonic 2 (USA) (NTSC-J).md")[0] == "NTSC-J")
    check("a multi-market file contributes both",
          set(N.wanted_regions("Sonic 2 (USA, Europe).md")) >= {"NTSC-U", "PAL"})
    check("a bare filename asks for nothing", N.wanted_regions("Crysis.exe") == [])

    print()
    print("4. THE SPLIT — two Genesis rows differing only by region")
    rows = [GEN_US, GEN_PAL]
    r, why = N.pick_release(rows, filename="Sonic The Hedgehog 2 (Europe).md")
    check("the European file takes the PAL row (%s)" % why, r["id"] == 124507)
    r, why = N.pick_release(rows, filename="Sonic The Hedgehog 2 (USA).md")
    check("the USA file takes the NTSC-U row (%s)" % why, r["id"] == 142)

    print()
    print("5. THE FILE OUTRANKS THE PREFERENCE — a default must not overrule evidence")
    r, why = N.pick_release(rows, filename="Sonic 2 (Europe).md",
                            prefer=("NTSC-U", "NTSC"))
    check("prefers NTSC-U, but the file said Europe -> PAL wins", r["id"] == 124507)
    r, _ = N.pick_release(rows, filename=None, prefer=("NTSC-U", "NTSC"))
    check("with NO file, the preference decides", r["id"] == 142)
    r, _ = N.pick_release(rows, filename=None, prefer=("PAL",))
    check("and it is genuinely the preference, not a hardcoded order", r["id"] == 124507)

    print()
    print("6. A FILE ASKING FOR A REGION NOBODY HAS IS A MISS, not a fallback")
    # The whole point. Falling back here files a Japanese dump under the US release.
    r, why = N.pick_release(rows, filename="Sonic 2 (Japan).md",
                            prefer=("NTSC-U", "NTSC", "PAL"))
    check("no row is returned", r is None)
    check("and the reason names what was asked for: %s" % why, "NTSC-J" in why)

    print()
    print("7. rows that differ by MORE than region are not a regional split")
    r, why = N.pick_release([GEN_US, GG_JP])
    check("Genesis + Game Gear -> no pick", r is None)
    check("and it says so rather than scoring them: %s" % why[:48], "platform" in why)
    r, why = N.pick_release([GG_JP, GG_PAL], filename="Sonic 2 (Europe).gg")
    check("but two Game Gear rows ARE one, and resolve", r["id"] == 109078)

    print()
    print("8. the trivial cases stay trivial")
    r, why = N.pick_release([GEN_US])
    check("one row is not a choice", r["id"] == 142 and why == "only candidate")
    r, why = N.pick_release([])
    check("no rows is not a crash", r is None and "no candidates" in why)

    print()
    print("9. MARKERS ARE NOT GENRES — and are not thrown away either")
    g, f = N.split_genres(["Platform", "Action", "Demo", "Unofficial", "Virtual Console"])
    check("real genres survive", g == ["Platform", "Action"])
    check("markers are lifted out", set(f) == {"demo", "unofficial", "rerelease"})
    check("none of the markers leaked into genres",
          not (set(g) & set(N.GENRE_MARKERS)))
    g, f = N.split_genres(["Utility", "Productivity"])
    check("two markers collapsing to one flag are not duplicated", f == ["application"])
    check("a pure-marker row yields NO genres rather than an empty-string one", g == [])

    print()
    print("10. a console row maps to our vocabulary")
    a = N.to_attributes(GEN_US, genre_names=["Platform"],
                        developer_names=["Sonic Team"], publisher_names=["Sega"])
    check("release_date", a["release_date"] == "1992-11-24")
    check("release_year derived from it", a["release_year"] == "1992")
    check("esrb_rating arrives as a readable string",
          a["esrb_rating"] == "E - Everyone")
    check("BOTH region axes are recorded", a["regions"] == ["NTSC-U", "USA"])
    check("players+coop fold into game_modes, not a parallel vocabulary",
          a["game_modes"] == ["Multiplayer", "Co-operative"])
    check("no PC spec on a console row", "os" not in a and "min_spec" not in a)

    print()
    print("11. a PC row carries the minimum spec nothing else supplies")
    a = N.to_attributes(CRYSIS)
    check("os", a["os"] == "Windows XP/Vista")
    check("min_spec has all five parts", set(a["min_spec"]) ==
          {"processor", "ram", "hdd", "video", "sound"})
    check("a bare youtube id becomes a URL",
          a["video_url"] == "https://www.youtube.com/watch?v=abc123")
    check("region 0 writes NO regions rather than an empty one", "regions" not in a)
    check("single-player-vs-multi is read from the count",
          a["game_modes"] == ["Multiplayer"])

    print()
    print("12. an absent field is never written as an empty one")
    # Writing "" would overwrite a better answer from another provider with nothing.
    a = N.to_attributes({"id": 9, "platform": 1, "region_id": 0})
    check("nothing invented from an empty row: %s" % sorted(a), a == {})
    a = N.to_attributes({"id": 9, "platform": 1, "region_id": 0, "overview": "   "})
    check("whitespace is not content", "description" not in a)

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
