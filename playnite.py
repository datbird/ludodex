#!/usr/bin/env python3
"""Playnite interchange — the canonical record format shared by ludodex and the
Playnite PowerShell bridge (playnite_bridge.ps1).

Both directions speak ONE JSON shape: a list of game records. The bridge maps a
Playnite `Game` <-> this record; ludodex (build_library import / playnite_export
output) reads & writes the same shape, so import/export round-trips.

Canonical record (all keys optional except name):
{
  "name": "Celeste",
  "source": "steam",            # underlying library (steam/gog/epic/itch/ea/
                                #   ubisoft/battlenet/xbox/amazon/playnite)
  "source_id": "504230",        # Playnite GameId (provider id)
  "platforms":   ["PC (Windows)"],
  "genres":      ["Platformer","Indie"],
  "tags":        [...], "features": ["Single Player"], "categories": [...],
  "developers":  ["Extremely OK Games"], "publishers": [...],
  "series":      [...], "age_ratings": [...], "regions": [...],
  "release_date":"2018-01-25", "release_year": 2018,
  "playtime": 36000, "play_count": 12, "completion_status": "Beaten",
  "user_score": 95, "critic_score": 92, "community_score": 90,
  "favorite": true, "hidden": false,
  "version": "1.4", "description": "<p>…</p>", "notes": "",
  "install_dir": "", "is_installed": true, "install_size": 1234567,
  "links": [{"name":"Steam","url":"https://…"}],
  "roms":  [{"name":"…","path":"…"}],
  "added": "2020-01-01T00:00:00Z", "last_activity": "2024-05-01T00:00:00Z",

  # --- media (both ways) ---
  # EXPORT direction (bridge -Export): absolute paths to Playnite's own art, so
  # ludodex can index it as the 'playnite' media provider.
  "cover": "C:/Users/…/library/files/<id>/x.png",
  "background": "…", "icon": "…",
  # IMPORT direction (playnite_export.py -> bridge -Import): art ludodex chose,
  # given as paths RELATIVE to the JSON (a portable bundle ships beside it), plus
  # per-slot booleans the Deck computed from the overwrite policy.
  "cover_file": "ludodex_to_playnite_media/<sha1>.png", "set_cover": true,
  "background_file": "…", "set_background": true,
  "icon_file": "…", "set_icon": false
}
"""

# Playnite built-in library plugin GUIDs -> ludodex source name. The bridge also
# resolves plugin display names, so unknown ids fall back to "playnite".
PLUGIN_GUIDS = {
    "cb91dfc9-b977-43bf-8e70-55f46e410fab": "steam",
    "aebe8b7c-6dc3-4a66-af31-e7375c6b5e9e": "gog",
    "00000002-dbd1-46c6-b5d0-b1ba559d10e4": "epic",
    "00000001-ebb2-4eec-abcb-7c89937a42bb": "itch",
    "85dd7072-2f20-4e76-a007-41035e390724": "ea",
    "c2f038e5-8b92-4877-91f1-da9094155fc5": "ubisoft",
    "e3c26a3d-d695-4cb7-a769-5ff7612c7edd": "battlenet",
    "7e4fbb5e-2ae3-48d4-8ba0-6b30e7a4e287": "xbox",
    "402674cd-4af6-4886-b6ec-0e695bfa0688": "amazon",
    "00000000-0000-0000-0000-000000000000": "playnite",   # manual / emulated
}
SOURCE_GUIDS = {v: k for k, v in PLUGIN_GUIDS.items()}

# Multi-valued attribute kinds (Playnite reference collections) -> stored as
# distinct (kind, value) rows aggregated per game; round-trip as JSON arrays.
LIST_KINDS = ["platforms", "genres", "tags", "features", "categories",
              "developers", "publishers", "series", "age_ratings", "regions",
              "os", "device"]
# Scalar attribute kinds carried per source / aggregated per game.
SCALAR_KINDS = ["release_date", "release_year", "playtime", "play_count",
                "completion_status", "user_score", "critic_score",
                "community_score", "favorite", "hidden", "version",
                "description", "notes", "install_dir", "is_installed",
                "install_size", "added", "last_activity"]


# Playnite's three art slots <-> canonical media kind. (Playnite has no separate
# logo/screenshot slots; 'icon' is sourced per the playnite_icon_source config.)
MEDIA_SLOTS = {"cover": "CoverImage", "background": "BackgroundImage",
               "icon": "Icon"}


def source_for_guid(guid):
    return PLUGIN_GUIDS.get((guid or "").strip().lower(), "playnite")
