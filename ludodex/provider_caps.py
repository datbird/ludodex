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
    # Steam is a SOURCE and an enrichment provider at once — it supplies genres,
    # categories, developers, publishers, the store description and its own
    # game/dlc/tool classification, for the titles you own there. Leaving it out of a
    # list of "who can fill this" would describe the library as less served than it is.
    "steam": "Steam",
    "steamspy": "SteamSpy",
    "thegamesdb": "TheGamesDB",
    # Two AI origins, not one. `ai` is the model answering from what it already knew —
    # a guess with no citation. `ai_web` is the model having gone and READ something,
    # with sources recorded against the finding. Crediting both as "AI" tells the user
    # the weaker thing about the stronger one.
    #
    # NEITHER of them appears when AI merely MATCHED a game to a provider: those values
    # are IGDB's or ScreenScraper's and are credited to them. The AI did the matching,
    # not the knowing, and the attribute is a fact about where the DATA came from.
    "ai": "AI (from what it knew)",
    "ai_web": "AI Web Search",
    # Not remote providers, but they DO fill attributes, and a tooltip that omits them
    # tells the user nothing can.
    "mobygames": "MobyGames",
    "arcadedb": "ArcadeDB",
    "zxinfo": "ZXInfo",
    "rom": "the ROM filename",
    "xbox": "Xbox",
    "ludodex": "you",
}

# provider -> the config flag that decides whether it is even consulted. Steam is a
# SOURCE as well as a provider and has no metadata_* toggle of its own — see STEAM_NOTE.
ENABLED_KEY = {
    "igdb": "metadata_igdb_enabled",
    "screenscraper": "metadata_screenscraper_enabled",
    "steamspy": "metadata_steamspy_enabled",
    "thegamesdb": "metadata_thegamesdb_enabled",
    "mobygames": "metadata_mobygames_enabled",
    "arcadedb": "metadata_arcadedb_enabled",
    "zxinfo": "metadata_zxinfo_enabled",
}

# Steam is the odd one out and the tooltip has to say so: it is BOTH an ownership source
# and an enrichment provider, and it only ever enriches titles you own there. For a ROM
# that is not "switched off", it is INELIGIBLE — the same distinction the review page
# already draws — so promising it as a filler for an emulated game would be a lie.
STEAM_NOTE = " (Steam-owned titles only)"

