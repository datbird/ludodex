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

> **Where it's headed:** see [`DESIGN.md`](DESIGN.md) for the canonical spec and roadmap
> — the **Device layer** (push curated games + metadata/media to named devices, with a
> detect/pin install ledger, provenance, changelog, and conflict awareness) and the
> Build-now / Next / Someday docket. For a single-page orientation + the full plan for
> the **AI-forward server**, see [`HANDOFF.md`](HANDOFF.md).

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
  live.) Shares its tag parser with `crawl.py` via **`romtags.py`**.
- **`crawl.py`** / **`process.py`** — the local-archive pipeline (see *Sources*):
  crawl appends new files to a raw inventory; process extracts system/title/attributes
  and flags variants of known games. Ingested as the `archive` source.
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
         has_emulation, has_steam, has_gog, has_epic, has_itch, has_archive,
         in_playnite, in_launchbox )
sources( game_id, source, platform, source_id, title_raw, detail )
source_attrs(    game_id, source, source_id, attrs_json )   -- lossless per-provider
game_attributes( game_id, kind, value )                     -- queryable, aggregated
metadata_links(  game_id, provider, provider_id, slug, url )-- canonical ids (igdb, …)
-- source ∈ emulation|steam|gog|epic|itch|archive|manual|ea|ubisoft|battlenet|xbox|amazon|…
--   (any provider; the has_* columns cover the common ones, sources_summary lists all)
-- emulation platform = system (psx, snes…); archive platform = archive name
-- sources_summary e.g. "emulation:psx,snes; archive:ssd-roms; ea; steam"
-- in_playnite / in_launchbox = provenance flags (game is in that frontend) — NOT sources
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

## Sources (enable/disable + local archives)

Every source can be toggled, and you can add your own local directories as sources.

```bash
python3 config.py sources                 # list all sources + on/off state
python3 config.py disable gog             # skip a built-in (steam|epic|gog|itch|emulation)
python3 config.py enable gog
```

**Local archives / mounts** — register any local folder or drive (SD card, USB, NAS
mount) and the crawler scans it into the catalog as the `archive` source (deduped against
everything by title). Mounts live in the profile DB (`config.sqlite`) and show live
status, so a removable drive that isn't plugged in is just skipped — its already-indexed
games stay in the catalog.

```bash
# kind 'rom':  recurse, first folder = system, ROM/disc files only, tags cleaned
python3 config.py mount add /run/media/deck/SDCARD rom        # name defaults to "SDCARD"
# kind 'flat': each immediate child (file or folder) is one title
python3 config.py mount add ~/Games flat installers          # explicit name

python3 config.py mounts                  # list paths + mounted/present/MISSING status
python3 config.py disable installers      # mounts toggle like any source
python3 config.py mount rm <name>
```

This is a **two-stage pipeline** over a persistent `crawl-index.sqlite` (gitignored):

1. **`crawl.py`** — append-only inventory. Walks each enabled archive and records raw
   file facts (full path, name, ext, size, mtime) into a `files` table, adding **only new
   files** (existing ones just touch `last_seen`; a changed file is re-flagged).
2. **`process.py`** — reads unprocessed `files` and extracts into an `extracted` table:
   system (from the path), cleaned game title + dedupe key, region/languages/version/
   revision/disc/dump-flags, and whether the file is a **variant of a game already in the
   catalog** (`is_variant` + `base_norm_key`). Marks each file processed → incremental.

`build_library.py` ingests `extracted`; `update.sh` runs both stages before each rebuild,
so archives refresh with every **games-update**. (`process.py --all` re-extracts
everything.)

## Metadata providers (enrich attributes — IGDB)

