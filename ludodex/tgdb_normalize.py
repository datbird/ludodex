#!/usr/bin/env python3
"""Normalize a TheGamesDB pull into ludodex's vocabulary.

THE REASON THIS FILE EXISTS: TheGamesDB is keyed FINER THAN LUDODEX IS. Its game row is
one per (title, platform, REGION); ludodex's entry is one per (game, platform). Measured
live on 2026-08-16, "Sonic the Hedgehog 2" returns:

    142     Sega Genesis    NTSC-U  country 50  1992-11-24
    124507  Sega Genesis    PAL     country 20  1992-11-24
    113847  Sega Game Gear  NTSC-J  country 28  1992-11-21
    109078  Sega Game Gear  PAL     country 18  1992-12-01
    49847   Arcade          –       country 0   1993-10-01
    105362  Handheld Elec.  NTSC    country 50  1992-01-01

Nothing else we mirror does this. IGDB keeps ONE game row plus a platform join, with
region buried in release_dates we do not mirror. ScreenScraper keeps one `ss_games` row
and puts region on `ss_names.region` and `ss_roms.region` — on names and dumps, never on
identity.

So a matcher that asks TheGamesDB for "Sonic 2 on Genesis" gets TWO answers that are both
right, and scoring cannot separate them because every scored term is identical: same
title, same platform, same year, same developer. THAT IS THE FAIL-OPEN SHAPE — a lookup
returning something the gate reads as consent — and the fix is the same as it always is:
make the ambiguity a first-class result and decide it on a term that actually differs.
`pick_release()` decides it on REGION, and when it cannot, it says so rather than
returning the first row.

The upside is real and it is why this is worth the trouble. ROM filenames carry (USA) /
(Europe) / (Japan), `romtags.parse_name` already extracts them, and TheGamesDB is the
only source that can bind a specific regional dump to a specific regional release with
its own date, its own rating and its own boxart.

TWO REGION AXES, WHICH LUDODEX CONFLATES. TheGamesDB reports `region_id` from a 9-value
TV-standard vocabulary AND `country_id` from a 51-country list. `romtags.REGIONS` is a
market/country vocabulary ("USA", "Europe", "Japan"); NTSC and PAL live in `FLAG_WORDS`,
so today a filename `(PAL)` becomes a FLAG and the hyphenated forms — (NTSC-J), (PAL-B) —
parse as nothing at all. This module reads the TV standard off the raw tag string rather
than changing `romtags`: the parsing is additive here and touching the shared parser
would re-key every ROM in the library to fix one provider's import.
"""
import os
import re
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import romtags                       # noqa: E402

# TheGamesDB's own region table (/v1/Regions), verified live.
REGION_NAME = {0: "", 1: "NTSC", 2: "NTSC-U", 3: "NTSC-C", 4: "NTSC-J", 5: "NTSC-K",
               6: "PAL", 7: "PAL-A", 8: "PAL-B", 9: "Other"}

# TV standard -> the MARKET vocabulary romtags already speaks. Deliberately many-to-one
# and deliberately incomplete: NTSC alone does not identify a market (the USA and Japan
# are both NTSC), so it maps to nothing rather than guessing the USA — which is exactly
# the assumption that puts a Japanese release under an American filename.
REGION_MARKET = {
    "NTSC-U": "USA", "NTSC-C": "China", "NTSC-J": "Japan", "NTSC-K": "Korea",
    "PAL": "Europe", "PAL-A": "Australia", "PAL-B": "Europe",
}

# The reverse: what a file's parsed market implies about the release we want. Used to
# turn "(USA)" on disk into a preference for the NTSC-U row.
MARKET_REGION = {
    "USA": ("NTSC-U", "NTSC"), "Canada": ("NTSC-U", "NTSC"),
    "Japan": ("NTSC-J", "NTSC"), "Korea": ("NTSC-K", "NTSC"),
    "China": ("NTSC-C", "NTSC"), "Taiwan": ("NTSC-C", "NTSC"),
    "Europe": ("PAL", "PAL-B"), "UK": ("PAL", "PAL-B"),
    "United Kingdom": ("PAL", "PAL-B"), "Australia": ("PAL-A", "PAL"),
    "New Zealand": ("PAL-A", "PAL"), "Brazil": ("PAL",),
}