# attribute kind -> {provider: what it actually gives}. The note is tooltip copy, so it
# says what arrives, not how it is fetched.
#
# EVERY ENTRY IS CHECKED AGAINST THE PROVIDER'S REAL MAPPER by tests/test_provider_caps.py
# — igdb.map_record, screenscraper.extract_metadata, media_fetch._extract_steam_attrs,
# tgdb_normalize.to_attributes and aimeta.SUPPLEMENT_KINDS are called and their output
# keys must cover what is claimed here. That check found three claims I had invented
# (steam/features, igdb/platforms, screenscraper/regions) and three real capabilities I
# had missed (igdb's esrb_rating, content_descriptors and age_ratings), which is exactly
# why the check exists rather than a careful reading.
CAPS = {
    "release_year": {
        "igdb": "first release year", "screenscraper": "release year",
        "steam": "from the store release date" + STEAM_NOTE,
        "thegamesdb": "year of THIS regional release",
        "mobygames": "the earliest release year across every platform it lists",
        "arcadedb": "for MAME sets: from the official MAME files, keyed on the set name",
        "zxinfo": "for ZX Spectrum titles, from the World of Spectrum archive",
        "ai": "inferred from the title and whatever context the file gives",
        "ai_web": "found by searching the web, with the sources it used recorded against the finding"},
    "release_date": {
        "igdb": "exact first release date",
        "steam": "store release date" + STEAM_NOTE,
        "thegamesdb": "exact date for THIS region — PAL and NTSC differ"},
    "genres": {
        "igdb": "curated genre list", "screenscraper": "genre list",
        "steam": "store genres" + STEAM_NOTE,
        "thegamesdb": "30-genre list; its non-genre entries (Demo, Unofficial, "
                      "Virtual Console\u2026) are split off as flags, never filed here",
        "arcadedb": "MAME's own genre for the set (Maze / Collect, Shooter\u2026)",
        "mobygames": "its 'Basic Genres' only \u2014 the Perspective and Setting categories "
                     "in the same flat list are filed as perspectives and themes "
                     "instead, so 'Adventure' is not buried among '1st-person'",
        "zxinfo": "for ZX Spectrum titles, from the World of Spectrum archive",
        "ai": "inferred when no provider has the game",
        "ai_web": "found by searching the web, with the sources it used recorded against the finding"},
    "themes": {"igdb": "themes (horror, sci-fi, comedy\u2026)",
               "mobygames": "its Setting and Narrative Theme genre categories",
               "ai": "inferred when no provider has the game",
               "ai_web": "found by searching the web, with the sources it used recorded against the finding"},
    "game_modes": {
        "igdb": "single player / co-op / multiplayer",
        "thegamesdb": "derived from its player count + explicit co-op yes/no",
        "arcadedb": "from the cabinet's player count",
        "zxinfo": "from the archive's max-players field",
        "ai": "inferred when no provider has the game",
        "ai_web": "found by searching the web, with the sources it used recorded against the finding"},
    "player_perspectives": {"igdb": "first person, third person, isometric\u2026",
                            "mobygames": "its Perspective genre category",
                            "ai": "inferred when no provider has the game",
                            "ai_web": "found by searching the web, with the sources it used recorded against the finding"},
    "developers": {
        "igdb": "developer companies", "screenscraper": "developer",
        "steam": "store developers" + STEAM_NOTE,
        "thegamesdb": "developer companies",
        "arcadedb": "the arcade manufacturer",
        "zxinfo": "the individual authors, named",
        "ai": "inferred when no provider has the game", "ai_web": "found by searching the web, with the sources it used recorded against the finding"},
    "publishers": {
        "igdb": "publisher companies", "screenscraper": "publisher",
        "steam": "store publishers" + STEAM_NOTE,
        "thegamesdb": "publisher companies",
        "zxinfo": "the publishing label",
        "ai": "inferred when no provider has the game", "ai_web": "found by searching the web, with the sources it used recorded against the finding"},
    "series": {"igdb": "franchise / collection"},
    "description": {
        "igdb": "the IGDB summary paragraph",
        "screenscraper": "the community-written synopsis",
        "steam": "the store short description" + STEAM_NOTE,
        "thegamesdb": "its overview paragraph",
        "mobygames": "its editorial description",
        "arcadedb": "the MAME history.dat entry for the set",
        "ai": "written from what is known about the title",
        "ai_web": "found by searching the web, with the sources it used recorded against the finding"},
    "categories": {
        "steam": "store categories (achievements, cloud saves, controller support\u2026)"
                 + STEAM_NOTE},
    "content_type": {
        "steam": "its own game / dlc / tool / soundtrack classification" + STEAM_NOTE},
    "critic_score": {"igdb": "aggregated critic rating"},
    "community_score": {
        "igdb": "IGDB user rating, 0-100",
        "screenscraper": "community rating, rescaled to 0-100",
        "thegamesdb": "community rating, rescaled to 0-100",
        "mobygames": "Moby Score, rescaled from 0-10 to 0-100"},
    "esrb_rating": {
        "igdb": "the ESRB badge, as its own value rather than parsed back out of a "
                "combined string",
        "thegamesdb": "ESRB as a readable string (\"E - Everyone\")"},
    "content_descriptors": {
        "igdb": "the descriptors behind the badge (Blood and Gore, Strong Language\u2026)"},
    "age_ratings": {"igdb": "every rating body it knows — ESRB, PEGI, USK, CERO\u2026"},
    "tags": {
        "steamspy": "Steam community tags, fetched from SteamSpy and badged as Steam's",
        "ludodex": "tags you set yourself, kept across rebuilds"},
    # --- filled by things that are not remote providers at all -----------------------
    # Left out of the first cut, and their absence made the tooltip say "no provider
    # supplies this" about three kinds that are routinely filled. A wrong tooltip sends
    # someone to switch on a provider that will not help; this one sent them looking for
    # a provider that was never the answer.
    "release_type": {"rom": "read from the filename tags (Proto, Beta, Demo, Hack\u2026)"},
    "language": {"rom": "read from the filename tags (En, Fr, Ja\u2026) and patch info",
                 "arcadedb": "the language the cabinet shipped in",
                 "zxinfo": "the release language"},
    "version": {"rom": "read from the filename tags (v1.1, Rev A\u2026)"},
    "regions": {
        "thegamesdb": "BOTH axes: the TV standard (NTSC-U / PAL / NTSC-J\u2026) and the "
                      "release market — it is the only source that separates them"},
    "os": {
        "xbox": "the platforms an Xbox store entry lists",
        "thegamesdb": "for PC titles: the OS its minimum spec names"},
    "device": {"xbox": "the devices an Xbox store entry lists",
               "zxinfo": "the exact Spectrum variant (48K / 128K / +2) — a 128K-only "
                         "title will not run on a 48K"},
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
