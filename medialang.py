#!/usr/bin/env python3
"""Per-asset media language: classify each media asset's language from the
structured attributes we persist on it (media.meta JSON), and enforce a user's
ordered language preference by hiding or banning off-language art.

Only ScreenScraper art carries a per-asset language today — its region tag is
persisted into media.meta by media_fetch.fetch_screenscraper. Store art (Steam,
IGDB) is language-neutral and has no region, so it stays UNKNOWN (kept). An asset
is "off-language" only when we can confidently pin it to a SINGLE language that
is not among the user's preferred languages; neutral/multi-region/undetectable
art is never off-language, so a filter run can never strip a game of all its art.

  media_lang_mode  off (default) | hide | ban
  media_languages  comma-joined ordered preference (1st,2nd,3rd), canonical names
"""
import json
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config          # noqa: E402
import mediaflags      # noqa: E402

INDEX_DB = os.path.join(DATA, "media-index.sqlite")

# Canonical language names — must match the picker list in the web UI.
LANGUAGES = ("English", "Japanese", "French", "German", "Spanish", "Italian",
             "Portuguese", "Dutch", "Korean", "Chinese", "Russian", "Polish",
             "Swedish", "Danish", "Norwegian", "Finnish", "Greek", "Turkish",
             "Czech", "Hungarian")

# ScreenScraper region tag -> canonical language, ONLY for regions that imply a
# single language. Multi-language / world / continental / unknown regions are
# deliberately absent -> classified UNKNOWN (kept).
_REGION_LANG = {
    "us": "English", "uk": "English", "gb": "English", "au": "English",
    "nz": "English", "ca": "English", "ie": "English",
    "jp": "Japanese", "kw": "Japanese",
    "fr": "French",
    "de": "German",
    "es": "Spanish", "sp": "Spanish",
    "it": "Italian",
    "pt": "Portuguese", "br": "Portuguese",
    "nl": "Dutch",
    "kr": "Korean", "ko": "Korean",
    "cn": "Chinese", "tw": "Chinese", "hk": "Chinese", "zh": "Chinese",
    "ru": "Russian",
    "pl": "Polish",
    "se": "Swedish", "dk": "Danish", "no": "Norwegian", "fi": "Finnish",
    "gr": "Greek", "tr": "Turkish", "cz": "Czech", "hu": "Hungarian",
}
# ScreenScraper `langue` two-letter code -> canonical language.
_LANGUE_LANG = {
    "en": "English", "ja": "Japanese", "jp": "Japanese", "fr": "French",
    "de": "German", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
    "nl": "Dutch", "ko": "Korean", "zh": "Chinese", "ru": "Russian",
    "pl": "Polish", "sv": "Swedish", "da": "Danish", "no": "Norwegian",
    "fi": "Finnish", "el": "Greek", "tr": "Turkish", "cs": "Czech",
    "hu": "Hungarian",
}


def _norm_lang(name):
    """Accept a canonical name or a code; return the canonical name or None."""
    if not name:
        return None
    s = str(name).strip()
    for lang in LANGUAGES:
        if s.lower() == lang.lower():
            return lang
    return _LANGUE_LANG.get(s.lower())


def asset_language(meta, provider=None):
    """Canonical language for one asset, or None when it can't be pinned to a
    single language (neutral / multi-region / store art / no data = KEPT)."""
    if not meta:
        return None
    try:
        attrs = json.loads(meta)
    except (ValueError, TypeError):
        return None                      # legacy opaque meta (appid/id) -> neutral
    if not isinstance(attrs, dict):
        return None
    explicit = _norm_lang(attrs.get("language"))
    if explicit:
        return explicit
    langue = _LANGUE_LANG.get(str(attrs.get("langue") or "").lower())
    if langue:
        return langue
    region = str(attrs.get("region") or "").split(",")[0].strip().lower()
    return _REGION_LANG.get(region)


def preferred():
    """Ordered list of the user's preferred canonical languages (1st,2nd,3rd…)."""
    raw = config.get("media_languages")
    if not raw:
        one = config.get("media_language")     # back-compat: the old single value
        raw = one or ""
    out = []
    for part in str(raw).split(","):
        lang = _norm_lang(part)
        if lang and lang not in out:
            out.append(lang)
    return out


def preferred_languages():
    """Ordered canonical languages to prefer when CHOOSING art.

    The mirror of `preferred_regions()`, and the direction that was missing. That
    function already falls back to the language preference on the grounds that "I want
    English art" and "I want the US/EU release" are one wish said twice — but only one
    way round: regions had a default and languages had none, so an install that never
    opened the picker held no language opinion at all, and the ordering term that
    depends on one silently did nothing.

    Distinct from `preferred()`, which stays empty when unset because the hide/ban
    FILTER must never act on a preference the user did not express. Choosing between
    two assets is not the same act as deleting one."""
    langs = preferred()
    if langs:
        return langs
    out = []
    for code in preferred_regions():
        lang = _REGION_LANG.get(code)
        if lang and lang not in out:
            out.append(lang)
    return out


def is_off_language(meta, provider=None, prefs=None):
    """True only when an asset can be pinned to a single language the user did not ask
    for. Unpinnable art (store art, multi-region, no data) is never off-language."""
    prefs = preferred_languages() if prefs is None else prefs
    if not prefs:
        return False
    lang = asset_language(meta, provider)
    return lang is not None and lang not in prefs


def mode():
    m = (config.get("media_lang_mode") or "off").lower()
    return m if m in ("off", "hide", "ban") else "off"


