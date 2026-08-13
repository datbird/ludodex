# Configuration

Nothing account- or environment-specific is hardcoded. It all lives in a `config` table
inside `config.sqlite` (gitignored), managed by `config.py`. Only safe, public defaults
ship in the code.

Most people never touch this — **Settings** in the web UI writes the same table, and
`scripts/setup.sh` walks through it interactively on first run.

```bash
python3 ludodex/config.py list                    # every key, value and description
python3 ludodex/config.py set steam_id 7656119…   # set one
python3 ludodex/config.py get steam_id            # read one
```

## Keys

| key | what it is |
|---|---|
| `steam_id` | SteamID64 of the account that **owns** your Steam Web API key |
| `steam_api_key` | the Steam Web API key; env `STEAM_API_KEY` overrides |
| `itch_api_key` | itch.io API key; env `ITCH_API_KEY` overrides |
| `igdb_client_id` / `igdb_client_secret` | Twitch app credentials for IGDB |
| `gog_client_id` / `gog_client_secret` | GOG Galaxy's public OAuth client — the defaults work |
| `library_db` / `roms_index_db` | output catalog and ROM-index paths |
| `unraid_host` / `roms_path` | ssh target and ROM archive path, for `scripts/update.sh --roms` |
| `launchbox_path` | LaunchBox install root |
| `playnite_import_json` | path to a Playnite export to ingest |
| `sync_target` | `pocketbase` · `firebase` · `both` |

Credentials for every integration, and how to obtain them, are in **[AUTH.md](AUTH.md)**.

## Behaviour preferences

Stored in the same table as `1` / `0`.

| pref | effect |
|---|---|
| `steam_include_free` | count played free-to-play games (TF2, Dota) as owned, vs strict ownership |
| `dedupe_preserve_years` | keep `(YYYY)` in the dedupe key, so a remake stays separate from its original |
| `dedupe_strip_editions` | merge remasters and editions (Remastered, GOTY…) into the base game |
| `igdb_meta_ttl_days` | how long a cached IGDB record stays fresh |
| `playnite_media_overwrite` | `gaps` · `all` · `playnite-wins` — see [FRONTENDS.md](FRONTENDS.md) |
| `playnite_icon_source` | `logo` · `cover` · `none` |
| `launchbox_media_mode` | `copy` · `link` |
| `matchindex.prefer` | `dynamic` (your own data first) · `supplement` |
| `matchindex.path` | where the optional match index lives |

## Credential precedence

Every credential resolves the same way: **environment variable → `config.sqlite`**.

So `STEAM_API_KEY` in the environment beats `steam_api_key` in the database, which is
what makes the Docker `.env` file work without writing secrets into the volume.

ludodex reads credentials **only** from environment variables or `config.sqlite`. It
never reaches out to a password manager or any external store at runtime.

## Integration help from the CLI

```bash
python3 ludodex/config.py integrations        # overview + which are configured
python3 ludodex/config.py integrations ea     # exact steps for one
bash scripts/auth_status.sh                   # OK / BROKEN per source
```
