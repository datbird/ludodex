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

_PREFS = None


def _prefs():
    global _PREFS
    if _PREFS is None:
        _PREFS = (config.get_bool("dedupe_preserve_years", True),
                  config.get_bool("dedupe_strip_editions", True))
    return _PREFS


def norm(t):
    preserve_years, strip_editions = _prefs()
    s = (t or "").lower()
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
    return " ".join(toks).strip()
