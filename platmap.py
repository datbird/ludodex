"""Platform ontology: map both ludodex emulation labels AND IGDB platform names to a shared
canonical token, so "nds" and IGDB "Nintendo DS" compare equal. Used by the cross-
contamination gate (an emulation entry shouldn't adopt an IGDB identity for a game that
demonstrably didn't release on that platform — the Atari-2600 "Dune" vs the 1992 Cryo Dune)."""
import re

# canonical -> every normalized alias (ludodex label + IGDB name forms). Normalization =
# lowercase + strip non-alphanumerics, so "Sega CD" and "sega cd" both hit "segacd".
_ALIASES = {
    "atari2600": ["atari2600"],
    "atari5200": ["atari5200"],
    "atari7800": ["atari7800"],
    "atari8bit": ["atari8bit", "atari800", "atari8bitfamily"],
    "amiga": ["amiga", "commodoreamiga"],
    "amigacd32": ["amigacd32", "cd32"],
    "apple2": ["apple2", "appleii", "appleiigs"],
    "c64": ["c64", "commodorec64128max", "commodore64"],
    "3do": ["3do", "3dointeractivemultiplayer"],
    "3ds": ["3ds", "nintendo3ds", "newnintendo3ds"],
    "nds": ["nds", "nintendods"],
    "n64": ["n64", "nintendo64"],
    "gamecube": ["gamecube", "nintendogamecube", "ngc"],
    "wii": ["wii"],
    "wiiu": ["wiiu"],
    "switch": ["switch", "nintendoswitch"],
    "snes": ["snes", "supernintendoentertainmentsystem", "superfamicom", "sfc"],
    "nes": ["nes", "nintendoentertainmentsystem", "familycomputer", "famicom",
            "familycomputerdisksystem", "fds"],
    "gb": ["gameboy", "gb"],
    "gbc": ["gameboycolor", "gbc"],
    "gba": ["gba", "gameboyadvance"],
    "virtualboy": ["virtualboy"],
    "colecovision": ["colecovision"],
    "intellivision": ["intellivision"],
    "vectrex": ["vectrex"],
    "dreamcast": ["dreamcast"],
    "saturn": ["segasaturn", "saturn"],
    "segacd": ["segacd", "megacd", "segamegacd"],
    "sega32x": ["sega32x", "32x"],
    "genesis": ["segagenesis", "genesis", "segamegadrivegenesis", "megadrive"],
    "sms": ["segams", "segamastersystem", "segamastersystemmarkiii", "mastersystem"],
    "gamegear": ["gamegear", "segagamegear"],
    "sg1000": ["sg1000", "segasg1000"],
    "jaguar": ["jaguar", "atarijaguar"],
    "jaguarcd": ["jaguarcd", "atarijaguarcd"],
    "lynx": ["lynx", "atarilynx"],
    "arcade": ["mame", "arcade"],
    "neogeo": ["neogeo", "neogeoaes", "neogeomvs", "neogeoaesmvs"],
    "neogeocd": ["neogeocd"],
    "ngp": ["neogeopocket"],
    "ngpc": ["neogeopocketcolor"],
    "ps1": ["psx", "playstation", "ps1", "psone"],
    "ps2": ["ps2", "playstation2"],
    "ps3": ["ps3", "playstation3"],
    "ps4": ["ps4", "playstation4"],
    "ps5": ["ps5", "playstation5"],
    "psp": ["psp", "playstationportable"],
    "psvita": ["psvita", "playstationvita"],
    "pce": ["turbogfx", "turbografx16pcengine", "pcengine", "turbografx",
            "turbografx16", "pcenginesupergrafx"],
    "pcecd": ["tuboduo", "turboduo", "turbografx16pcenginecd", "pcenginecd",
              "turbografxcd"],
    "zxspectrum": ["zxspectrum", "sinclairzx81"],
    "amstrad": ["amstradcpc"],
    "msx": ["msx", "msx2"],
    "xbox": ["xbox"],
    "xbox360": ["xbox360"],
    "xboxone": ["xboxone"],
    "supervision": ["supervision", "watara", "wataraquickshotsupervision"],
    "dos": ["dos"],
    "pc": ["pcmicrosoftwindows", "windows", "pc"],
    "mac": ["mac"],
    "linux": ["linux"],
}
_CANON = {a: c for c, al in _ALIASES.items() for a in al}
KNOWN = set(_ALIASES)             # canonicals we're confident about

