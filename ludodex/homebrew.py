#!/usr/bin/env python3
"""Classify a ROM by its filename tags into a RELEASE TYPE — the un-official
provenance of a dump — and pull structured facts (translation language, patch
version) out of the raw filename that the parsed title strips.

Read from the RAW ROM filename (No-Intro / GoodTools / TOSEC / scene tags like
"(Demo)", "(Proto)", "(Hack)", "(Aftermarket)", "(19xx)", "[T+Eng]",
"(English v1.01)"). Uses:
  * `release_type` (editable attribute) — Homebrew / Hack / Translation / Prototype
    / Demo / Unlicensed, or blank = commercial. Spotlight keeps the non-commercial
    ones out of its normal themes.
  * `Translation` is special: an English (or other) fan-translation IS the base
    game, just localized — so build_library does NOT block it from matching its
    real IGDB entry (Akumajou Dracula X -> Castlevania: Rondo of Blood); it inherits
    the game's art/metadata and carries `language` + `version` attributes on top.
"""
import re

# ISO-ish language codes / names seen in translation tags -> canonical language.
_LANG_CODE = {
    "en": "English", "eng": "English", "us": "English",
    "fr": "French", "fre": "French", "fra": "French",
    "de": "German", "ger": "German", "deu": "German",
    "es": "Spanish", "spa": "Spanish",
    "it": "Italian", "ita": "Italian",
    "pt": "Portuguese", "por": "Portuguese", "ptbr": "Portuguese",
    "nl": "Dutch", "dut": "Dutch",
    "ru": "Russian", "rus": "Russian",
    "pl": "Polish", "pol": "Polish",
    "ja": "Japanese", "jp": "Japanese", "jpn": "Japanese",
    "ko": "Korean", "kor": "Korean",
    "zh": "Chinese", "chi": "Chinese", "zho": "Chinese",
    "sv": "Swedish", "cs": "Czech", "hu": "Hungarian", "tr": "Turkish",
}
_LANG_NAME = {n.lower(): n for n in set(_LANG_CODE.values())}

# A fan translation / localization patch. The strongest, least-ambiguous signals:
# a GoodTools [T+xx]/[T-xx] tag, a "(T-Eng)" tag, the word "translat(ed/ion)", or a
# "(<Language> v1.01)" tag (a version pins it to a patch, not a mere region label).
# THE +/- SIGN IS REQUIRED, in the parenthesised form as well as the bracketed one.
# Optional, `\(t[+\-]?[a-z]{2,4}\)` matched "(Taito)", "(Tape)", "(Trial)" and "(Test)" —
# and because Translation is the FIRST rule, a name carrying one of those had every later
# rule masked, so a real "(Hack)" or "(Demo)" beside it was never seen.
_TRANSLATION_RX = re.compile(
    r"\[t[+\-]\s*[a-z]{2,4}|\(t[+\-][a-z]{2,4}\)|\btranslat\w*|"
    r"\((?:english|french|german|spanish|italian|portuguese|dutch|russian|polish|"
    r"korean|chinese|swedish)\s+(?:v\.?\s*)?\d",
    re.I)

# Ordered by "how un-official" — FIRST match wins when a name carries several tags.
# Translation is checked before Hack (a translated ROM is a translation, not a plain
# hack); every pattern is a PARENTHETICAL/BRACKET tag so a game merely *named* with
# one of these words (".hack", "Prototype") is never mis-flagged.
_RULES = [
    ("Translation", _TRANSLATION_RX),
    ("Hack", re.compile(
        r"\(hack\)|\(hack[ ,\-][^)]*\)|\[h[0-9]*\]|\[t[0-9]*\]", re.I)),
    ("Homebrew", re.compile(
        r"\(aftermarket\)|\(homebrew\)|\(hb\)|\(pd\)|\(public domain\)|"
        r"\((?:19|20)xx\)|\((?:19|20)\?\?\)", re.I)),
    ("Unlicensed", re.compile(
        r"\(unl\)|\(unlicensed\)|\(pirate\)|\[p[0-9]*\]", re.I)),
    ("Prototype", re.compile(
        r"\(proto\)|\(proto[ ,\-][^)]*\)|\(prototype[^)]*\)|\(beta[^)]*\)|"
        r"\(alpha[^)]*\)|\(pre-?release\)|\(debug\)", re.I)),
    ("Demo", re.compile(
        r"\(demo\)|\(demo[ ,\-][^)]*\)|\(sample\)|\(kiosk\)|\(trade demo\)|"
        r"\(taikenban\)", re.I)),
]

