# ludodex — Integrations & Credentials

How to obtain and wire up the auth for **every** ludodex integration: ownership
sources, metadata providers, media providers, and remote sync. This is the single
source of truth — the README links here.

No secrets live in this repo. Your real values go into `config.sqlite` (gitignored)
or 1Password; only code, skills, and docs are tracked.

---

## How ludodex resolves a credential

Every credential follows the same **precedence**, so you can store it whichever way
you prefer:

```
environment variable   >   local config (config.sqlite)   >   1Password
```

- **Env var** — highest priority; handy for one-off runs / CI.
- **Local config** — `python3 config.py set <key> <value>`; stored in
  `config.sqlite` (gitignored).
- **1Password** — leave the local value blank and point a `*_op_item` key at a
  1Password item. Requires the `op` CLI signed in (this repo's environment uses an
  `opx` wrapper that loads a service-account token from `~/.config/op/token`). The
  vault is `op_vault` (default `<vault>`).

Resolver helpers (used by the pull scripts and `auth_status.sh`):

| Helper | Resolves |
|---|---|
| `python3 config.py steam-key` | Steam Web API key |
| `python3 config.py itch-key` | itch.io API key |
| `config.igdb_creds()` | IGDB (Twitch) Client ID + Secret |
| `config.steamgriddb_key()` | SteamGridDB API key |

**Verify everything at once:** `bash auth_status.sh` → prints `OK` / `BROKEN` per
source. The `games-auth` skill wraps this with re-auth walkthroughs.

---

## Quick reference

| Integration | Type | Credential | Get it from | Config key(s) | 1Password item key | Expires? |
|---|---|---|---|---|---|---|
| **Steam** | source | Web API key + SteamID | steamcommunity.com/dev/apikey | `steam_api_key`, `steam_id` | `steam_key_op_item` (field `apikey`) | no |
| **Epic** | source | OAuth (legendary) | legendary.gl/epiclogin | — (`~/.config/legendary`) | — | auto-refresh |
| **GOG** | source | Galaxy OAuth code | login URL in `gog_owned.py` | `gog_client_id/secret` (public) | — (`.gog/tokens.json`) | auto-refresh |
| **itch.io** | source | personal API key | itch.io/user/settings/api-keys | `itch_api_key` | `itch_key_op_item` (field `apikey`) | no |
| **EA** | source | browser access token | URL below (returns JSON) | `ea_op_item` | `ea_op_item` (field `credential`) | ~4h (re-grab) |
| **emulation / archive** | source | none (local files / SSH) | — | mounts (see below) | — | — |
| **LaunchBox** | source (frontend) | none (local/networked files) | a LaunchBox install folder | `launchbox_path`, `launchbox_media_mode` | — | — |
| **IGDB** | metadata | Twitch app Client ID+Secret | dev.twitch.tv/console/apps | `igdb_client_id/secret` | `igdb_op_item` (`username`=ID, `credential`=secret) | token auto-mints |
| **ScreenScraper** | metadata + media | software `devid`/`devpassword` + account `ssid`/`sspassword` | forum (devid) + screenscraper.fr (account) | `screenscraper_devid/devpassword/ssid/sspassword` | `screenscraper_op_item` (`username`=ssid, `password`=sspassword) | no (devid is approval-gated) |
| **SteamGridDB** | media | API key | steamgriddb.com/profile/preferences/api | `steamgriddb_api_key` | `steamgriddb_op_item` (field `credential`) | no |
| **ES-DE / Steam-grid / Steam CDN / IGDB images** | media | none / reuses IGDB | — | media mounts | — | — |
| **PocketBase** | sync | superuser email+password | your PocketBase admin | `pocketbase_admin_email/password`, `pocketbase_url` | `pocketbase_op_item` (`username`/`password`) | no |
| **Firebase** | sync | service-account JSON | Firebase console | `firebase_project_id`, `firebase_sa_json` | — | no |

---

## Ownership sources

