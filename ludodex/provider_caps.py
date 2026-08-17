#!/usr/bin/env python3
"""Which enrichment provider can fill which attribute — the matrix behind the tooltips.

An empty attribute row tells you nothing about WHY it is empty. "Nobody has asked yet",
"the providers we have cannot supply this at all", and "the one provider that could is
switched off" are three different situations with three different fixes, and the UI
rendered all of them as the same blank. This is the table that lets it say which.

MEASURED, NOT ASSUMED. The provider columns below were checked against a live
`game_attributes` (2026-08-16, 2,205 games) rather than read off documentation:

    kind                  igdb    steam   ss     ai
    genres                4,316   2,885   2      9
    themes                5,088   ·       ·      17
    game_modes            3,654   ·       ·      11
    player_perspectives   2,354   ·       ·      16
    series                700     ·       ·      ·
    categories            ·       19,021  ·      ·
    critic_score          1,396   ·       ·      ·
    community_score       1,879   ·       1      ·

The uncomfortable half of that measurement is what is NOT there. `esrb_rating`,
`regions`, `os`, `age_ratings`, `content_descriptors`, `language`, `release_type`,
`version`, `features` and `platforms` are all in the editable vocabulary and had ZERO
rows from ANY provider — they were hand-entry-only and nothing said so. Three of them
(esrb_rating, regions, os) TheGamesDB can actually fill, which is a large part of why it
is worth having at all.

RULE FOR ADDING A PROVIDER: claim only what it demonstrably returns. A tooltip that
promises a provider can fill something it cannot is worse than no tooltip, because it
sends the user to switch on a provider that will not help. `tests/test_provider_caps.py`
holds every claim here to a stated source.
"""
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config                       # noqa: E402

# Human names for the tooltip. Kept here rather than imported from the web layer so the
# API answer is self-describing.
LABEL = {
    "igdb": "IGDB",
    "screenscraper": "ScreenScraper",
    "steam": "Steam",
    "steamspy": "SteamSpy",
    "thegamesdb": "TheGamesDB",
    "ai": "AI (the wand)",
}

# provider -> the config flag that decides whether it is even consulted. Steam is a
# SOURCE as well as a provider and has no metadata_* toggle of its own.
ENABLED_KEY = {
    "igdb": "metadata_igdb_enabled",
    "screenscraper": "metadata_screenscraper_enabled",
    "steamspy": "metadata_steamspy_enabled",
    "thegamesdb": "metadata_thegamesdb_enabled",
}

# attribute kind -> {provider: what it actually gives}. The note is tooltip copy, so it
# says what arrives, not how it is fetched.
CAPS = {
    "release_year": {
        "igdb": "first release year", "screenscraper": "release year",
        "steam": "from the store release date",
        "thegamesdb": "year of THIS regional release"},
    "release_date": {
        "igdb": "exact first release date", "steam": "store release date",
        "thegamesdb": "exact date for THIS region — PAL and NTSC differ"},
    "genres": {
        "igdb": "curated genre list", "screenscraper": "genre list",
        "steam": "store genres",
        "thegamesdb": "30-genre list; its non-genre entries (Demo, Unofficial, "
                      "Virtual Console…) are split off as flags, never filed here"},
    "themes": {"igdb": "themes (horror, sci-fi, comedy…)"},
    "game_modes": {
        "igdb": "single player / co-op / multiplayer",
        "thegamesdb": "derived from its player count + explicit co-op yes/no"},
    "player_perspectives": {"igdb": "first person, third person, isometric…"},
    "developers": {
        "igdb": "developer companies", "screenscraper": "developer",
        "steam": "store developers", "thegamesdb": "developer companies"},
    "publishers": {
        "igdb": "publisher companies", "screenscraper": "publisher",
        "steam": "store publishers", "thegamesdb": "publisher companies"},
    "series": {"igdb": "franchise / collection"},
    "description": {
        "igdb": "the IGDB summary paragraph",
        "screenscraper": "the community-written synopsis",
        "steam": "the store description",
        "thegamesdb": "its overview paragraph"},
    "categories": {"steam": "store categories (achievements, cloud saves, controller…)"},
    "features": {"steam": "store feature flags"},
    "critic_score": {"igdb": "aggregated critic rating"},
    "community_score": {
        "igdb": "IGDB user rating", "screenscraper": "community rating",
        "thegamesdb": "community rating"},
    "content_type": {"steam": "game / application / tool / soundtrack / video"},
    "platforms": {"igdb": "every platform the title released on"},
    # --- kinds NO provider could fill before TheGamesDB ------------------------------
    # Each of these measured ZERO rows from every provider on the live library. They were
    # hand-entry-only and the UI gave no hint of that.
    "esrb_rating": {
        "thegamesdb": "ESRB as a readable string (\"E - Everyone\") rather than an id "
                      "needing a second lookup"},
    "regions": {
        "screenscraper": "region codes on the ROM dumps it knows",
        "thegamesdb": "BOTH axes: the TV standard (NTSC-U / PAL / NTSC-J…) and the "
                      "release country — it is the only source that separates them"},
    "os": {"thegamesdb": "for PC titles: the OS its minimum spec names"},
}

