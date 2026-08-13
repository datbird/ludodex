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
`external_games` map where possible, else by name search through the acceptance gate —
and attaches genres, themes, game modes, developers, publishers, series, release dates,
ratings and age ratings.

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

### Others

**RetroAchievements** for achievement sets, **SteamGridDB** for artwork gap-fill.

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

```bash
python3 ludodex/media_index.py     # scan local providers
python3 ludodex/media_fetch.py     # add remote refs
python3 ludodex/media_choose.py    # pick the ONE best asset per game+kind
python3 ludodex/media_choose.py --materialize --kind cover
```

`scripts/update.sh` runs index → fetch → choose automatically.
