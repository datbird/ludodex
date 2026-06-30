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
| **IGDB** | metadata | Twitch app Client ID+Secret | dev.twitch.tv/console/apps | `igdb_client_id/secret` | `igdb_op_item` (`username`=ID, `credential`=secret) | token auto-mints |
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

## Onboarding a fresh machine

`./setup.sh` runs a guided wizard that initializes config and walks through obtaining
each credential, authenticates the stores, optionally indexes ROMs, and builds the
catalog. It's re-runnable and safe (Enter keeps the current value).