### Steam
A Steam **Web API key** is a public-API access token (not a personal login), so with
the **correct SteamID** it reads your owned games with **no login, no 2FA, no public
profile**.

1. Sign in at **https://steamcommunity.com/dev/apikey**, register a key (any domain).
2. Find the SteamID of the account that **owns the key** — use the `steamid` in
   `~/.steam/steam/config/loginusers.vdf` (the SteamID64).
   > ⚠️ Do **not** resolve via a vanity URL (`/id/<name>`) — that can be a different
   > account. The API key only reads its **own** owner's data.
3. Store:
   ```
   python3 config.py set steam_id <SteamID64>
   python3 config.py set steam_api_key <key>      # or use 1Password instead:
   python3 config.py set steam_key_op_item "<1Password item>"   # field: apikey
   ```
   (Env override: `STEAM_API_KEY`.)
- Pull: `python3 steam_owned.py` → `steam_games.tsv`

### Epic
Uses the **`legendary`** CLI (installed locally; pipx).
```
legendary auth          # opens/echoes a URL
# visit https://legendary.gl/epiclogin, copy the authorizationCode, paste it
```
Token is cached in `~/.config/legendary` and auto-refreshes. Pull: `python3 epic_owned.py`.

### GOG
GOG Galaxy **OAuth** using Galaxy's public client (defaults already in config).
```
python3 gog_owned.py            # prints the login URL on first run
# log in, copy the `code=...` value from the redirected URL, then:
python3 gog_owned.py --code <code>
```
A refresh token is cached in `.gog/tokens.json` and auto-refreshes thereafter.

### itch.io
A personal **API key**.
1. Create one at **https://itch.io/user/settings/api-keys**.
2. Store:
   ```
   python3 config.py set itch_api_key <key>          # or 1Password:
   python3 config.py set itch_key_op_item "<1Password item>"   # field: apikey
   ```
   (Env override: `ITCH_API_KEY`.) Pull: `python3 itch_owned.py` → `itch_games.tsv`.
   > The itch *login* (user/pass) is separate from the API key — you only need the key.

### EA (EA app / Origin)
EA's auth sits behind **Akamai Bot Manager** (bot cookies + TLS fingerprint), so a
headless cookie→token refresh is rejected. The reliable path is a **browser-minted
access token**:

1. Log in to your EA account in a normal browser.
2. Visit this URL in that browser — it returns raw **JSON**:
   ```
   https://accounts.ea.com/connect/auth?client_id=ORIGIN_JS_SDK&response_type=token&redirect_uri=nucleus:rest&prompt=none
   ```
3. Inject the token (whole JSON or just the value):
   ```
   python3 ea_owned.py --token '{"access_token":"...","expires_in":14400}'
   ```
   It's cached in `.ea/token.json` for ~4 hours; pulls run headless until it expires.
- Pull: `python3 ea_owned.py` → `ea_games.tsv`
- **Durability:** the token is short-lived and EA blocks headless refresh, so EA is a
  **re-grab-on-demand** source (fine — EA libraries rarely change). True automation
  would need a real browser engine (Playwright) like Lutris uses.
- The `remid`/`sid` cookies (`config.py ... ea_op_item`, `.ea/cookies.json`) are
  stored for reference but **cannot** mint tokens headlessly.

### emulation / local archives
No cloud auth. The emulation ROM index is built from `roms-index.sqlite`
(optionally rescanned over **SSH** to the ROM host — `unraid_host`/`roms_path`).
Local archives are registered as **crawl mounts**:
```
python3 config.py mount add <path> [rom|flat] [name]
python3 config.py mounts
```