# --- Explicit in-title hardware tags: filename beats folder ---------------------
# Some ROM sets bake the console name into the title ("Doom 32X"), and large user
# collections routinely misplace such a ROM into the wrong system folder (a 32X dump
# dropped in the Genesis folder). When a title carries one of these tokens it names
# the platform AUTHORITATIVELY and overrides a contradicting folder/path label —
# end users mislabel folders far more often than filenames, so filename wins 100%.
# Value = the ludodex platform label to attribute to. Curated to tokens that are pure
# hardware designators, NEVER a real title word ("cd" is deliberately ABSENT — else
# "Sonic CD" would be yanked to the Sega CD). Every token here is also stripped from
# the dedupe key by titlenorm (which imports this set), so "Doom 32X" and "Doom" on
# the 32X unify. Extend deliberately.
TITLE_PLATFORM = {
    "32x": "sega 32x",
    "cd32": "amigacd32",   # Amiga CD32; unambiguous, never a title word (unlike bare "cd")
}


def platform_from_title(title):
    """The ludodex platform label named by an explicit hardware token in `title`, or
    None. Deterministic and conservative — fires only on the curated unambiguous
    hardware designators in TITLE_PLATFORM, so a coincidental word never re-platforms."""
    for tok in re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).split():
        lbl = TITLE_PLATFORM.get(tok)
        if lbl:
            return lbl
    return None


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def canon(label):
    """Canonical platform token for a ludodex label OR an IGDB platform name. Falls back to
    the bare normalized string for anything unmapped (so exact-normalize matches still work),
    but such a token is NOT in KNOWN, so the gate never separates on an unmapped platform."""
    n = _norm(label)
    return _CANON.get(n, n)


def igdb_canons(platforms):
    """Set of canonical tokens for an IGDB record's `platforms` list ([{name:..},..])."""
    return {canon(p.get("name")) for p in (platforms or []) if p.get("name")}


# Hardware generation per canonical platform (home-computer lines mapped to the nearest
# console gen). Used ONLY to gate contamination SUSPECTS — a game can be ported FORWARD to
# newer hardware, but a genuine game can't appear on hardware OLDER than its earliest
# platform. So "entry generation < the game's earliest platform generation" = suspect (then
# the AI decides). Approximate on purpose; the AI is the precision layer.
GEN = {
    "atari2600": 2, "atari5200": 2, "atari7800": 3, "intellivision": 2, "colecovision": 2,
    "vectrex": 2, "sg1000": 2, "apple2": 2, "c64": 2, "amstrad": 2, "zxspectrum": 2, "msx": 2,
    "nes": 3, "sms": 3, "atari8bit": 2, "amiga": 3, "dos": 3, "gb": 3, "gamegear": 3,
    "lynx": 3, "supervision": 3,
    "genesis": 4, "snes": 4, "neogeo": 4, "neogeocd": 4, "sega32x": 4, "segacd": 4,
    "amigacd32": 4, "pce": 4, "pcecd": 4, "gbc": 4, "ngp": 4, "ngpc": 4, "virtualboy": 4,
    "arcade": 4,
    "ps1": 5, "saturn": 5, "n64": 5, "3do": 5, "jaguar": 5, "jaguarcd": 5, "gba": 5,
    "pc": 5, "mac": 5, "linux": 5,
    "dreamcast": 6, "ps2": 6, "gamecube": 6, "xbox": 6, "nds": 6, "psp": 6,
    "ps3": 7, "xbox360": 7, "wii": 7, "psvita": 7, "3ds": 7,
    "ps4": 8, "xboxone": 8, "wiiu": 8, "switch": 8,
    "ps5": 9,
}


def generation(label):
    return GEN.get(canon(label))


def contamination_suspect(entry_platform, igdb_platforms):
    """True if an emulation entry on `entry_platform` bound to an IGDB game with these
    `igdb_platforms` is a CONTAMINATION SUSPECT: its platform isn't among the game's, and it
    is an OLDER hardware generation than the game's earliest platform (an impossible
    backport → probably a different game sharing the title). Conservative: unknown platform,
    empty IGDB platforms, or no generation data => not a suspect (the AI is never bothered)."""
    ec = canon(entry_platform)
    if ec not in KNOWN:
        return False
    igc = igdb_canons(igdb_platforms)
    if not igc or ec in igc:
        return False                       # game has no platforms, or entry IS compatible
    eg = GEN.get(ec)
    game_gens = [GEN[c] for c in igc if c in GEN]
    if eg is None or not game_gens:
        return False
    return eg < min(game_gens)             # entry predates the game's earliest platform