A **metadata provider** is *consulted* to fill in attributes; it is **not a source** (it
adds no ownership). **[IGDB](https://www.igdb.com/)** resolves each catalog game to a
canonical IGDB id — by Steam appid via its `external_games` map, else by name search —
and attaches genres, themes, game modes, developers/publishers, series, release dates and
ratings. The merge is **fill-gaps only**: if a game already has values for a kind (from a
store or Playnite), IGDB leaves that kind untouched, so owned-source data always wins.

```bash
# one-time: Twitch app creds (free) at https://dev.twitch.tv/console/apps
python3 config.py set igdb_client_id     <client-id>
python3 config.py set igdb_client_secret <client-secret>   # env IGDB_CLIENT_SECRET overrides
python3 config.py enable igdb            # on by default; no-ops without creds
```

IGDB data is cached in `metadata-cache.sqlite` (gitignored) by **`igdb_enrich.py`** —
re-runs only fetch new/stale records (`igdb_meta_ttl_days`); `--all` re-does everything.
`update.sh` runs enrichment after each rebuild, then re-merges. Each link is recorded in
`metadata_links` (provider `igdb`, the id, slug and `igdb.com` URL).

## Media (covers, backgrounds, logos, screenshots…)

Game art is indexed **by reference** into `media-index.sqlite`, keyed by the catalog's
stable `norm_key`, from several **media providers**:

- **ES-DE** (local) — RetroDECK/EmuDeck `downloaded_media` sets, matched to emulation
  games by ROM filename. Register the folder: `config.py media-mount add "<path>" esde`.
- **Steam grid** (local) — your custom Steam artwork (`userdata/<id>/config/grid`).
- **Steam CDN / IGDB images** (remote) — capsule/hero/logo by appid; IGDB
  cover/artwork/screenshots by id. **SteamGridDB** (remote, needs a key) gap-fills.

```bash
python3 media_index.py          # scan local providers (ES-DE, Steam grid)
python3 media_fetch.py          # add remote refs (Steam CDN, IGDB images)
python3 media_choose.py         # pick the ONE best asset per game+kind (by priority)
python3 media_choose.py --materialize --kind cover   # pull chosen bytes into media/ repo
```

Hybrid storage: everything is indexed as a reference; only the **chosen** asset per kind
is materialized into a content-addressed local repo (`media/`, gitignored) on demand —
so the index is cheap and complete while the repo stays small. `update.sh` runs
index → fetch → choose automatically.

## Playnite interoperability (import/export)

[Playnite](https://playnite.link/) is a unified library manager that — like ludodex —
*consolidates* games across stores/emulators. So Playnite is **not** treated as a source:
each imported game maps to its **underlying provider** (a Playnite EA game → source `ea`,
a Steam game → enriches the existing `steam` entry). "In your Playnite library" is kept
only as the `in_playnite` provenance flag. ludodex's title-dedup is the cross-store merge
Playnite lacks natively.

Both sides speak one canonical JSON (see `playnite.py`). A PowerShell bridge runs inside
Playnite (it stores its library in LiteDB, which needs .NET):

```powershell
# in Playnite (Extensions > Execute script):
.\playnite_bridge.ps1 -Export -Path playnite_games.json     # Playnite -> JSON
.\playnite_bridge.ps1 -Import -Path ludodex_to_playnite.json  # JSON -> Playnite (create+enrich)
```

```bash
# ludodex side:
python3 config.py set playnite_import_json /path/playnite_games.json   # then update.sh ingests it
python3 playnite_export.py                                  # catalog -> ludodex_to_playnite.json
```

ludodex adopts Playnite's full attribute vocabulary (genres, tags, features, categories,
developers, publishers, series, age ratings, regions, release date, playtime, completion,
scores, favorite, version, links, …), stored in `game_attributes` (queryable) and
`source_attrs` (lossless, for round-trip export).

**Media both ways.** The bridge also carries cover/background/icon. On `-Export`,
Playnite's own art is indexed as the `playnite` media provider (`playnite_import.py`),
so your hand-curated art can win/seed the chosen set. On the way back,
`playnite_export.py` materializes ludodex's **chosen** art (including ES-DE scrapes,
Steam, IGDB, ScreenScraper) into a portable bundle beside the JSON — copy the JSON
**and** its `<name>_media/` folder to the Playnite machine, then `-Import` writes them
in. Two knobs control the write:

