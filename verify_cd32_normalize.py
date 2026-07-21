#!/usr/bin/env python3
"""Verify the Amiga CD32 hardware token is stripped/re-platformed like 32X (task #5).

ROM sets bake "CD32" into CD32 titles ("Diggers CD32"), which never matches IGDB
"Diggers" -> the game stays unidentified (game_key title:...), gets no cover, and the
wand — with no resolution to source art from — falsely reports "already has art". Same
class as "Doom 32X"; the token was simply never added to platmap. Guards: bare "cd" must
still NOT strip (else "Sonic CD" -> "Sonic"); the 32X path must keep working."""
import platmap
import titlenorm


def main():
    # cd32 now canonicalizes to the Amiga CD32 (required by titlenorm's strip guard,
    # which pops a trailing token only when platmap.canon(tok) == the entry's platform).
    assert platmap.canon("cd32") == "amigacd32", "canon(cd32) -> amigacd32"
    # explicit in-title tag: filename names the platform authoritatively
    assert platmap.platform_from_title("Diggers CD32") == "amigacd32", "CD32 title -> amigacd32"
    # the trailing hardware token is stripped from the dedupe/resolution key
    assert titlenorm.norm("Diggers CD32", "amigacd32") == "diggers", "Diggers CD32 -> diggers"
    assert titlenorm.norm("Chaos Engine CD32", "amigacd32") == "chaos engine", "multi-word title"
    # never strip the sole token (a bare "CD32" isn't a title)
    assert titlenorm.norm("CD32", "amigacd32") == "cd32", "sole token kept"

    # --- guards: no regressions ---
    # bare "cd" stays a title word — "Sonic CD" on the Sega CD must NOT lose it
    assert "cd" not in platmap.TITLE_PLATFORM, "bare 'cd' stays absent"
    assert titlenorm.norm("Sonic CD", "segacd") == "sonic cd", "Sonic CD unaffected"
    assert platmap.platform_from_title("Sonic CD") is None, "Sonic CD does not re-platform"
    # the 32X path still works
    assert platmap.canon("32x") == "sega32x", "canon(32x) unchanged"
    assert titlenorm.norm("Doom 32X", "sega 32x") == "doom", "Doom 32X -> doom (unchanged)"
    print("verify_cd32_normalize: OK")


if __name__ == "__main__":
    main()
