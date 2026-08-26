# Database schema

Everything is SQLite, on your disk, one file per concern. The catalog itself is
`game-library.sqlite`; identity caches, media, config and ownership live alongside it.

## The catalog

```sql
games(   id, canonical_title, norm_key, platform, entry_key, base_key, game_key,
         card_key,                                           -- the library grouping; see below
         n_sources, n_kinds, sources_summary,
         has_emulation, has_steam, has_gog, has_epic, has_itch, has_archive,
         in_playnite, in_launchbox, wanted,
         parent_key, content_kind )                          -- add-ons; see below

sources( game_id, source, platform, source_id, title_raw, detail, state, via_collection, evidence )

source_attrs(    game_id, source, source_id, attrs_json )    -- lossless, per SOURCE
game_attributes( game_id, kind, value, origin )              -- the merged, queryable view
provider_attrs(  game_id, provider, kind, value )            -- lossless, per METADATA PROVIDER
metadata_links(  game_id, provider, provider_id, slug, url ) -- canonical ids (igdb, …)
game_tags(       game_id, tag, origin )                      -- origin: playnite | ludodex | …
identity_review( norm_key, reason, detail )                  -- identities the algo refused
wanted(          game_id, store, store_id, title_raw )       -- which store wants it
```

The whole thing is rebuilt on every run, so nothing durable lives here — the durable
stores are the separate files listed further down.

### The keys, and why there are five

This is the part worth understanding.

| key | means |
|---|---|
| `norm_key` | the title, normalized — region tags, edition suffixes, articles and punctuation stripped |
| `entry_key` | **the unit of identity**: this game, on this platform. One row per entry |
| `base_key` | the game without its platform — groups the same game across hardware |
| `game_key` | the grouping used for media and collections |
| `card_key` | **the unit of DISPLAY**: one per game. `game_key`, folded onto the game an edition belongs to |

*Sonic 2* on Genesis and *Sonic 2* on Game Gear share a `norm_key` and a `base_key`, and
have different `entry_key`s. They are different games that share a name, and collapsing
them is how a Genesis game ends up wearing Game Gear box art.

`card_key` is the display half of the same idea, added 2026-08-25. The rows stay per
platform, because that is what art, ownership and publish all address. The LIBRARY groups
them, so *Dark Souls* is one tile whether you own it on two systems or in three editions.
It folds ports, editions and remasters and never a remake. It is display only: it cannot
bind an identity, gate media, or spend a provider call. See DESIGN §11.10.

### Column notes

- `source` ∈ `emulation | steam | gog | epic | itch | archive | manual | ea | psn |
  xbox | …` — any provider. The `has_*` columns cover the common ones;
  `sources_summary` lists all of them.
- For `emulation`, `platform` is the system (`psx`, `snes`); for `archive` it's the
  archive name.
- `sources_summary` reads like `emulation:psx,snes; archive:ssd-roms; ea; steam`.
- `in_playnite` / `in_launchbox` are **provenance flags**, not sources. A frontend is a
  meta-layer; the game's real source is whatever it came from underneath.
- `n_kinds` counts distinct source *kinds* — use it for "owned from more than one
  place". `n_sources` is the raw row count, so a game on three emulated systems is three
  rows and one kind.
- `state` on a source is `have` or `want`, per format.
- `via_collection` on a source is the `coll_key` of the compilation this copy came from.
- `evidence` on a source records what the store row actually PROVES, when that is
  weaker than a purchase. Empty means the plain claim. `xbox` writes `play-history`,
  because titlehub returns what was launched, which can include Game Pass titles and
  can miss games that were bought and never started.
  `state` stays `have`, because it *is* owned — collection credit is a form of ownership —
  so every existing owned/count/facet query keeps working and the provenance rides along
  beside it.
- **`parent_key` / `content_kind`** — an owned DLC or expansion stays a **full** entry,
  with its own attributes, media, providers and detail page. `content_kind` is `dlc` or
  `expansion`; `parent_key` is the base game's `base_key`. An add-on simply leaves the
  grid (which filters `parent_key IS NULL`) and is listed under the game it extends.
  `parent_key` is **NULL when the parent is not owned** — hiding something you own
  underneath something you do not is strictly worse — while `content_kind` is set either
  way, so Discover can offer the base game as a want.
- `game_attributes.origin` is the comma-joined source(s) that contributed the winning
  value (`steam`, `igdb`, `ai`, …).
- **`provider_attrs` vs `game_attributes`** — `game_attributes` is the merged, winning
  view; `provider_attrs` keeps **every** value each metadata provider contributed per
  kind, including the scalar losers the merge dropped. That is what powers per-provider
  provenance and the disable/re-point cascade: turn a provider off for one game and the
  chosen value is recomputed without it, rather than being lost.
- **`identity_review`** — identities the algorithmic tier *refused*. Algo never guesses:
  when it can prove a match is unsafe (an IGDB bundle id standing in for a single owned
  app) it declines, keeps the entries separate, and records why here. The Light and Heavy
  tiers read this to scope AI match-verification at the games that need it instead of
  sweeping the catalog.

