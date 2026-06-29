---
name: games-update
description: Refresh the user's unified game-ownership library — pull current owned games from Steam, Epic, and GOG (cached auth, no prompts), rebuild the deduped catalog that also includes the emulation ROM archive, and report newly added games. Use when the user asks to update/refresh their game library, game collection, or owned-games database, or asks what new games they have.
---

# Update the unified game library

The user keeps a single local SQLite catalog of **every game they own/have**, deduped
by title across sources: **emulation ROMs** (Unraid archive) + **Steam / Epic / GOG**
PC ownership. Each game lists all the sources it's available from.

- Working dir / scripts: `~/game-ownership/`
- Unified DB: `~/game-ownership/game-library.sqlite`
- ROM index (input): `~/roms-index.sqlite`

## To run an update

```bash
bash ~/game-ownership/update.sh
```

This pulls all three stores (auth is cached — Steam via API key in 1Password
`<vault> › Steam Web API (datbird main)`, Epic via legendary, GOG via cached
OAuth token), rebuilds `game-library.sqlite`, and prints the games added since the
last run. Report that "new games" list to the user, plus the totals line.

Add `--roms` to also re-scan the Unraid ROM archive first (slow, ~5 min — only when
the ROM collection changed):

```bash
bash ~/game-ownership/update.sh --roms
```

## Schema (for answering questions)

`games(id, canonical_title, norm_key, n_sources, sources_summary,
       has_emulation, has_steam, has_gog, has_epic)`
`sources(game_id, source, platform, source_id, title_raw, detail)`
— `source` ∈ emulation|steam|gog|epic; for emulation, `platform` is the system
(psx, snes…). `sources_summary` e.g. `emulation:psx,sega saturn; steam; epic`.

## Common queries

```bash
# does the user own a game, and where?
sqlite3 ~/game-ownership/game-library.sqlite \
  "SELECT canonical_title, sources_summary FROM games WHERE norm_key LIKE '%<term>%';"
# games owned on multiple sources
sqlite3 ~/game-ownership/game-library.sqlite \
  "SELECT canonical_title, sources_summary FROM games WHERE n_sources>1 ORDER BY canonical_title;"
# counts per source
sqlite3 ~/game-ownership/game-library.sqlite \
  "SELECT SUM(has_emulation), SUM(has_steam), SUM(has_gog), SUM(has_epic) FROM games;"
```

## Auth notes (only relevant if a pull fails)

- **Steam**: Web API key + the *correct* SteamID `<steam-id>` (account name
  `datbird`). NOTE the vanity `/id/datbird` is a DIFFERENT account
  (`<steam-id-2>`) — never resolve by vanity. The key bypasses profile privacy
  only for its owner's SteamID, so no public profile / login / 2FA is needed.
- **Epic**: `legendary` token in `~/.config/legendary` (auto-refreshes). Re-auth:
  `legendary auth` (browser code).
- **GOG**: cached OAuth token in `~/game-ownership/.gog/tokens.json` (auto-refreshes).
  Re-auth: `python3 ~/game-ownership/gog_owned.py --code <code>` (see login URL in
  that file).