# Canonical value set (also the picker options in the attribute editor). A blank /
# unrecognized release_type means a normal commercial release. Translation is
# deliberately NOT treated as "un-official" for match-blocking (it IS the base game).
TYPES = [label for label, _ in _RULES]


def classify(name):
    """Release type for a ROM filename (or None = commercial). Case-insensitive."""
    if not name:
        return None
    for label, rx in _RULES:
        if rx.search(name):
            return label
    return None


# Folder-path provenance: a dump filed under a Homebrew / Hacks / Translations / …
# tree carries that provenance even when its FILENAME has no scene tag — the Atari 2600
# "Doom (2011) (Mr. Podoboo)" lives under `…/Archive/4 Homebrew/`. Matched as whole words
# in any path segment so "4 Homebrew", "ROM Hacks", "Aftermarket Games" are all caught.
# Only the strong, unambiguous folder words (a commercial game's path won't contain them).
_PATH_RULES = [
    ("Translation", re.compile(r"\btranslat\w+\b", re.I)),
    ("Hack", re.compile(r"\b(?:rom[\s_-]*)?hacks\b", re.I)),
    ("Homebrew", re.compile(r"\b(?:home[\s_-]*brew|homebrews|aftermarket)\b", re.I)),
    ("Unlicensed", re.compile(r"\b(?:unlicen[cs]ed|bootlegs?|pirates?)\b", re.I)),
]


def classify_path(path):
    """Release type inferred from a ROM's folder path (its `subdir`), or None."""
    if not path:
        return None
    for label, rx in _PATH_RULES:
        if rx.search(path):
            return label
    return None


def year_from_name(name):
    """A 4-digit release year in a filename parenthetical ('(2011)'), or None — used to
    catch aftermarket/homebrew dumps whose year is impossible for the console era."""
    m = re.search(r"\((19|20)\d{2}\)", name or "")
    return int(m.group(0)[1:5]) if m else None


def extract_language(name):
    """Canonical language of a translation/localization from a ROM filename, or None.
    Only returns a language when the name actually looks like a translation patch, so
    a plain "(USA)" region tag doesn't masquerade as a localization."""
    if not name:
        return None
    n = name.lower()
    m = re.search(r"\[t[+\-]\s*([a-z]{2,4})|\(t[+\-]([a-z]{2,4})\)", n)  # [T+Eng] / (T-Eng)
    if m:
        code = m.group(1) or m.group(2)
        if code in _LANG_CODE:
            return _LANG_CODE[code]
    m = re.search(r"\((english|french|german|spanish|italian|portuguese|dutch|"
                  r"russian|polish|korean|chinese|swedish)\b", n)          # (English …)
    if m and _TRANSLATION_RX.search(name):
        return _LANG_NAME[m.group(1)]
    return None


def extract_version(name):
    """Patch / translation version from a ROM filename ('v1.01' -> '1.01'), or None.
    Requires a dotted version so a bare 'v1' word or a disc/volume number isn't taken
    for a patch version."""
    if not name:
        return None
    m = re.search(r"\bv\.?\s*(\d+(?:\.\d+)+[a-z]?)", name, re.I)   # v1.01, v.1.2a
    if m:
        return m.group(1)
    m = re.search(r"\[t[+\-][a-z]{2,4}[ _]?(\d+\.\d+[a-z]?)", name, re.I)  # [T+Eng1.2]
    return m.group(1) if m else None