def apply_filter(the_mode=None, prefs=None, limit=None):
    """Enforce the language preference over the media index.

    off  -> no-op (also clears any stale hidden flags so disabling re-includes).
    hide -> off-language assets get hidden=1 + unchosen (kept on disk, never
            auto-picked); kept assets get hidden=0.
    ban  -> off-language assets are banned (never re-downloaded) and their index
            row is deleted.
    Returns {mode, scanned, hidden, banned, kept}."""
    the_mode = the_mode or mode()
    prefs = prefs if prefs is not None else preferred()
    res = {"mode": the_mode, "scanned": 0, "hidden": 0, "banned": 0, "kept": 0}
    if not os.path.exists(INDEX_DB):
        return res
    con = sqlite3.connect(INDEX_DB)
    _ensure_hidden(con)
    rows = con.execute(
        "SELECT id, norm_key, kind, provider, ref, meta, hidden FROM media").fetchall()
    to_ban, to_hide, to_show = [], [], []
    for rid, nk, kind, provider, ref, meta, hidden in rows:
        res["scanned"] += 1
        lang = asset_language(meta, provider)
        off = the_mode != "off" and lang is not None and lang not in prefs
        if not off:
            res["kept"] += 1
            if hidden:
                to_show.append(rid)
            continue
        if the_mode == "ban":
            mediaflags.ban(nk, kind, provider, ref)
            to_ban.append(rid)
        else:
            to_hide.append((rid, nk, kind, provider, ref))
    if to_show:
        con.executemany("UPDATE media SET hidden=0 WHERE id=?",
                        [(i,) for i in to_show])
    if to_ban:
        con.executemany("DELETE FROM media WHERE id=?", [(i,) for i in to_ban])
        res["banned"] = len(to_ban)
    if to_hide:
        con.executemany("UPDATE media SET hidden=1, chosen=0 WHERE id=?",
                        [(r[0],) for r in to_hide])
        res["hidden"] = len(to_hide)
    con.commit()
    con.close()
    return res


def _ensure_hidden(con):
    """Idempotent migration: the recomputed-each-run hide flag."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(media)")}
    if "hidden" not in cols:
        con.execute("ALTER TABLE media ADD COLUMN hidden INTEGER DEFAULT 0")
        con.commit()


def main(argv):
    m = None
    for a in argv[1:]:
        if a in ("off", "hide", "ban"):
            m = a
    res = apply_filter(m)
    print("medialang: mode=%s scanned=%d kept=%d hidden=%d banned=%d"
          % (res["mode"], res["scanned"], res["kept"], res["hidden"], res["banned"]),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

# --------------------------------------------------------------------- region
# Region is a DETERMINISTIC signal and was going unused. ScreenScraper stamps every
# asset with its region in `media.meta` ({"type":"box-2D","region":"jp"}), and until now
# that tag was consulted only as a proxy for LANGUAGE, to hide or ban off-language art.
# Nothing used it to CHOOSE. So Contra: Hard Corps served its Japanese box while the US
# box sat beside it at identical size and type, decided by a model reading the artwork
# when the answer was already written down.
#
# Regions that mean "the release the user owns" for a given language, most-preferred
# first. Only used as a DEFAULT — an explicit `media_regions` always wins.
_LANG_REGIONS = {
    "English": ("us", "uk", "gb", "eu", "au", "ca", "wor", "ss"),
    "Japanese": ("jp", "asi", "wor", "ss"),
    "French": ("fr", "eu", "wor", "ss"),
    "German": ("de", "eu", "wor", "ss"),
    "Spanish": ("sp", "es", "eu", "wor", "ss"),
    "Italian": ("it", "eu", "wor", "ss"),
    "Portuguese": ("br", "pt", "eu", "wor", "ss"),
    "Korean": ("kr", "asi", "wor", "ss"),
    "Chinese": ("cn", "tw", "asi", "wor", "ss"),
    "Russian": ("ru", "eu", "wor", "ss"),
}
# With no preference expressed at all, prefer the release most catalogues are built
# around rather than picking arbitrarily.
_DEFAULT_REGIONS = ("us", "uk", "gb", "eu", "wor", "ss")


def preferred_regions():
    """Ordered region codes to prefer when choosing art.

    An explicit `media_regions` wins. Otherwise it follows the LANGUAGE preference,
    because "I want English art" and "I want the US/EU release" are the same wish
    expressed twice, and making the user say it twice is how the two drift apart.
    """
    raw = config.get("media_regions")
    if raw:
        out = []
        for part in str(raw).split(","):
            code = part.strip().lower()
            if code and code not in out:
                out.append(code)
        if out:
            return out
    langs = preferred()
    return list(_LANG_REGIONS.get(langs[0], _DEFAULT_REGIONS)) if langs \
        else list(_DEFAULT_REGIONS)


def region_of(meta):
    """The asset's region code from its `media.meta`, or "" when it has none.

    Store art (Steam, IGDB, SteamGridDB) carries no region and returns "" — which must
    rank as UNKNOWN, never as wrong, or a preference would strip every store cover.
    """
    if not meta:
        return ""
    if isinstance(meta, str):
        if not meta.startswith("{"):
            return ""
        try:
            meta = json.loads(meta)
        except ValueError:
            return ""
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("region") or "").split(",")[0].strip().lower()


def region_rank(meta, prefs=None):
    """Sort rank for an asset's region: 0 = most preferred, higher = less.

    An asset with NO region sits immediately after the preferred list and ahead of a
    known-but-unwanted one. That ordering is the whole point: a neutral store cover is a
    fine answer, and a Japanese box for an owner of the US release is not — but neither
    is ever excluded, only ordered.
    """
    prefs = prefs if prefs is not None else preferred_regions()
    code = region_of(meta)
    if not code:
        return len(prefs)                      # unknown/neutral — acceptable, not ideal
    try:
        return prefs.index(code)
    except ValueError:
        return len(prefs) + 1                  # a region the user did not ask for
