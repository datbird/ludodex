---
name: games-update
description: Refresh the user's unified game-ownership library — pull current owned games from Steam, Epic, and GOG (cached auth, no prompts), rebuild the deduped catalog that also includes the emulation ROM archive, and report newly added games. Use when the user asks to update/refresh their game library, game collection, or owned-games database, or asks what new games they have.
---

# Update the unified game library

The user keeps a single local SQLite catalog of **every game they own/have**, deduped
by title across sources: **emulation ROMs** (Unraid archive) + **Steam / Epic / GOG /
itch.io** PC ownership. Each game lists all the sources it's available from.

- Working dir / scripts: `~/game-ownership/`
- Unified DB: `~/game-ownership/game-library.sqlite`
- ROM index (input): `~/roms-index.sqlite`
- Account/environment settings (SteamID, 1Password item, ROM host/paths) live in a
  `config` table — `python3 config.py list` to see them, `config.py set <key> <value>`
  to change. Nothing personal is hardcoded in the scripts.

## To run an update

```bash
bash ~/game-ownership/update.sh
```

This pulls all three stores (auth is cached — Steam via the API key in the configured
1Password item, Epic via legendary, GOG via cached OAuth token), rebuilds
`game-library.sqlite`, and prints the games added since the last run. Report that
"new games" list to the user, plus the totals line.

Add `--roms` to also re-scan the ROM archive first (slow, ~5 min — only when the ROM
collection changed; needs `unraid_host` + `roms_path` set in config):

```bash
bash ~/game-ownership/update.sh --roms
```

## Schema (for answering questions)

`games(id, canonical_title, norm_key, n_sources, n_kinds, sources_summary,
       has_emulation, has_steam, has_gog, has_epic, has_itch)`
`sources(game_id, source, platform, source_id, title_raw, detail)`
— `source` ∈ emulation|steam|gog|epic|itch; for emulation, `platform` is the system
(psx, snes…). `sources_summary` e.g. `emulation:psx,sega saturn; steam; itch`.
**`n_kinds`** = number of distinct source *kinds* (emu/steam/gog/epic/itch) — use this
for "available from multiple sources". **`n_sources`** = raw source-row count (a game on
3 emulation systems has n_sources=3 but n_kinds=1), so don't use n_sources for that.

## Common queries

```bash
# does the user own a game, and where?
sqlite3 ~/game-ownership/game-library.sqlite \
  "SELECT canonical_title, sources_summary FROM games WHERE norm_key LIKE '%<term>%';"
# games owned on multiple source KINDS (cross-source) — use n_kinds, not n_sources
sqlite3 ~/game-ownership/game-library.sqlite \
  "SELECT canonical_title, sources_summary FROM games WHERE n_kinds>1 ORDER BY canonical_title;"
# counts per source
sqlite3 ~/game-ownership/game-library.sqlite \
  "SELECT SUM(has_emulation), SUM(has_steam), SUM(has_gog), SUM(has_epic), SUM(has_itch) FROM games;"
```

## Auth notes (only relevant if a pull fails)

- **Steam**: Web API key (configured 1Password item) + the SteamID in config
  (`python3 config.py get steam_id`) — must be the account that *owns the key*. NOTE a
  vanity URL can resolve to a different account and return 0 games. The key bypasses
  profile privacy only for its owner's SteamID, so no public profile / login / 2FA.
- **Epic**: `legendary` token in `~/.config/legendary` (auto-refreshes). Re-auth:
  `legendary auth` (browser code).
- **GOG**: cached OAuth token in `~/game-ownership/.gog/tokens.json` (auto-refreshes).
  Re-auth: `python3 ~/game-ownership/gog_owned.py --code <code>` (see login URL in
  that file).
- **itch.io**: personal API key (no expiry), resolved by `config.py itch-key`. Re-auth:
  generate at https://itch.io/user/settings/api-keys → `config.py set itch_api_key <key>`.