## Queries worth stealing

```bash
DB=game-library.sqlite

# Do I own <game>, and where?
sqlite3 -column -header "$DB" \
  "SELECT canonical_title, sources_summary FROM games WHERE norm_key LIKE '%halo%';"

# Games I own from more than one kind of source
sqlite3 -column -header "$DB" \
  "SELECT canonical_title, sources_summary FROM games WHERE n_kinds > 1;"

# Counts per source
sqlite3 "$DB" "SELECT SUM(has_emulation) emu, SUM(has_steam) steam,
                      SUM(has_gog) gog, SUM(has_epic) epic, COUNT(*) total FROM games;"

# Everything on one platform
sqlite3 -column -header "$DB" \
  "SELECT canonical_title, platform FROM games WHERE platform = 'snes' ORDER BY 1;"
```

## The other databases

`ludodex/reset.py` is the authority on which of these survive a reset, and the grouping
below is its grouping. **Anything not marked "yes" is worth backing up**: it holds either
your own judgement or something that cost a rate-limited round trip to obtain. The
rebuildable ones are deliberately excluded from `ALL` backups so a snapshot stays small.

**The catalog and its caches** — regenerable, cleared by every reset scope:

| file | holds |
|---|---|
| `game-library.sqlite` | the catalog itself |
| `media-index.sqlite` | every known art reference |
| `metadata-cache.sqlite` | provider resolution + payload cache. Also holds `entry_resolution` (this entry's own identity, or a detach), which is a DURABLE USER DECISION sitting in a store `reset.py` classes as an import cache. Worth moving. |
| `card-unfold.sqlite` | `card_unfold(entry_key, pinned_at)`: entries you pinned apart from the game they fold onto. A decision, so it is a curation store, and a library reset keeps it |
| `screenscraper-cache.sqlite` | ScreenScraper responses |
| `crawl-index.sqlite` | the file-crawl inventory and extracted facts |
| `steam-meta.sqlite` | Steam appdetails attribute cache |
| `steam-tags.sqlite` | SteamSpy community tags (30-day TTL) |
| `scores.sqlite` | fetched ratings |
| `os.sqlite` | per-appid OS support |
| `ra.sqlite` | RetroAchievements cache |
| `ai-metadata.sqlite` | AI scan findings |
| `ingest-hints.sqlite` | AI ingest conclusions |
| `sync_cache.sqlite` | the backing store's per-record shadow hashes |
| `roms-index.sqlite`, `roms-index-mgr<n>.sqlite` | ROM indexes (rescan to rebuild) |

**Your decisions** — *not* rebuildable, and the reason backups exist. These are exactly
the stores `dbsync.py` syncs to a backing store:

| file | holds |
|---|---|
| `tags.sqlite` | your tags |
| `pins.sqlite` | art you pinned |
| `attr-overrides.sqlite` | attribute values you overrode |
| `manual-games.sqlite` | games you added by hand |
| `ownership.sqlite` | per-format have/want |
| `collections.sqlite` | compilations and their membership |
| `merges.sqlite`, `splits.sqlite` | entries you merged or peeled apart |
| `framing.sqlite` | per-asset framing + hero preference |
| `user-media.sqlite` | art you supplied yourself |
| `media-flags.sqlite` | assets you banned or marked non-redistributable |
| `identity-disable.sqlite` | providers you turned off per game |

**How to reach the outside world** — only a factory reset clears these:

| file | holds |
|---|---|
| `config.sqlite` | config, mounts, preferences, credentials |
| `connections.sqlite` | device connections + their credentials |
| `file-profiles.sqlite` | file-organization profiles |

**Never removed by any scope** — the way back in, and the way back:
`auth.sqlite` (accounts and sessions), `backups.sqlite` (the backup jobs and archives),
`ai-usage.sqlite` (the spend ledger).

**Provider mirrors and indexes** — rebuildable, but expensive enough that a rebuild is a
real cost: `igdb-catalog.sqlite`, `ss-catalog.sqlite`, `tgdb-catalog.sqlite`,
`moby-catalog.sqlite` (plus `thegamesdb-state.sqlite` and `mobygames-state.sqlite`, the
walk cursors), and `match-index.sqlite`, the optional offline identity index.

## Dedup

Titles normalize to a `norm_key`: lowercased; region, version, `[..]` and `(..)` tags
stripped; ™®© removed; trailing ROM extensions dropped; `&` → `and`; roman numerals →
arabic; edition suffixes and leading articles removed.

Distinct subtitles stay distinct — *Tomb Raider* ≠ *Tomb Raider: Anniversary*. Store
titles win as the display name over tag-laden ROM filenames. Fuzzy near-misses are left
separate rather than merged on a guess.

Two preferences change the aggressiveness:

- `dedupe_preserve_years` — keep `(YYYY)` in the key, so a remake stays separate.
- `dedupe_strip_editions` — merge remasters and GOTY editions into the base game.