- `playnite_media_overwrite` = `gaps` (fill empty slots only) · `all` (always replace)
  · `playnite-wins` (never clobber **and** make your Playnite art the canonical pick
  everywhere — it then propagates to LaunchBox and the server too).
- `playnite_icon_source` = `logo` · `cover` · `none` (Playnite has no separate logo slot).

This same canonical art set is what `launchbox_export.py` pushes, so metadata **and
media** stay in sync **across Playnite and LaunchBox**.

## LaunchBox interoperability (import/export)

[LaunchBox](https://www.launchbox-app.com/) is treated exactly like Playnite — a
frontend **meta-layer**, not a source (imported games map to their underlying
provider; `in_launchbox` is provenance only). Unlike Playnite it stores everything
as plain files (Platform XMLs + `Images/` folders), so **no bridge is needed** —
ludodex reads and writes the install directly:

```
python3 config.py set launchbox_path <LaunchBox root>   # update.sh then imports it
python3 launchbox_export.py            # catalog + chosen art -> LaunchBox
python3 launchbox_export.py --link     # symlink art -> media_repo/<sha1> (1 stored copy)
```

Export upserts each game by a **stable per-game GUID** (idempotent — re-runs update
in place and never duplicate or clobber hand-added games), splits multi-value fields
on `;`, and drops chosen art into the right `Images/<Platform>/<MediaType>/` folder
with LaunchBox's exact filename sanitization. **Reference mode** (`--link`, or
`launchbox_media_mode=link`) keeps one stored copy per asset (e.g. on Unraid) shared
by every frontend instead of duplicating. The same canonical pipeline can thus sync
metadata + media **between LaunchBox and Playnite** (import both → consolidate →
export to each).

## Sync to a remote DB (optional)

`sync.py` mirrors the catalog (`games` + `sources`) one-way to a remote backend so
other devices/apps can read it. Targets: **PocketBase** (self-hosted) and/or **Firebase
Firestore**. Set `sync_target` (`pocketbase` | `firebase` | `both`) and `update.sh` pushes
after every rebuild; or run it directly:

```bash
python3 sync.py both --dry-run     # show what would be pushed, write nothing
python3 sync.py pocketbase         # force a target
python3 sync.py --reconcile        # self-heal: ignore cache, repair remote drift
python3 sync.py                    # use the configured sync_target
```

It's **incremental and idempotent**: record ids are deterministic (hash of the natural
key), and a local content-hash cache (`sync_cache.sqlite`, gitignored) means each run
pushes only new/changed/removed records — a no-op re-sync is ~1s. Transient HTTP errors
(429/5xx/network) retry with exponential backoff. `--reconcile` ignores the cache,
re-asserts every record, and prunes any remote doc missing locally — use it after a lost
cache, manual remote edits, or a failed partial run.

- **PocketBase** — set `pocketbase_url`, `pocketbase_admin_email`, and a password
  (`pocketbase_admin_password` locally, or the `POCKETBASE_PASSWORD` env). Collections
  `games`/`sources` are auto-created. Upserts are
  idempotent (create↔patch self-heal per record); uses the batch API when enabled, else
  parallel per-record writes.
