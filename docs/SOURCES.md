# Sources and providers

Two different things, deliberately kept apart:

- A **source** asserts ownership — "this game exists in my library, here".
- A **provider** is consulted for information and adds no ownership.

Confusing the two is how a library ends up claiming you own every game IGDB knows about.

## Ownership sources

| source | what it reads |
|---|---|
| `steam` | owned games via the Steam Web API |
| `epic` | owned games via `legendary` |
| `gog` | owned games via Galaxy OAuth |
| `itch` | owned keys via the itch.io API |
| `ea` | EA account library |
| `psn` | PlayStation Network library |
| `xbox` | Xbox / Microsoft Store library |
| `nintendo` | Nintendo account library, via the Virtual Game Card portal |
| `emulation` | an indexed ROM archive |
| `archive` | any local folder or drive you register |
| `manual` | games you add by hand |

Wishlists are tracked separately for Steam and GOG.

Every source can be toggled:

```bash
python3 ludodex/config.py sources          # list all sources + on/off state
python3 ludodex/config.py disable gog
python3 ludodex/config.py enable gog
```

Credentials for each are covered in **[AUTH.md](AUTH.md)**.

## Local archives and mounts

Register any folder or drive and the crawler indexes it as the `archive` source, deduped
against everything else by title. Mounts live in `config.sqlite` and report live status,
so an unplugged drive is skipped rather than treated as a deletion.

```bash
# kind 'rom':  recurse, first folder = system, ROM/disc files only, tags cleaned
python3 ludodex/config.py mount add /run/media/deck/SDCARD rom     # name defaults to "SDCARD"

# kind 'flat': each immediate child (file or folder) is one title
python3 ludodex/config.py mount add ~/Games flat installers

python3 ludodex/config.py mounts           # paths + mounted/present/MISSING status
python3 ludodex/config.py disable installers
python3 ludodex/config.py mount rm <name>
```

The crawl itself is described in **[PIPELINE.md](PIPELINE.md)**.

## Metadata providers

### IGDB

Resolves each catalog game to a canonical IGDB id — by store id via IGDB's own
`external_games` map where possible, else by name search — and attaches genres, themes,
game modes, developers, publishers, series, release dates, ratings and age ratings.

**How the name search decides, today.** `igdb_enrich.py` uses its own rules, not the
shared acceptance gate:

- `_title_matches` — the candidate's primary name **or any alternate name** must
  normalize *exactly* to the game's `norm_key`. Not a fuzzy score: an exact normalized
  equality.
- `_era_ok` — a candidate is rejected only when its release year is impossible for
  **every** platform the game lives on. So an Apple II ROM (era 1977-1993) rejects a
  2010 movie tie-in of the same name, while a title also owned on Steam — `pc`, which is
  not era-bound — accepts any year.
- `matchgate.pick_by_year` is called for the year tie-break among survivors, and that is
  the *only* part of the gate involved.

`matchgate.score` — the coverage / `numbering_variant` / `safe_aliases` scoring that
`provider_ids.py`, `matchindex.py` and `ra_fetch.py` all run their candidates through —
**is not** applied here, and after review it is staying that way for now. IGDB's rule is
*stricter* than the shared one: exact normalized equality on the primary name and every
alternate name, plus era, plus platform fit, plus uniqueness for store titles. Swapping
in `score()` would LOOSEN the provider every other provider is joined against, which is
the wrong direction. Unifying honestly means giving the shared gate an exact-name mode
and a uniqueness mode, which is a redesign rather than a fix.

What did change: `--era-reheal` no longer overwrites a manual or AI-decided identity,
and it passes the platform through so the platform-fit branch actually runs. Hardware is
now a leg of the shared gate (`matchgate.hardware_ok`), so the ScreenScraper and
TheGamesDB paths check it instead of waiving it. So the accurate sentence is "IGDB
resolves by its own exact-name resolver; the other providers go through the shared
acceptance gate", not "everything goes through the gate".