### LaunchBox (desktop frontend)
No credentials — LaunchBox is plain files on disk, so ludodex reads and writes it
directly (no in-app bridge, unlike Playnite). It's a **meta-layer**, not a real
source: imported games map to their underlying provider and only flag
`in_launchbox` provenance. Point ludodex at the install root (the folder with
`Data/`, `Images/`, `Videos/`, `Manuals/`) — locally or via a mounted network/Unraid
share:
```
python3 config.py set launchbox_path <LaunchBox root>
```
Then every `update.sh` imports it, and you push the consolidated catalog + chosen
art back with:
```
python3 launchbox_export.py            # copies art into <root>/Images/...
python3 launchbox_export.py --link     # symlink art -> media_repo/<sha1> instead
```
**Reference mode (`--link` / `launchbox_media_mode=link`)** keeps a single stored
copy of each asset (e.g. on Unraid) and points every frontend at it — use it when
the LaunchBox `Images/` folder is on the same filesystem as `media_repo`. Close
LaunchBox before exporting (it rewrites Platform XMLs on edit); ludodex upserts by a
stable per-game GUID and never touches games you added by hand.

---

## Metadata providers

### IGDB (igdb.com) — enrich attributes, not a source
IGDB auth = a free **Twitch application** (Client ID + Secret; OAuth
client-credentials, the access token auto-mints/refreshes).

1. Go to **https://dev.twitch.tv/console/apps** → *Register Your Application*:
   - Name: anything (e.g. `ludodex-igdb`)
   - OAuth Redirect URL: `http://localhost`
   - Category: *Application Integration*
2. Copy the **Client ID** and generate a **Client Secret**.
3. Store either locally or in 1Password, then enable:
   ```
   python3 config.py set igdb_client_id <id>
   python3 config.py set igdb_client_secret <secret>     # or 1Password:
   python3 config.py set igdb_op_item "<item>"   # username=Client ID, credential=Secret
   python3 config.py enable igdb
   ```
   (Env overrides: `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET`.)
- Enrich: `python3 igdb_enrich.py` (incremental; `--all` re-does). Caches in
  `metadata-cache.sqlite`; `build_library.py` merges **fill-gaps only**.

### ScreenScraper (screenscraper.fr) — emulation metadata **and** media
The emulation community's canonical database (what ES-DE/Skraper/RetroArch scrape).
**One scrape per game returns both metadata** (genres, developer, publisher,
players, rating, description) **and every media URL** (box, wheel/logo, fanart
background, screenshots, title, marquee, video, manual). It fills the retro gaps
IGDB misses and the backgrounds ES-DE lacks.

**Auth = a software credential + your account** (both required):
1. **Account** — make a free account at screenscraper.fr; your login is `ssid`.
   Past contribution / a Patreon pledge raises your **tier** (more threads + a
   higher daily request cap). Store it (often in its own vault):
   ```
   python3 config.py set screenscraper_op_item "<1Password item>"   # username=ssid, password=sspassword
   python3 config.py set screenscraper_op_vault "<vault>"           # if not op_vault
   # …or locally: config.py set screenscraper_ssid / screenscraper_sspassword
   ```
2. **`devid`/`devpassword`** — a *software* credential the API **requires** (you
   cannot call it with only an account). Request one on the **dev forum**
   **https://www.screenscraper.fr/forumsujets.php?frub=12** describing your free,
   non-commercial use. ⚠️ Manual approval, **can take days–weeks — request early.**
   ```
   python3 config.py set screenscraper_devid <id>
   python3 config.py set screenscraper_devpassword <pw>     # or screenscraper_dev_op_item
   ```

**Tiers & quota (the engine adapts to whatever you have):**

| Tier | Threads | Daily requests |
|---|---|---|
| Free / registered | ~1 | ~20,000 |
| ~5 €/mo (Patreon) | +1 | higher |
| ~10 €/mo (Patreon) | +5 | ~50,000 |

The engine reads your **live `ssuser` quota** from every response, paces under the
per-minute limit, runs at your thread count, and **stops before the daily cap**,
resuming the next day. Check it any time: `python3 ss_scrape.py --status`. Scrape:
`python3 ss_scrape.py` (resumable; `--limit N` to cap a run). Metadata merges
fill-gaps (after IGDB); media URLs are indexed as the `screenscraper` provider and
downloaded (with auth) only when a chosen asset is materialized.

