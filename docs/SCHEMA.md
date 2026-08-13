# Database schema

Everything is SQLite, on your disk, one file per concern. The catalog itself is
`game-library.sqlite`; identity caches, media, config and ownership live alongside it.

## The catalog

```sql
games(   id, canonical_title, norm_key, platform, entry_key, base_key, game_key,
         n_sources, n_kinds, sources_summary,
         has_emulation, has_steam, has_gog, has_epic, has_itch, has_archive,
         in_playnite, in_launchbox, wanted )

sources( game_id, source, platform, source_id, title_raw, detail, state, via_collection )

source_attrs(    game_id, source, source_id, attrs_json )    -- lossless, per provider
game_attributes( game_id, kind, value )                      -- queryable, aggregated
metadata_links(  game_id, provider, provider_id, slug, url ) -- canonical ids (igdb, …)
wanted( … )                                                  -- want-vs-have
```

### The keys, and why there are four

This is the part worth understanding.

| key | means |
|---|---|
| `norm_key` | the title, normalized — region tags, edition suffixes, articles and punctuation stripped |
| `entry_key` | **the unit of identity**: this game, on this platform. One row per entry |
| `base_key` | the game without its platform — groups the same game across hardware |
| `game_key` | the grouping used for media and collections |

*Sonic 2* on Genesis and *Sonic 2* on Game Gear share a `norm_key` and a `base_key`, and
have different `entry_key`s. They are different games that share a name, and collapsing
them is how a Genesis game ends up wearing Game Gear box art.

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

| file | holds | rebuildable? |
|---|---|---|
| `game-library.sqlite` | the catalog | yes, from sources |
| `config.sqlite` | config, mounts, preferences | **no** |
| `metadata-cache.sqlite` | provider resolutions, learned matches, your overrides | **no** — this is where decisions live |
| `media-index.sqlite` | every known art reference | yes |
| `ownership.sqlite` | per-format have/want | **no** |
| `match-index.sqlite` | the optional offline identity index | yes |
| `igdb-catalog.sqlite`, `ss-catalog.sqlite` | local mirrors of provider catalogs | yes |

The "no" rows are the ones worth backing up — they hold things that cost a rate-limited
round trip, or your own judgement, to obtain. The rebuildable ones are deliberately
excluded from `ALL` backups so a snapshot stays small.

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
