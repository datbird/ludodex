---
name: games-playnite
description: Import a Playnite library into the unified catalog, or export the catalog to Playnite. Use when the user wants to sync/import/export between ludodex and Playnite, bring in Playnite's metadata/attributes, or push ludodex games (emulation, cross-store) into Playnite.
---

# Playnite import / export

[Playnite](https://playnite.link/) is a unified game-library manager. It is **not a
source** — it's a meta/consolidation layer like ludodex. So each Playnite game maps to
its **underlying provider** (a Playnite EA game → source `ea`; a Steam game enriches the
existing `steam` entry). "In Playnite" is recorded only as the `in_playnite` flag. ludodex
adopts Playnite's full attribute vocabulary (`game_attributes` + lossless `source_attrs`).

Both sides exchange one canonical JSON (defined in `ludodex/playnite.py`). Because
Playnite stores its library in **LiteDB** (a .NET format), a PowerShell **bridge** runs
**inside Playnite** to read/write it.

## Import (Playnite → ludodex)
1. On the Playnite machine, run the bridge (Extensions → Execute script):
   `.\scripts/playnite_bridge.ps1 -Export -Path playnite_games.json`
2. Copy that JSON to the Deck (scp/share), then point ludodex at it:
   `python3 ludodex/config.py set playnite_import_json /path/playnite_games.json`
3. Rebuild: run the **games-update** skill (`bash scripts/update.sh`). Playnite games enrich
   their provider entries; new providers (EA/Battle.net/Xbox/…) appear as sources.

## Export (ludodex → Playnite)
1. `python3 ludodex/playnite_export.py` → writes `ludodex_to_playnite.json`
   (one record per deduped game, full attributes; ludodex-only games get a synthesized
   provider id).
2. Copy it to the Playnite machine and run:
   `.\scripts/playnite_bridge.ps1 -Import -Path ludodex_to_playnite.json`  (creates missing games
   + enriches existing; add `-NoCreate` to only enrich).

## Notes
- Toggle the import with `config.py enable|disable playnite`.
- Playnite is Windows-only today (native Linux build expected 2026); the bridge needs to
  run where Playnite/.NET lives.
- Queries: `in_playnite=1` finds games tracked in Playnite; provider sources are queried
  normally (e.g. `sources.source='ea'`).