---

## Media providers

Media is indexed by reference into `media-index.sqlite` (keyed by `norm_key`).
`config.py sources` lists media providers + their state; `config.py media-mounts`
lists registered local paths.

| Provider | Auth | Setup |
|---|---|---|
| **ES-DE** (RetroDECK / EmuDeck) | none | `python3 config.py media-mount add "<downloaded_media path>" esde [name]` |
| **Steam grid** (local custom art) | none | autodetected (`~/.steam/.../userdata/<id>/config/grid`); or `config.py set steam_grid_path <dir>` |
| **Steam CDN** | none | works for any owned Steam appid |
| **IGDB images** | reuses IGDB (Twitch) creds above | runs once IGDB is configured |
| **SteamGridDB** | **API key** | see below |

- ES-DE roots: RetroDECK → `<retrodeck>/ES-DE/downloaded_media`; EmuDeck →
  `<Emulation>/tools/downloaded_media`. Register each as an `esde` media mount.
- Index local: `python3 media_index.py`. Fetch remote: `python3 media_fetch.py`
  (`--steamgriddb` for the gap-fill pass).

### SteamGridDB
1. Get a free API key at **https://www.steamgriddb.com/profile/preferences/api**.
2. Store + enable:
   ```
   python3 config.py set steamgriddb_api_key <key>          # or 1Password:
   python3 config.py set steamgriddb_op_item "<item>"   # field: credential
   python3 config.py enable steamgriddb
   ```
   (Env override: `STEAMGRIDDB_API_KEY`.)

---

## Remote sync (optional)

### PocketBase
Mirrors the catalog to a PocketBase instance. Auth = a dedicated **superuser**.
```
python3 config.py set pocketbase_url https://<host>:8090
python3 config.py set pocketbase_admin_email <email>
python3 config.py set pocketbase_admin_password <password>   # or 1Password:
python3 config.py set pocketbase_op_item "<item>"   # fields: username / password
python3 config.py set sync_target pocketbase        # auto-sync after each rebuild
```
Create the superuser on the server:
`docker exec <container> /pb/pocketbase superuser upsert <email> <password>`.
Sync: `python3 sync.py` (delta) / `python3 sync.py --reconcile` (self-heal).

### Firebase Firestore
```
python3 config.py set firebase_project_id <project>
python3 config.py set firebase_sa_json /path/to/service-account.json
python3 config.py set sync_target firebase      # or 'both'
pip install -r requirements-firebase.txt        # google-auth, etc.
```
Get the service-account JSON from the Firebase console → Project settings →
Service accounts → Generate new private key. (Keep the JSON out of git — `*.json`
SA files are gitignored.)

---

## 1Password (optional, for any `*_op_item`)

Any credential above can be kept in 1Password instead of `config.sqlite`. Point the
relevant `*_op_item` key at an item in `op_vault`, and leave the local value blank.
The resolver reads the field named in the table above (`apikey`, `credential`,
`username`/`password`, …). The `op` CLI must be authenticated (service-account token
or desktop-app integration).

---

## Commercial use

Each integration carries a **commercial posture** (shown by `config.py
integrations`): whether its data is cleared for a paid/hosted product *without a
separate license*. Your own ownership data and infrastructure are fine; **IGDB**
has commercial terms (verify + attribute); **ScreenScraper** is free/non-commercial
unless you obtain their prior authorization (a fresh request to their team), and
**community/publisher art** (SteamGridDB, and ES-DE art which derives from
ScreenScraper) is IP-gated. Setting `commercial_safe_only=1` is intended to run
only the cleared providers — keep a commercial build standing on IGDB + members'
own ownership/library data + user-owned media, with the rest as license-gated
enrichers.

## Onboarding a fresh machine

`./setup.sh` runs a guided wizard that initializes config and walks through obtaining
each credential, authenticates the stores, optionally indexes ROMs, and builds the
catalog. It's re-runnable and safe (Enter keeps the current value).
