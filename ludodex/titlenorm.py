#!/usr/bin/env python3
"""Canonical title normalizer — the dedupe key used across ludodex.

Shared by build_library.py (catalog dedupe) and process.py (variant detection)
so a crawled file maps to the same game key the catalog uses. Honors the
dedupe_preserve_years / dedupe_strip_editions preferences (config.py).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import platmap                    # platform canonicalization (32x == "Sega 32X")

ROMAN = {"ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8",
         "ix": "9", "xi": "11", "xii": "12", "xiii": "13", "xiv": "14"}
EDITION = re.compile(
    r"\b(game of the year edition|goty|definitive edition|remaster(ed)?|"
    r"complete edition|deluxe edition|gold edition|enhanced edition|"
    r"ultimate edition|collector'?s edition|digital deluxe|standard edition|"
    r"legacy edition|premium edition|special edition)\b")
_TRAIL_EXT = re.compile(
    r"\.(m3u|iso|chd|cue|bin|img|mdf|nrg|ccd|rvz|wbfs|nkit|gcm|gcz|cso|pbp|gdi|"
    r"cdi|rom|nds|3ds|zip|7z|rar)$")
# No-Intro / GoodTools article convention: the leading article is moved to the end after
# a comma — "Legend of Zelda, The", "Boy and His Blob, A - Trouble on Blobolonia". Drop
# that comma-article so it matches the article-front form ("The Legend of Zelda", which
# the leading-article strip below folds to the same key) and article-position twins of
# the same ROM merge instead of becoming two entries. Covers the common EN + FR/DE/ES/IT
# articles No-Intro uses.
_COMMA_ARTICLE = re.compile(
    r",\s*(the|a|an|le|la|les|las|el|los|il|lo|gli|die|der|das|ein|eine)\b")

# Some ROM sets bake the console name into the title itself ("Doom 32X") instead of
# leaving it to the folder/region tags. A bare trailing hardware token like this is
# redundant — "Doom 32X" and "Doom" on the Sega 32X are the same game — but it splits
# them into two catalog entries because the normalized keys differ. So, when a platform
# is known, drop a TRAILING token that names that very platform. Guarded two ways: the
# token must canonicalize (via platmap) to the entry's own platform AND be one of the
# curated pure-hardware-designator tokens platmap recognizes. The canon-match alone
# already spares "Sonic CD" (platmap has no bare "cd" alias); the allow-set is the extra
# guard for platform words that ARE title words ("Saturn", "Lynx"). Sourced from
# platmap.TITLE_PLATFORM so the tokens stripped here EXACTLY match the tokens that
# re-platform a misplaced ROM in build_library — one source of truth.
_HW_SUFFIX_SAFE = set(platmap.TITLE_PLATFORM)

_PREFS = None


def _prefs():
    global _PREFS
    if _PREFS is None:
        _PREFS = (config.get_bool("dedupe_preserve_years", True),
                  config.get_bool("dedupe_strip_editions", True))
    return _PREFS


def norm(t, platform=None):
    preserve_years, strip_editions = _prefs()
    s = (t or "").lower()
    s = _COMMA_ARTICLE.sub(" ", s)          # No-Intro "Title, The" -> "Title"
    s = _TRAIL_EXT.sub("", s)
    s = re.sub(r"[™®©]", "", s)
    if preserve_years:
        s = re.sub(r"\(([^)]*)\)",
                   lambda m: " %s " % m.group(1)
                   if re.fullmatch(r"\s*\d{4}\s*", m.group(1)) else " ", s)
    else:
        s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = s.replace("&", " and ").replace("+", " plus ")
    if strip_editions:
        s = EDITION.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    toks = [ROMAN.get(w, w) for w in s.split()]
    while toks and toks[0] in ("the", "a", "an"):
        toks = toks[1:]
    if strip_editions:
        # Drop a bare marketing 'edition'/'editions' token when it's TRAILING (or
        # only a year follows it) and isn't the leading word — e.g. "Mass Effect 2
        # (2010) Edition" == "Mass Effect 2 (2010)". Left in place when it's part
        # of the real title ("Special Edition Racing", "Editions of Chaos").
        out = []
        for i, w in enumerate(toks):
            if w in ("edition", "editions") and i > 0 and \
                    all(re.fullmatch(r"\d{4}", x) for x in toks[i + 1:]):
                continue
            out.append(w)
        toks = out
    if platform:
        # Drop a trailing bare hardware token that just names this platform
        # ("Doom 32X" on the Sega 32X -> "doom"). Never strip the sole token.
        pc = platmap.canon(platform)
        while len(toks) > 1 and toks[-1] in _HW_SUFFIX_SAFE \
                and platmap.canon(toks[-1]) == pc:
            toks.pop()
    return " ".join(toks).strip()