# Kinds the vocabulary offers that NOTHING can currently fill. Listed explicitly rather
# than inferred from a missing key, so the tooltip can say "no provider supplies this"
# as a statement we stand behind instead of as the absence of one.
UNSUPPLIED_NOTE = ("No enrichment provider supplies this — set it yourself, or let the "
                   "wand infer it.")


def providers_for(kind):
    """[(provider, note)] that can fill `kind`, in a stable order."""
    return sorted((CAPS.get(kind) or {}).items())


def enabled(provider):
    """Is this provider actually going to be consulted? Steam has no toggle: it is
    consulted for the games you own there and not for anything else."""
    key = ENABLED_KEY.get(provider)
    if not key:
        return True
    return config.get_bool(key, True)


def configured(provider):
    """Does it have what it needs to run at all? A provider that is switched on but has
    no credentials is a third state, and 'enabled' alone would misreport it as ready."""
    try:
        if provider == "igdb":
            return all(config.igdb_creds())
        if provider == "screenscraper":
            return bool(config.screenscraper_creds().get("devid"))
        if provider == "thegamesdb":
            import thegamesdb
            return bool(thegamesdb.api_key())
    except Exception:                                   # noqa: BLE001
        return False
    return True


def matrix(kinds=None):
    """The whole table, with live enabled/configured state, for the API and the UI.

    Returns {kind: {"providers": [{id,label,note,enabled,configured}], "unsupplied":
    bool}} — `unsupplied` is an explicit claim, not an empty list the caller has to
    interpret."""
    out = {}
    for kind in (kinds if kinds is not None else sorted(CAPS)):
        rows = []
        for pid, note in providers_for(kind):
            rows.append({"id": pid, "label": LABEL.get(pid, pid), "note": note,
                         "enabled": enabled(pid), "configured": configured(pid)})
        out[kind] = {"providers": rows, "unsupplied": not rows}
    return out


def tooltip(kind):
    """One sentence for a native title= tooltip. The UI can render richer, but this
    keeps the wording in one place so the API and any CLI say the same thing."""
    rows = providers_for(kind)
    if not rows:
        return UNSUPPLIED_NOTE
    parts = []
    for pid, note in rows:
        label = LABEL.get(pid, pid)
        if not enabled(pid):
            label += " (off)"
        elif not configured(pid):
            label += " (no credentials)"
        parts.append("%s — %s" % (label, note))
    return "Can be filled by: " + "; ".join(parts)


def main(argv):
    import json
    if "--json" in argv:
        print(json.dumps(matrix(), indent=2))
        return 0
    for kind in sorted(CAPS):
        print("%-22s %s" % (kind, tooltip(kind)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
