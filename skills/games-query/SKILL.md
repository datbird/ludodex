---
name: games-query
description: Answer questions about the user's game collection from the unified deduped library (game-library.sqlite) — whether they own a game and on which sources (emulation/Steam/Epic/GOG), games available from multiple sources, per-system or per-store listings, counts, and gaps. Use when the user asks "do I have/own <game>", "what do I have for <system/store>", "which games are on multiple sources", or similar collection questions.
---

# Query the unified game library

DB: `~/game-ownership/game-library.sqlite` (built by the `games-update` skill). One row
per deduped game; each lists every source it's available from.

Schema:
- `games(id, canonical_title, norm_key, n_sources, n_kinds, sources_summary,
         has_emulation, has_steam, has_gog, has_epic, has_itch)`
- `sources(game_id, source, platform, source_id, title_raw, detail)` —
  `source` ∈ emulation|steam|gog|epic|itch; emulation `platform` = system (psx, snes…);
  `detail` for emulation = regions.

`norm_key` is the normalized title (lowercase, tags/punct/edition-suffix stripped,
roman→arabic) — **match against `norm_key` with LIKE and a normalized term** for best
recall. `sources_summary` e.g. `emulation:psx,sega saturn; steam; itch`.
**Use `n_kinds>1` for "owned from multiple sources"** (distinct kinds). `n_sources` is
the raw source-row count — a game on 3 emulation systems has n_sources=3 but n_kinds=1,
so n_sources>1 is NOT the cross-source test.

## Patterns

```bash
DB=~/game-ownership/game-library.sqlite
# Do I own <game>, and where?
sqlite3 -column -header "$DB" "SELECT canonical_title, sources_summary FROM games WHERE norm_key LIKE '%<term>%' ORDER BY canonical_title;"
# Full source/platform detail for a game
sqlite3 -column -header "$DB" "SELECT s.source, s.platform, s.title_raw, s.detail FROM games g JOIN sources s ON s.game_id=g.id WHERE g.norm_key LIKE '%<term>%';"
# Everything for a system / store
sqlite3 "$DB" "SELECT DISTINCT g.canonical_title FROM games g JOIN sources s ON s.game_id=g.id WHERE s.platform='psx' ORDER BY 1;"   # or platform='steam'
# Games available from >1 source KIND (cross-source)
sqlite3 -column -header "$DB" "SELECT canonical_title, sources_summary FROM games WHERE n_kinds>1 ORDER BY canonical_title;"
# Owned on a PC store AND emulated
sqlite3 -column -header "$DB" "SELECT canonical_title, sources_summary FROM games WHERE has_emulation=1 AND (has_steam=1 OR has_gog=1 OR has_epic=1 OR has_itch=1);"
# Counts per source
sqlite3 -column -header "$DB" "SELECT SUM(has_emulation) emu, SUM(has_steam) steam, SUM(has_gog) gog, SUM(has_epic) epic, SUM(has_itch) itch, COUNT(*) total FROM games;"
```

## Tips
- Normalize the user's search term the same way (lowercase; drop `:`/`-`/`™`; "&"→"and";
  roman numerals → digits) before the LIKE, e.g. "Tomb Raider II" → `tomb raider 2`.
- If a match looks split across near-duplicate rows (fuzzy-title misses, e.g.
  "Lara Croft Tomb Raider - Legend" vs "Tomb Raider - Legend"), search a shorter
  common substring.
- Data is a point-in-time snapshot; run **games-update** first if freshness matters.
