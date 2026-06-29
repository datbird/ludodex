# ludodex

> *ludo* (game) + *-dex* (index) — one local catalog of **every game you own**, across
> emulation ROMs and PC stores, deduped by title and showing all the sources each game
> can be played from.

ludodex pulls game ownership from **Steam, Epic, and GOG**, merges it with an indexed
**emulation ROM archive**, normalizes and **dedupes by title**, and writes a single local
SQLite catalog where each game lists every source it's available from. All store auth is
cached, so refreshing is one command with no prompts.

It's driven through three Claude skills (`skills/`), so the day-to-day interface is just
asking Claude to *update*, *query*, or *re-auth* — but every script runs standalone too.

## Why

> "A local db of all games + their sources, universal between emulation and PC stores,
> deduped showing all sources."

No public-profile toggling, no logins on every run. One DB, deduped, queryable.

## How it works

```
 Steam  ──steam_owned.py─┐
 Epic   ──epic_owned.py──┤                            ┌─ games   (one row per deduped title)
 GOG    ──gog_owned.py───┼─→ build_library.py ──────→ │
 ROMs   ──build_romdb.py─┘     (normalize + dedupe)   └─ sources (every place a game lives)
                                                          → game-library.sqlite
```

- **`steam_owned.py`** — Steam owned games via the Web API `GetOwnedGames`. The Web API
  key bypasses profile privacy **only for its owner's SteamID**, so no public profile /
  login / 2FA is needed — just the key and the correct SteamID. → `steam_games.tsv`
- **`epic_owned.py`** — Epic owned games via `legendary list --json`. → `epic_games.tsv`
- **`gog_owned.py`** — GOG owned games via Galaxy OAuth (`--code` once, then a cached
  refresh token; uses GOG Galaxy's public client credentials). → `gog_games.tsv`
- **`build_romdb.py`** — recursively indexes a ROM archive into `roms-index.sqlite`,
  parsing No-Intro / GoodTools tags for system, region, and version. (Runs where the ROMs
  live.)
- **`build_library.py`** — normalizes titles and dedupes all sources into
  `game-library.sqlite`.
- **`update.sh`** — refresh all stores (cached auth) → rebuild → report games new since
  the last run. `--roms` also re-scans the ROM archive (slow).
- **`auth_status.sh`** — prints `OK`/`BROKEN` per source.

## Skills

Drop `skills/*` into `~/.claude/skills/` (or symlink). Then:

- **games-update** — refresh ownership, rebuild the catalog, report new games.
- **games-auth** — check/repair Steam/Epic/GOG logins (re-auth walkthroughs).
- **games-query** — answer "do I own X / on what sources / what's cross-source / counts".

## Schema

```sql
games(   id, canonical_title, norm_key, n_sources, sources_summary,
         has_emulation, has_steam, has_gog, has_epic )
sources( game_id, source, platform, source_id, title_raw, detail )
-- source ∈ emulation|steam|gog|epic
-- emulation platform = system (psx, snes…); detail = regions
-- sources_summary e.g. "emulation:psx,sega saturn; steam; epic"
```

```bash
DB=game-library.sqlite
# Do I own <game>, and where?
sqlite3 -column -header "$DB" \
  "SELECT canonical_title, sources_summary FROM games WHERE norm_key LIKE '%halo%';"
# Games available from more than one source
sqlite3 -column -header "$DB" \
  "SELECT canonical_title, sources_summary FROM games WHERE n_sources>1;"
# Counts per source
sqlite3 "$DB" \
  "SELECT SUM(has_emulation) emu, SUM(has_steam) steam, SUM(has_gog) gog, SUM(has_epic) epic, COUNT(*) total FROM games;"
```

## Dedup notes

Titles are normalized to a `norm_key` (lowercase; strip region/version/`[..]`/`(..)`
tags, ™®©, trailing ROM extensions like `.m3u`; `&`→`and`; roman→arabic; drop edition
suffixes and leading articles). Distinct subtitles stay distinct (*Tomb Raider* ≠ *Tomb
Raider: Anniversary*). Store titles win as the display name over tag-laden ROM names.
Fuzzy near-misses may stay separate — acceptable.

## Setup / auth

ludodex is built for a personal setup; paths and source locations are configured inline
in the scripts (Steam API key from 1Password, an Unraid ROM share, etc.). Adapt those to
your environment. Auth, once cached, needs no further interaction:

- **Steam** — Web API key (does not expire). Generate at
  https://steamcommunity.com/dev/apikey while logged into the owning account; use that
  account's SteamID64. `steam_owned.py` reads `STEAM_API_KEY` from the environment.
- **Epic** — `legendary auth`, then paste the `authorizationCode` from
  https://legendary.gl/epiclogin. Token auto-refreshes (`~/.config/legendary`).
- **GOG** — `python3 gog_owned.py --code <code>` once (login URL is in the script). A
  refresh token is cached in `.gog/tokens.json` and auto-refreshes.

> **Privacy note:** the SQLite catalogs, per-store ownership dumps (`*_games.tsv`), and
> cached auth tokens (`.gog/`) are `.gitignore`d — only code, skills, and docs are
> tracked. No personal ownership data or credentials are committed.

## License

MIT — see [LICENSE](LICENSE).