```bash
# one-time: a free Twitch app at https://dev.twitch.tv/console/apps
python3 ludodex/config.py set igdb_client_id     <client-id>
python3 ludodex/config.py set igdb_client_secret <client-secret>   # env IGDB_CLIENT_SECRET overrides
python3 ludodex/config.py enable igdb                              # on by default; no-ops without creds
```

Cached in `metadata-cache.sqlite`; re-runs fetch only new or stale records
(`igdb_meta_ttl_days`), and `--all` re-does everything. Each link is recorded in
`metadata_links` with the id, slug and URL.

### ScreenScraper

Emulation-focused metadata and media, matched by ROM hash where possible — which needs
no name matching at all — and by name plus system otherwise. Tier-aware and resumable:
it reads your live quota from the server rather than counting locally, and parks a
cooldown rather than hammering a rate limit.

### TheGamesDB

Metadata and box art. The API key is rationed hard — **12,000 requests a month** on the
paid Developer tier — and `ByGameName` costs one request per title and cannot be batched.
So ludodex does not search it per game: `tgdb_mirror.py` walks its id space into a local
catalogue once, and `tgdb_freemap.py` builds a free SHA1 → TheGamesDB-id map so a ROM's
hash buys an id for nothing. `tgdb_normalize.py` reconciles its platform names.

### MobyGames

332,414 games, and the cheapest id space in the stack. Rate-limited per hour rather than
per month, so `moby_mirror.py` walks it into a local catalogue and is resumable — a
relaunch continues rather than restarting the clock.

### ArcadeDB

Arcade metadata **and** media, no key, no quota. Arcade is the one category where every
other provider here is weak, so this is the specialist that covers it.

### ZXInfo

The ZX Spectrum archive. No key, no quota. Narrow and deep, which is the shape the rest
of the stack is missing.

### libretro DATs

The No-Intro and Redump dump databases via libretro-database. Free, and the only source
in the stack that carries a **disc serial** — which is an identifier, where a name is
only a guess.

### Wikidata

Cross-database ids, free and CC0. A cross-reference table is a pointer, not content:
"this game is MobyGames #1234" is a coordinate, so it carries no licensing weight and
costs nothing to use.

### RetroAchievements

Achievement sets and which ones the configured user has earned. An enrichment provider,
never an ownership source.

### SteamGridDB

Artwork gap-fill; needs a key.

### The local mirrors

IGDB, ScreenScraper, TheGamesDB and MobyGames each have a **local mirror**
(`igdb_mirror.py`, `ss_mirror.py`, `tgdb_mirror.py`, `moby_mirror.py`). Matching a ROM
library title-by-title means one rate-limited HTTP round trip per title, forever; a
mirror turns that into a local lookup, and turns a metered subscription into something
permanent. They are built once and kept current incrementally.

> A mirror lives in the data dir, not the repo, and a **rebuild deletes the index** —
> re-mirroring is expensive, so treat those files as data worth backing up.

## Media providers

Art is indexed by reference, then the chosen asset per `(game, platform, kind)` is
materialized on demand.

| provider | kind |
|---|---|
| ES-DE / RetroDECK `downloaded_media` | local |
| your Steam grid folder (`userdata/<id>/config/grid`) | local |
| Playnite's own art | local, via the bridge |
| Steam CDN — capsule, hero, logo by appid | remote |
| IGDB images — cover, artwork, screenshots | remote |
| ScreenScraper | remote |
| SteamGridDB | remote, needs a key |
| TheGamesDB — box art | remote |
| MobyGames | remote |
| ArcadeDB | remote, no key |

```bash
python3 ludodex/media_index.py     # scan local providers
python3 ludodex/media_fetch.py     # add remote refs
python3 ludodex/media_choose.py    # pick the ONE best asset per game+kind
python3 ludodex/media_choose.py --materialize --kind cover
```

`scripts/update.sh` runs index → fetch → choose automatically.