# Every TV standard TheGamesDB knows, matched case-insensitively out of a raw tag blob.
# Longest-first so "NTSC-J" is not consumed as "NTSC".
_TV_RE = re.compile(
    r"\b(" + "|".join(sorted((v for v in REGION_NAME.values() if v and v != "Other"),
                             key=len, reverse=True)).replace("-", r"\-") + r")\b",
    re.I)

# Seven of TheGamesDB's thirty "genres" are not genres. They are release-type or
# non-game markers, and filing them under genres pollutes a facet the whole library
# filters on — while THROWING THEM AWAY would discard signal `nongame` and `homebrew`
# already want. So they are split, not dropped.
GENRE_MARKERS = {
    "Demo": "demo",
    "Unofficial": "unofficial",
    "Virtual Console": "rerelease",
    "GBA Video / PSP Video": "video",
    "Productivity": "application",
    "Utility": "application",
    "Education": "education",
}


def region_of(row):
    """(tv_standard, market) for a TheGamesDB game row. Either may be ''.

    The market is DERIVED from the TV standard, not from `country_id`: the country list
    is 51 entries deep and mostly finer than anything ludodex filters on, whereas the
    9-value standard maps cleanly onto the markets `romtags` already produces."""
    tv = REGION_NAME.get(int(row.get("region_id") or 0), "")
    return tv, REGION_MARKET.get(tv, "")


def tv_standard(raw_tags):
    """The TV standard named in a ROM filename's tag blob, or ''.

    Reads what `romtags` leaves on the floor. `(PAL)` currently lands in FLAG_WORDS and
    `(NTSC-J)` lands nowhere at all, so both are recovered from the raw tags instead —
    additive, and it does not re-key a single existing ROM."""
    m = _TV_RE.search(raw_tags or "")
    return m.group(1).upper() if m else ""


def wanted_regions(filename):
    """What regional release a ROM filename is asking for, best guess first.

    Explicit beats implied: `(NTSC-J)` in the name is a statement, while `(Japan)` only
    implies NTSC-J. Both are returned, in that order, so an exact hit wins and the
    implication is still available as a fallback."""
    _t, market, _l, _v, _r, _d, _f, raw = romtags.parse_name(filename or "")
    out = []
    tv = tv_standard(raw)
    if tv:
        out.append(tv)
    # A file may name several markets — "(USA, Europe)" — and each contributes.
    for part in (market or "").split(","):
        for cand in MARKET_REGION.get(part.strip(), ()):
            if cand not in out:
                out.append(cand)
    return out


def pick_release(rows, filename=None, prefer=("NTSC-U", "NTSC", "PAL", "NTSC-J")):
    """Choose ONE TheGamesDB row from several that differ only by region.

    -> (row, why). `row` is None when the caller must not be handed a guess.

    The ordering of the checks is the whole point:

      * ONE ROW IS NOT A CHOICE. Return it and say so.
      * THE FILE'S OWN REGION WINS. `Sonic 2 (Europe).md` wants the PAL row, whatever
        the configured preference says — the file is evidence and the preference is a
        default.
      * THE PREFERENCE IS A TIE-BREAK, NOT A MATCHER. It only ever runs when the file
        said nothing.
      * ROWS THAT DIFFER BY MORE THAN REGION ARE NOT A REGIONAL SPLIT and must not be
        resolved as one. Different platforms are different entries; returning one of
        them because it sorted first is precisely the fail-open bug.
    """
    rows = [r for r in (rows or []) if r]
    if not rows:
        return None, "no candidates"
    if len(rows) == 1:
        return rows[0], "only candidate"

    plats = {str(r.get("platform")) for r in rows}
    if len(plats) > 1:
        # NOT a regional split. Say what it is instead of picking.
        return None, ("candidates span %d platforms (%s) — that is not a regional "
                      "split, and picking one would be a guess"
                      % (len(plats), ", ".join(sorted(plats))))

    by_region = {}
    for r in rows:
        by_region.setdefault(region_of(r)[0], r)

    for want in (wanted_regions(filename) if filename else []):
        if want in by_region:
            return by_region[want], "the file names %s" % want
    if filename:
        # The file spoke and none of the candidates match it. That is a real miss, not
        # an invitation to fall back to the default — a (Japan) dump must never be
        # resolved to the NTSC-U release just because that is what we usually prefer.
        if wanted_regions(filename):
            return None, ("the file asks for %s and no candidate offers it"
                          % "/".join(wanted_regions(filename)))
    for want in prefer:
        if want in by_region:
            return by_region[want], "preferred region %s" % want
    return None, ("%d candidates, none in the preferred regions (%s)"
                  % (len(rows), ", ".join(sorted(x for x in by_region if x)) or "unset"))


