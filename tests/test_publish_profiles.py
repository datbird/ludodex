#!/usr/bin/env python3
"""Phase 2 moves a seam. It must not move behaviour.

The whole value of this refactor is that ES-DE keeps doing exactly what it did, so the
first test is an EQUIVALENCE test against the constants as they were — transcribed here
independently, from the pre-refactor source, rather than imported from the module under
test. A test that asks the new code whether it agrees with itself proves nothing.

Two things it also has to prove, because they are what the seam is FOR:

  * A second profile with the opposite shape (no renaming, no media, no metadata, no
    conversion) produces different answers from the same inputs. If publishing still
    compiles ES-DE assumptions into the push path, this is what catches it.
  * An unknown profile id RAISES. Falling back to a default would publish a library
    into the wrong layout and look like it worked.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


# --- the pre-refactor behaviour, transcribed from the original devicesync.py -------- #
# Deliberately a COPY, not an import. This is the oracle; if it and the profile ever
# disagree, the profile changed behaviour and that is the bug this file exists to catch.
_OLD_MAP = {
    "sega genesis": "genesis", "sega ms": "mastersystem", "sega cd": "segacd",
    "sega 32x": "sega32x", "sega saturn": "saturn", "gameboy": "gb",
    "gameboy color": "gbc", "gamecube": "gc", "jaguar": "atarijaguar",
    "jaguar cd": "atarijaguarcd", "lynx": "atarilynx", "atari st": "atarist",
    "atari 2600": "atari2600", "atari 5200": "atari5200", "atari 7800": "atari7800",
    "3ds": "n3ds", "nintendo switch": "switch", "zx spectrum": "zxspectrum",
    "turbo gfx": "tg16", "tubo duo": "tg16", "neogeopocketcolor": "ngpc",
    "neogeo": "neogeo", "mame": "arcade", "arcade (mame)": "arcade",
}
_OLD_CD = {"psx", "ps2", "saturn", "segacd", "pcenginecd", "pcfx", "3do",
           "neogeocd", "amigacd32", "megacd", "tg-cd", "turbografxcd"}
_OLD_SRC = {"cue", "bin", "iso", "img", "gdi", "toc", "ccd", "mdf", "nrg"}
_OLD_FOLDERS = {
    "cover": "covers", "background": "fanart", "logo": "marquees",
    "screenshot": "screenshots", "title_screen": "titlescreens", "box_3d": "3dboxes",
    "box_back": "backcovers", "physical_media": "physicalmedia", "mix": "miximages",
    "video": "videos", "manual": "manuals",
}


def old_system(platform):
    return _OLD_MAP.get((platform or "").strip().lower(), (platform or "").strip().lower())


def old_convert(sys_, ext):
    e = (ext or "").lower().lstrip(".")
    if sys_ in _OLD_CD and e in _OLD_SRC:
        return ("chd", "chd")
    if sys_ in ("gc", "wii") and e in ("iso", "gcm", "rvz", "wbfs", "ciso"):
        return ("rvz", "rvz") if e != "rvz" else ("rvz", "copy")
    return (e, "copy")


def old_gamelists(media_path):
    mp = (media_path or "").rstrip("/")
    if mp.endswith("/downloaded_media"):
        return mp[:-len("/downloaded_media")] + "/gamelists"
    return os.path.join(os.path.dirname(mp), "gamelists")


def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "ludodex"))
    import publish_profiles as P
    import devicesync as D

    print("1. every platform in the old map resolves identically")
    plats = sorted(_OLD_MAP) + ["snes", "nes", "psx", "n64", "gba", "nds", "ps2",
                                "ps3", "psp", "psvita", "dreamcast", "wii", "wiiu",
                                "switch", "", "  SNES  ", "totally unknown platform"]
    bad = [p for p in plats if P.system_for(P.ESDE, p) != old_system(p)]
    check("%d platforms, none disagree" % len(plats), not bad)
    check("and the module-level helper agrees too",
          all(D.esde_system(p) == old_system(p) for p in plats))

    print()
    print("2. every conversion decision is unchanged")
    systems = sorted(_OLD_CD) + ["gc", "wii", "snes", "nes", "arcade", "switch"]
    exts = sorted(_OLD_SRC) + ["rvz", "gcm", "wbfs", "ciso", "zip", "7z", "sfc",
                               "chd", "", ".ISO"]
    diffs = [(s, e) for s in systems for e in exts
             if P.convert_plan(P.ESDE, s, e) != old_convert(s, e)]
    check("%d combinations, none disagree" % (len(systems) * len(exts)), not diffs)
    if diffs:
        print("      first disagreements: %s" % diffs[:5])

    print()
    print("3. media folders and the gamelists path are unchanged")
    check("every media kind maps the same",
          all(P.media_folder(P.ESDE, k) == v for k, v in _OLD_FOLDERS.items()))
    check("a kind ES-DE has no folder for returns None",
          P.media_folder(P.ESDE, "bezel") is None)
    for mp in ("/x/ES-DE/downloaded_media", "/x/media", "/a/b/downloaded_media/",
               "downloaded_media"):
        check("gamelists path for %r" % mp,
              D.esde_gamelists_path(mp) == old_gamelists(mp))

    print()
    print("4. the SECOND profile answers differently — the seam is real")
    # If any of these match ES-DE, the layout is still hardcoded somewhere.
    check("no system renaming", P.system_for(P.FOLDER, "sega genesis") == "sega genesis")
    check("no media home", P.media_folder(P.FOLDER, "cover") is None)
    check("no media destination", P.media_dest(
        P.FOLDER, "/m", "/r", "snes", "cover", "Game", "png") is None)
    check("no metadata file", P.metadata_root(P.FOLDER, "/x/media") is None)
    check("no conversion — a psx cue ships as-is",
          P.convert_plan(P.FOLDER, "psx", "cue") == ("cue", "copy"))
    check("...where ES-DE would convert it",
          P.convert_plan(P.ESDE, "psx", "cue") == ("chd", "chd"))

    print()
    print("5. an archive policy is per-system, and drives conversion")
    # The 'this emulator cannot read a zip' rule the design calls for.
    prof = dict(P.ESDE, archives={"default": "keep", "n64": "unzip"})
    check("a keep system ships the zip", P.convert_plan(prof, "snes", "zip")
          == ("zip", "copy"))
    check("an unzip system unpacks it",
          P.convert_plan(prof, "n64", "zip")[1] == "unzip")
    check("and a non-archive on that system is untouched",
          P.convert_plan(prof, "n64", "z64") == ("z64", "copy"))

    print()
    print("6. an unknown profile id raises rather than defaulting")
    # Silently falling back to ES-DE would lay a library out for the wrong frontend and
    # report success.
    ok = False
    try:
        P.get("retrobat-typo")
    except KeyError:
        ok = True
    check("KeyError, not a default", ok)
    check("known ids resolve", P.get("esde")["id"] == "esde"
          and P.get("folder")["id"] == "folder")
    check("and the lookup is case/space tolerant", P.get("  ESDE ")["id"] == "esde")

    print()
    print("7. disc handling is a profile fact, not a global one")
    check("ES-DE knows psx is disc-based", P.is_disc_system(P.ESDE, "psx"))
    check("the folder profile does not", not P.is_disc_system(P.FOLDER, "psx"))
    check("but both agree on what an entry point looks like",
          P.entry_exts(P.ESDE) == P.entry_exts(P.FOLDER))

    print()
    print("8. the esgamelist profile reproduces a REAL library's paths")
    # Every string below was read out of a live, ScreenScraper-scraped library: a
    # Windows ROM tree's `3do/gamelist.xml`. This is the phase-6
    # reality check — a profile that agrees only with its own assumptions proves
    # nothing, so it is asserted against what the target actually contains.
    g = P.get("esgamelist")
    live = {"cover": ("png", "G:/roms/3do/images/Road Rash (USA)-image.png"),
            "logo": ("png", "G:/roms/3do/images/Road Rash (USA)-marquee.png"),
            "screenshot": ("png", "G:/roms/3do/images/Road Rash (USA)-thumb.png"),
            "video": ("mp4", "G:/roms/3do/videos/Road Rash (USA)-video.mp4"),
            "manual": ("pdf", "G:/roms/3do/manuals/Road Rash (USA)-manual.pdf")}
    for kind, (ext, want) in live.items():
        got = P.media_dest(g, None, "G:/roms", "3do", kind, "Road Rash (USA)", ext)
        check("%-10s -> %s" % (kind, got), got == want)
    check("the gamelist sits IN the system folder, not a sibling tree",
          P.metadata_path(g, None, "G:/roms", "3do") == "G:/roms/3do/gamelist.xml")
    check("and it records media paths, unlike ES-DE",
          g["metadata"].get("records_media_paths") is True
          and not P.ESDE["metadata"].get("records_media_paths"))

    print()
    print("9. cover and logo share a folder — the KIND decides the filename")
    # The bug this catches: deriving the kind back from the folder returns whichever
    # was declared first, so the marquee silently lands on <base>-image.png.
    cov = P.media_dest(g, None, "/r", "3do", "cover", "X", "png")
    logo = P.media_dest(g, None, "/r", "3do", "logo", "X", "png")
    check("same folder", os.path.dirname(cov) == os.path.dirname(logo))
    check("different filenames", cov != logo)
    check("and the right ones", cov.endswith("-image.png")
          and logo.endswith("-marquee.png"))

    print()
    print("10. observed system names, not guessed ones")
    # Read off the real directory listing. These are LaunchBox-style, NOT Batocera's.
    for ours, theirs in (("psx", "sonyplaystation"), ("snes", "nintendosupernes"),
                         ("sega genesis", "segagenesis"), ("n64", "nintendo64"),
                         ("dreamcast", "segadreamcast"), ("mame", "mame")):
        check("%-14s -> %s" % (ours, theirs), P.system_for(g, ours) == theirs)
    check("ES-DE still disagrees, which is the point",
          P.system_for(P.ESDE, "psx") != P.system_for(g, "psx"))

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
