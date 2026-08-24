# Playnite and LaunchBox

Both are **frontends**, not sources.

[Playnite](https://playnite.link/) and [LaunchBox](https://www.launchbox-app.com/) do
the same job ludodex does — consolidate games across stores and emulators — so treating
either as a source would double-count your entire library. Instead, each imported game
maps to its **underlying provider**: a Playnite EA game becomes source `ea`, a Playnite
Steam game enriches the existing `steam` entry. "It's in your Playnite library" survives
only as the `in_playnite` / `in_launchbox` provenance flag.

What ludodex adds on top is the cross-store title dedup neither of them does natively.

## Playnite

Playnite stores its library in LiteDB, which needs .NET, so a small PowerShell bridge
runs inside Playnite itself. Both sides speak one canonical JSON.

```powershell
# in Playnite: Extensions > Execute script
.\scripts\playnite_bridge.ps1 -Export -Path playnite_games.json        # Playnite -> JSON
.\scripts\playnite_bridge.ps1 -Import -Path ludodex_to_playnite.json   # JSON -> Playnite
```

```bash
# ludodex side
python3 ludodex/config.py set playnite_import_json /path/playnite_games.json
python3 ludodex/playnite_export.py     # catalog -> ludodex_to_playnite.json
```

ludodex adopts Playnite's full attribute vocabulary — genres, tags, features,
categories, developers, publishers, series, age ratings, regions, release date,
playtime, completion status, scores, favourite, version, links — stored in
`game_attributes` for querying and `source_attrs` losslessly for round-trip export.

### Media travels both ways

On `-Export`, Playnite's own art is indexed as the `playnite` media provider, so
hand-curated art can win or seed the chosen set. On the way back, `playnite_export.py`
materializes ludodex's chosen art — including ES-DE scrapes, Steam, IGDB and
ScreenScraper — into a portable bundle beside the JSON. Copy the JSON **and** its
`<name>_media/` folder across, then `-Import` writes them in.

Two knobs control the write:

- `playnite_media_overwrite`
  - `gaps` — fill empty slots only
  - `all` — always replace
  - `playnite-wins` — never clobber, **and** promote your Playnite art to the canonical
    pick everywhere, so it propagates to LaunchBox and the server too
- `playnite_icon_source` — `logo` · `cover` · `none` (Playnite has no separate logo slot)

## LaunchBox

LaunchBox stores everything as plain files — Platform XMLs and `Images/` folders — so no
bridge is needed. ludodex reads and writes the install directly.

```bash
python3 ludodex/config.py set launchbox_path <LaunchBox root>   # scripts/update.sh then imports
python3 ludodex/launchbox_export.py                             # catalog + chosen art -> LaunchBox
python3 ludodex/launchbox_export.py --link                      # symlink art instead of copying
```

Export upserts each game by a **stable per-game GUID**, so it is idempotent: re-runs
update in place and never duplicate or clobber games you added by hand. Multi-value
fields split on `;`, and chosen art lands in the right
`Images/<Platform>/<MediaType>/` folder using LaunchBox's exact filename sanitization.

**Reference mode** (`--link`, or `launchbox_media_mode=link`) keeps one stored copy per
asset — on a NAS, say — shared by every frontend instead of duplicating it per app.

## Both at once

Because both sides read and write the same canonical set, ludodex can sit in the middle:
import from both, consolidate and dedupe, then export to each. Metadata and media stay
in sync across Playnite and LaunchBox without either one having to know the other
exists.