- **Firebase (Firestore)** — one-time setup:
  1. Create/pick a project at <https://console.firebase.google.com>.
  2. **Build → Firestore Database → Create database** (Native mode).
  3. **Project settings → Service accounts → Generate new private key** → downloads a
     JSON (or in Google Cloud: a service account with role *Cloud Datastore User*).
  4. Put the JSON on the machine and `python3 config.py set firebase_sa_json <path>`
     (it's gitignored), plus `firebase_project_id`. Optional: `firebase_database` (for a
     named, non-default DB) and `firebase_collection_prefix`.
  5. Install the one dependency: `python3 -m pip install --user -r requirements-firebase.txt`.

  Collections `<prefix>games`/`<prefix>sources` are upserted by deterministic doc id
  (`norm_key` for games), and docs no longer present locally are pruned — so the remote
  mirrors local. `has_*` are stored as booleans, counts as integers.

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
| `steam_api_key` | the Steam Web API key, stored locally (gitignored); env `STEAM_API_KEY` overrides |
| `itch_api_key` | itch.io API key, stored locally (gitignored); env `ITCH_API_KEY` overrides |
| `library_db` / `roms_index_db` | output catalog + ROM-index DB paths |
| `unraid_host` / `roms_path` | ssh target + ROM archive path (only for `update.sh --roms`) |
| `gog_client_id` / `gog_client_secret` | GOG Galaxy's public OAuth client (defaults work) |

Behavior **preferences** live in the same table (`1`/`0`):

| pref | effect |
|------|--------|
| `steam_include_free` | count played free-to-play games (TF2, Dota) as owned (`1`) vs strict ownership (`0`) |
| `dedupe_preserve_years` | keep `(YYYY)` in the dedupe key so a remake stays separate from the original (`1`) |
| `dedupe_strip_editions` | merge remasters/editions (Remastered, GOTY…) into the base game (`1`) |

The Steam key is resolved at runtime as **`STEAM_API_KEY` env → `steam_api_key` config**
(`config.py steam-key`). ludodex reads credentials only from env vars or
`config.sqlite` (gitignored) — never from 1Password or any external store at runtime.

## Auth

**See [AUTH.md](AUTH.md) for the complete, authoritative guide** to obtaining and
wiring up the credentials for *every* integration (ownership sources, metadata and
media providers, and remote sync), plus the env → config credential precedence.

Quick version — once cached, auth needs no further interaction:

- **Steam** — Web API key (no expiry). https://steamcommunity.com/dev/apikey + the
  owning account's SteamID64.
- **Epic** — `legendary auth` → paste the code from https://legendary.gl/epiclogin.
- **GOG** — `python3 gog_owned.py --code <code>` once; refresh token auto-renews.
- **itch.io** — key from https://itch.io/user/settings/api-keys.
- **EA** — browser-minted token (`ea_owned.py --token …`); see AUTH.md (EA's Akamai
  shield blocks headless refresh, so it's a re-grab-on-demand source).
- **IGDB** (metadata) — free Twitch app at https://dev.twitch.tv/console/apps.
- **ScreenScraper** (emulation metadata + media) — your account + a software
  `devid` (request on their forum); tier-aware, resumable scraper. See AUTH.md.
- **SteamGridDB** (media) — key from https://www.steamgriddb.com/profile/preferences/api.

Get the exact steps for any integration right from the CLI:
`python3 config.py integrations` (overview + which are configured) or
`python3 config.py integrations <id>` (e.g. `ea`, `igdb`). Verify all sources at
once: `bash auth_status.sh`.

> **Privacy note:** the SQLite catalogs, per-store ownership dumps (`*_games.tsv`), and
> cached auth tokens (`.gog/`, `.ea/`) are `.gitignore`d — only code, skills, and docs
> are tracked. No personal ownership data or credentials are committed.

## License

**Code:** MIT — see [LICENSE](LICENSE).

**Game data:** not MIT, and not shipped here. This repository contains no copy of anyone's
game catalog — ludodex fetches data using credentials *you* supply, so a clone gives you
code, not data. The providers' own terms apply to what you fetch.

One thing worth knowing before you fork commercially: the optional prebuilt **match index**
is derived from ScreenScraper's database and is therefore **CC BY-NC-SA 4.0** —
attribution, non-commercial, share-alike. MIT lets you sell a fork of this code; that
licence still does not let you ship or sell the index with it. Building your own index
locally from your own API access is unaffected.

See [DATA-LICENSE.md](DATA-LICENSE.md) for the full breakdown and per-provider terms.