def split_genres(names):
    """-> (genres, flags). Markers never reach the genre facet, and are never dropped."""
    genres, flags = [], []
    for n in names or []:
        n = (n or "").strip()
        if not n:
            continue
        marker = GENRE_MARKERS.get(n)
        if marker:
            if marker not in flags:
                flags.append(marker)
        elif n not in genres:
            genres.append(n)
    return genres, flags


def to_attributes(row, genre_names=(), developer_names=(), publisher_names=()):
    """A TheGamesDB game row -> {ludodex attribute kind: value}.

    Only kinds with a value are returned; an absent field is not the same as an empty
    one, and writing "" would overwrite a better answer from another provider with
    nothing. The lookup tables are passed in because they are per-account constants that
    the caller should fetch once, not once per game."""
    out = {}
    date = (row.get("release_date") or "").strip()
    if date:
        out["release_date"] = date[:10]
        if date[:4].isdigit():
            out["release_year"] = date[:4]

    genres, flags = split_genres(genre_names)
    if genres:
        out["genres"] = genres
    if flags:
        out["release_flags"] = flags        # consumed by nongame/homebrew, not a facet

    if developer_names:
        out["developers"] = list(developer_names)
    if publisher_names:
        out["publishers"] = list(publisher_names)

    ov = (row.get("overview") or "").strip()
    if ov:
        out["description"] = ov
    rating = (row.get("rating") or "").strip()
    if rating:
        out["esrb_rating"] = rating
    # Its community rating is 0-5; every other provider's community_score is 0-100, and
    # a facet that has to know which scale each row came from is a facet that will one
    # day compare them directly.
    try:
        note = float(row.get("rating_community") or row.get("community_rating") or 0)
    except (TypeError, ValueError):
        note = 0.0
    if note > 0:
        out["community_score"] = round(note / 5.0 * 100)

    tv, market = region_of(row)
    regions = [x for x in (tv, market) if x]
    if regions:
        out["regions"] = regions

    # players + coop describe the same fact IGDB calls game_modes, so they are folded
    # into that vocabulary rather than inventing a parallel one.
    modes = []
    try:
        players = int(row.get("players") or 0)
    except (TypeError, ValueError):
        players = 0
    if players == 1:
        modes.append("Single player")
    elif players > 1:
        modes.append("Multiplayer")
    if (row.get("coop") or "").strip().lower() == "yes":
        modes.append("Co-operative")
    if modes:
        out["game_modes"] = modes

    # PC minimum spec. Console rows carry these as null, so this simply does not fire
    # there — no need for a platform test that would then need maintaining.
    if (row.get("os") or "").strip():
        out["os"] = row["os"].strip()
    spec = {k: (row.get(k) or "").strip()
            for k in ("processor", "ram", "hdd", "video", "sound")}
    spec = {k: v for k, v in spec.items() if v}
    if spec:
        out["min_spec"] = spec

    yt = (row.get("youtube") or "").strip()
    if yt:
        out["video_url"] = yt if yt.startswith("http") else (
            "https://www.youtube.com/watch?v=" + yt)
    return out
