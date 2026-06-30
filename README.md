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

## Quick start

```bash
./setup.sh
```

A guided wizard: it initializes the config DB and walks you through obtaining and
entering each credential (with the exact URLs/steps), authenticates Steam/Epic/GOG,
optionally indexes a ROM archive, and builds the first catalog. Re-runnable any time
(existing values are offered as defaults). After that, refresh with `bash update.sh`.

## How it works

```
 Steam   ──steam_owned.py─┐
 Epic    ──epic_owned.py──┤                           ┌─ games   (one row per deduped title)
 GOG     ──gog_owned.py───┼→ build_library.py ──────→ │
 itch.io ──itch_owned.py──┤    (normalize + dedupe)   └─ sources (every place a game lives)
 ROMs    ──build_romdb.py─┘                              → game-library.sqlite
```

- **`steam_owned.py`** — Steam owned games via the Web API `GetOwnedGames`. The Web API
  key bypasses profile privacy **only for its owner's SteamID**, so no public profile /
  login / 2FA is needed — just the key and the correct SteamID. → `steam_games.tsv`
- **`epic_owned.py`** — Epic owned games via `legendary list --json`. → `epic_games.tsv`
- **`gog_owned.py`** — GOG owned games via Galaxy OAuth (`--code` once, then a cached
  refresh token; uses GOG Galaxy's public client credentials). → `gog_games.tsv`
- **`itch_owned.py`** — itch.io owned games via the server-side API (`/profile/owned-keys`)
  with a personal API key. → `itch_games.tsv`
- **`build_romdb.py`** — recursively indexes a ROM archive into `roms-index.sqlite`,
  parsing No-Intro / GoodTools tags for system, region, and version. (Runs where the ROMs
  live.)
- **`build_library.py`** — normalizes titles and dedupes all sources into
  `game-library.sqlite`.
- **`update.sh`** — refresh all stores (cached auth) → rebuild → report games new since
  the last run. `--roms` also re-scans the ROM archive (slow). Pushes to a remote DB if
  `sync_target` is set.
- **`auth_status.sh`** — prints `OK`/`BROKEN` per source.
- **`sync.py`** — mirror the catalog to a remote PocketBase / Firebase DB (see below).

## Skills

Drop `skills/*` into `~/.claude/skills/` (or symlink). Then:

- **games-update** — refresh ownership, rebuild the catalog, report new games.
- **games-auth** — check/repair Steam/Epic/GOG/itch.io logins (re-auth walkthroughs).
- **games-query** — answer "do I own X / on what sources / what's cross-source / counts".

## Schema

```sql
games(   id, canonical_title, norm_key, n_sources, n_kinds, sources_summary,
         has_emulation, has_steam, has_gog, has_epic, has_itch )
sources( game_id, source, platform, source_id, title_raw, detail )
-- source ∈ emulation|steam|gog|epic|itch
-- emulation platform = system (psx, snes…); detail = regions
-- sources_summary e.g. "emulation:psx,sega saturn; steam; itch"
-- n_kinds  = # of distinct source kinds  -> use for "owned from multiple sources"
-- n_sources = raw source-row count (a game on 3 emu systems = 3 rows, 1 kind)
```

```bash
DB=game-library.sqlite
# Do I own <game>, and where?
sqlite3 -column -header "$DB" \
  "SELECT canonical_title, sources_summary FROM games WHERE norm_key LIKE '%halo%';"
# Games available from more than one source kind (cross-source)
sqlite3 -column -header "$DB" \
  "SELECT canonical_title, sources_summary FROM games WHERE n_kinds>1;"
# Counts per source
sqlite3 "$DB" \
  "SELECT SUM(has_emulation) emu, SUM(has_steam) steam, SUM(has_gog) gog, SUM(has_epic) epic, COUNT(*) total FROM games;"
```

## Sync to a remote DB (optional)

`sync.py` mirrors the catalog (`games` + `sources`) one-way to a remote backend so
other devices/apps can read it. Targets: **PocketBase** (self-hosted) and/or **Firebase
Firestore**. Set `sync_target` (`pocketbase` | `firebase` | `both`) and `update.sh` pushes
after every rebuild; or run it directly:

```bash
python3 sync.py both --dry-run     # show what would be pushed, write nothing
python3 sync.py pocketbase         # force a target
python3 sync.py                    # use the configured sync_target
```

- **PocketBase** — set `pocketbase_url`, `pocketbase_admin_email`, and a password
  (`pocketbase_admin_password` locally, or `pocketbase_op_item` in 1Password, or the
  `POCKETBASE_PASSWORD` env). Collections `games`/`sources` are auto-created; each sync
  is a full replace (remote == local). Uses the batch API when available, else parallel
  per-record writes.
- **Firebase** — set `firebase_project_id` and `firebase_sa_json` (path to a
  service-account key, gitignored). Needs `google-auth` (`uv pip install google-auth`).
  Upserts by deterministic doc id and prunes stale docs.

## Dedup notes

Titles are normalized to a `norm_key` (lowercase; strip region/version/`[..]`/`(..)`
tags, ™®©, trailing ROM extensions like `.m3u`; `&`→`and`; roman→arabic; drop edition
suffixes and leading articles). Distinct subtitles stay distinct (*Tomb Raider* ≠ *Tomb
Raider: Anniversary*). Store titles win as the display name over tag-laden ROM names.
Fuzzy near-misses may stay separate — acceptable.

## Configuration

`./setup.sh` (above) handles this interactively. Under the hood, account- and
environment-specific values are **not hardcoded** — they live in a `config` table inside
`config.sqlite` (gitignored), managed by `config.py`. Only safe/public defaults ship in
the code. To tweak values directly:

```bash
python3 config.py list      # show all keys, values, and descriptions
python3 config.py set steam_id 7656119...      # set one value
python3 config.py get steam_id                 # read one (used by the shell scripts)
```

| key | what it is |
|-----|------------|
| `steam_id` | SteamID64 of the account that **owns** your Steam Web API key |
| `steam_api_key` | the Steam key, stored locally (gitignored) — *or* leave blank and use 1Password |
| `itch_api_key` | itch.io API key, stored locally — *or* blank + use 1Password (`itch_key_op_item`) |
| `op_vault` / `steam_key_op_item` | 1Password vault + item holding the Steam key (`apikey` field) |
| `library_db` / `roms_index_db` | output catalog + ROM-index DB paths |
| `unraid_host` / `roms_path` | ssh target + ROM archive path (only for `update.sh --roms`) |
| `gog_client_id` / `gog_client_secret` | GOG Galaxy's public OAuth client (defaults work) |

Behavior **preferences** live in the same table (`1`/`0`):

| pref | effect |
|------|--------|
| `steam_include_free` | count played free-to-play games (TF2, Dota) as owned (`1`) vs strict ownership (`0`) |
| `dedupe_preserve_years` | keep `(YYYY)` in the dedupe key so a remake stays separate from the original (`1`) |
| `dedupe_strip_editions` | merge remasters/editions (Remastered, GOTY…) into the base game (`1`) |

The Steam key is resolved at runtime as **`STEAM_API_KEY` env → `steam_api_key` config →
1Password** (`config.py steam-key`), so you can store it whichever way you prefer; config
and 1Password both stay out of git.

## Auth

Once cached, auth needs no further interaction:

- **Steam** — Web API key (does not expire). Generate at
  https://steamcommunity.com/dev/apikey while logged into the owning account; use that
  account's SteamID64. `steam_owned.py` reads `STEAM_API_KEY` from the environment.
- **Epic** — `legendary auth`, then paste the `authorizationCode` from
  https://legendary.gl/epiclogin. Token auto-refreshes (`~/.config/legendary`).
- **GOG** — `python3 gog_owned.py --code <code>` once (login URL is in the script). A
  refresh token is cached in `.gog/tokens.json` and auto-refreshes.
- **itch.io** — generate a key at https://itch.io/user/settings/api-keys and store it
  (`config.py set itch_api_key <key>`, or 1Password). The key doesn't expire.

> **Privacy note:** the SQLite catalogs, per-store ownership dumps (`*_games.tsv`), and
> cached auth tokens (`.gog/`) are `.gitignore`d — only code, skills, and docs are
> tracked. No personal ownership data or credentials are committed.

## License

MIT — see [LICENSE](LICENSE).
