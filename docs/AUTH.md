# ludodex — Integrations & Credentials

How to obtain and wire up the auth for **every** ludodex integration: ownership
sources, metadata providers, media providers, and remote sync. This is the single
source of truth — the README links here.

No secrets live in this repo. Your real values go into `config.sqlite` (gitignored).
ludodex reads credentials **only** from environment variables or `config.sqlite` —
it never reaches out to 1Password (or any external secret store) at runtime.

---

## How ludodex resolves a credential

Every credential follows the same **precedence**:

```
environment variable   >   local config (config.sqlite)
```

- **Env var** — highest priority; handy for one-off runs / CI.
- **Local config** — `python3 ludodex/config.py set <key> <value>` (or enter it in the web
  UI: **Settings › Services**); stored in `config.sqlite` (gitignored).

Resolver helpers (used by the pull scripts and `scripts/auth_status.sh`):

| Helper | Resolves |
|---|---|
| `python3 ludodex/config.py steam-key` | Steam Web API key |
| `python3 ludodex/config.py itch-key` | itch.io API key |
| `config.igdb_creds()` | IGDB (Twitch) Client ID + Secret |
| `config.steamgriddb_key()` | SteamGridDB API key |

**Verify everything at once:** `bash scripts/auth_status.sh` → prints `OK` / `BROKEN` per
source.

---

## Quick reference

| Integration | Type | Credential | Get it from | Config key(s) | Expires? |
|---|---|---|---|---|---|
| **Steam** | source | Web API key + SteamID | steamcommunity.com/dev/apikey | `steam_api_key`, `steam_id` | no |
| **Epic** | source | OAuth (legendary) | legendary.gl/epiclogin | — (`~/.config/legendary`) | auto-refresh |
| **GOG** | source | Galaxy OAuth code | login URL in `gog_owned.py` | `gog_client_id/secret` (public) | auto-refresh |
| **itch.io** | source | personal API key | itch.io/user/settings/api-keys | `itch_api_key` | no |
| **EA** | source | remid cookie (preferred) / browser access token (fallback) | see EA section | `ea_remid` | remid: durable · token: ~4h |
| **emulation / archive** | source | none (local files / SSH) | — | mounts (see below) | — |
| **LaunchBox** | source (frontend) | none (local/networked files) | a LaunchBox install folder | `launchbox_path`, `launchbox_media_mode` | — |
| **Playnite** | source (frontend) | none (PowerShell bridge in-app) | your Playnite install | `playnite_import_json`, `playnite_media_overwrite`, `playnite_icon_source` | — |
| **IGDB** | metadata | Twitch app Client ID+Secret | dev.twitch.tv/console/apps | `igdb_client_id/secret` | token auto-mints |
| **ScreenScraper** | metadata + media | account `ssid`/`sspassword` (the software `devid`/`devpassword` ships embedded) | screenscraper.fr (account); dev forum only if you want your own devid | `screenscraper_devid/devpassword/ssid/sspassword` | no |
| **SteamGridDB** | media | API key | steamgriddb.com/profile/preferences/api | `steamgriddb_api_key` | no |
| **Open-web art discovery** | media (last resort) | none — uses your AI provider's grounded search | — | — | — |
| **ES-DE / Steam-grid / Steam CDN / IGDB images** | media | none / reuses IGDB | — | media mounts | — |
| **PocketBase** | sync | superuser email+password | your PocketBase admin | `pocketbase_admin_email/password`, `pocketbase_url` | no |
| **Firebase** | sync | service-account JSON | Firebase console | `firebase_project_id`, `firebase_sa_json` | no |

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
   python3 ludodex/config.py set steam_id <SteamID64>
   python3 ludodex/config.py set steam_api_key <key>
   ```
   (Env override: `STEAM_API_KEY`.)
- Pull: `python3 ludodex/steam_owned.py` → `steam_games.tsv`

### Epic
Uses the **`legendary`** CLI (installed locally; pipx).
```
legendary auth          # opens/echoes a URL
# visit https://legendary.gl/epiclogin, copy the authorizationCode, paste it
```
Token is cached in `~/.config/legendary` and auto-refreshes. Pull: `python3 ludodex/epic_owned.py`.

### GOG
GOG Galaxy **OAuth** using Galaxy's public client (defaults already in config).
```
python3 ludodex/gog_owned.py            # prints the login URL on first run
# log in, copy the `code=...` value from the redirected URL, then:
python3 ludodex/gog_owned.py --code <code>
```
A refresh token is cached in `.gog/tokens.json` and auto-refreshes thereafter.

### itch.io
A personal **API key**.
1. Create one at **https://itch.io/user/settings/api-keys**.
2. Store:
   ```
   python3 ludodex/config.py set itch_api_key <key>
   ```
   (Env override: `ITCH_API_KEY`.) Pull: `python3 ludodex/itch_owned.py` → `itch_games.tsv`.
   > The itch *login* (user/pass) is separate from the API key — you only need the key.

### EA (EA app / Origin)
EA is the fiddliest source because its auth sits behind **Akamai Bot Manager** (bot
cookies + TLS fingerprint). There are **two ways in**; ludodex tries the first and
falls back to the second.

**Path A — `remid` cookie (preferred; can be set-and-forget).** Capture the durable
`remid` "remember-me" cookie once, and ludodex exchanges it for short-lived access
tokens non-interactively (`prompt=none` grant), refreshing on its own.
1. Log in to your EA account in a normal browser.
2. Dev tools (F12) → Application/Storage → Cookies → `https://accounts.ea.com` → copy
   the **`remid`** value (the long durable one).
3. Store it (either):
   ```
   python3 ludodex/ea_owned.py --login --remid <value>     # validates + caches to .ea/cookies.json
   python3 ludodex/config.py set ea_remid <value>           # or enter it in the web UI
   ```
   (Env override: `EA_REMID`.)

> ⚠️ **The silent `remid`→token refresh is frequently blocked by Akamai from
> datacenter/VPS/server IPs** (it was blocked from this project's build VM). It tends
> to work from **residential IPs** (a normal home machine). If Path A fails with an
> auth error, use Path B.

**Path B — browser-minted access token (always works; ~4h).** Grab a token straight
from your logged-in browser:
1. Logged in at EA, visit this URL in that same browser — it returns raw **JSON**:
   ```
   https://accounts.ea.com/connect/auth?client_id=ORIGIN_JS_SDK&response_type=token&redirect_uri=nucleus:rest&prompt=none
   ```
2. Inject it (whole JSON or just the token value):
   ```
   python3 ludodex/ea_owned.py --token '{"access_token":"...","expires_in":14400}'
   ```
   Cached in `.ea/token.json` for **~4 hours**; pulls run headless until it expires.

- Pull (either path): `python3 ludodex/ea_owned.py` → `ea_games.tsv`
- **Durability:** with a working `remid` (Path A) EA is effectively set-and-forget.
  Where Akamai blocks it (Path B), EA is a **re-grab-on-demand** source — re-do the
  URL→JSON step when you want to refresh ownership (EA libraries change rarely). Full
  server-side automation would need a real browser engine (Playwright), as Lutris uses.

### emulation / local archives
No cloud auth. The emulation ROM index is built from `roms-index.sqlite`
(optionally rescanned over **SSH** to the ROM host — `unraid_host`/`roms_path`).
Local archives are registered as **crawl mounts**:
```
python3 ludodex/config.py mount add <path> [rom|flat] [name]
python3 ludodex/config.py mounts
```

### LaunchBox (desktop frontend)
No credentials — LaunchBox is plain files on disk, so ludodex reads and writes it
directly (no in-app bridge, unlike Playnite). It's a **meta-layer**, not a real
source: imported games map to their underlying provider and only flag
`in_launchbox` provenance. Point ludodex at the install root (the folder with
`Data/`, `Images/`, `Videos/`, `Manuals/`) — locally or via a mounted network/Unraid
share:
```
python3 ludodex/config.py set launchbox_path <LaunchBox root>
```
Then every `scripts/update.sh` imports it, and you push the consolidated catalog + chosen
art back with:
```
python3 ludodex/launchbox_export.py            # copies art into <root>/Images/...
python3 ludodex/launchbox_export.py --link     # symlink art -> media_repo/<sha1> instead
```
**Reference mode (`--link` / `launchbox_media_mode=link`)** keeps a single stored
copy of each asset (e.g. on Unraid) and points every frontend at it — use it when
the LaunchBox `Images/` folder is on the same filesystem as `media_repo`. Close
LaunchBox before exporting (it rewrites Platform XMLs on edit); ludodex upserts by a
stable per-game GUID and never touches games you added by hand.

### Playnite (desktop frontend)
No cloud auth — a small PowerShell bridge (`scripts/playnite_bridge.ps1`) runs **inside**
Playnite (Playnite stores its library in LiteDB, which needs .NET, so unlike
LaunchBox ludodex can't touch it directly). Both sides speak one canonical JSON.
Like LaunchBox it's a **meta-layer**, not a source (`in_playnite` provenance only).

```powershell
# in Playnite (Extensions > Execute script): export its library + art paths
.\scripts/playnite_bridge.ps1 -Export -Path playnite_games.json
```
```
python3 ludodex/config.py set playnite_import_json <path to playnite_games.json>  # scripts/update.sh ingests it
python3 ludodex/playnite_export.py        # catalog + chosen art -> ludodex_to_playnite.json + _media/ bundle
# copy the JSON AND its <name>_media/ folder to the Playnite machine, then:
#   .\scripts/playnite_bridge.ps1 -Import -Path ludodex_to_playnite.json
```
Media flows both ways (cover/background/icon). Two knobs:
- `playnite_media_overwrite` = `gaps` (fill empty slots) · `all` (replace) ·
  `playnite-wins` (never clobber + your Playnite art becomes the canonical pick
  everywhere, propagating to LaunchBox and the server).
- `playnite_icon_source` = `logo` · `cover` · `none`.

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
   > ⚠️ The Client Secret is shown **once**. If you lose it, generate a new one — the
   > Client ID stays the same.
3. Store + enable:
   ```
   python3 ludodex/config.py set igdb_client_id <id>
   python3 ludodex/config.py set igdb_client_secret <secret>
   python3 ludodex/config.py enable igdb
   ```
   (Env overrides: `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET`.)
- Enrich: `python3 ludodex/igdb_enrich.py` (incremental; `--all` re-does). Caches in
  `metadata-cache.sqlite`; `build_library.py` merges **fill-gaps only**.

### ScreenScraper (screenscraper.fr) — emulation metadata **and** media
The emulation community's canonical database (what ES-DE/Skraper/RetroArch scrape).
**One scrape per game returns both metadata** (genres, developer, publisher,
players, rating, description) **and every media URL** (box, wheel/logo, fanart
background, screenshots, title, marquee, video, manual). It fills the retro gaps
IGDB misses and the backgrounds ES-DE lacks.

**Auth = a software credential + your account.** The software credential
**ships with ludodex**, so there is nothing for you to request: `_ssauth.py`
carries a `devid`/`devpassword` that identifies *the app*, and every deployment
authenticates as that one recognisable client. You only supply your own account.

1. **Account** — make a free account at screenscraper.fr; your login is `ssid`.
   Past contribution / a Patreon pledge raises your **tier** (more threads + a
   higher daily request cap). Store it:
   ```
   python3 ludodex/config.py set screenscraper_ssid <login>
   python3 ludodex/config.py set screenscraper_sspassword <password>
   ```
2. **`devid`/`devpassword`** — the *software* credential the API **requires**
   (you cannot call it with only an account). **Already embedded; skip this step
   unless you want your own identity.** It is obfuscated in the repo rather than
   secret — a credential that ships inside a client can never be hidden, because
   the key travels with it — and that is a deliberate trade for having
   ScreenScraper see one app instead of a swarm of anonymous callers.

   To use your own instead, request one on the **dev forum**
   **https://www.screenscraper.fr/forumsujets.php?frub=12** describing your free,
   non-commercial use. ⚠️ Manual approval, **days–weeks — request early.**
   ```
   python3 ludodex/config.py set screenscraper_devid <id>
   python3 ludodex/config.py set screenscraper_devpassword <pw>
   ```
   (Env overrides: `SS_DEVID`, `SS_DEVPASSWORD`, `SS_SSID`, `SS_SSPASSWORD`.
   Resolution is env > config > embedded.)

   Note there is **no rotation path** for the embedded credential, and none is
   implied: a replacement is a fresh manual forum request that would reach only
   installs which pull a new build. What bounds the risk instead is that the
   devid selects the *software*, while tier and daily quota come from **your**
   `ssid`/`sspassword` — so misuse of the shared identity spends the shared
   software's allowance, never your account's.

**Tiers & quota (the engine adapts to whatever you have):**

| Tier | Threads | Daily requests |
|---|---|---|
| Free / registered | ~1 | ~20,000 |
| ~5 €/mo (Patreon) | +1 | higher |
| ~10 €/mo (Patreon) | +5 | ~50,000 |

The engine reads your **live `ssuser` quota** from every response, paces under the
per-minute limit, runs at your thread count, and **stops before the daily cap**,
resuming the next day. Check it any time: `python3 ludodex/ss_scrape.py --status`. Scrape:
`python3 ludodex/ss_scrape.py` (resumable; `--limit N` to cap a run). Metadata merges
fill-gaps (after IGDB); media URLs are indexed as the `screenscraper` provider and
downloaded (with auth) only when a chosen asset is materialized.

---

## Media providers

Media is indexed by reference into `media-index.sqlite` (keyed by `norm_key`).
`config.py sources` lists media providers + their state; `config.py media-mounts`
lists registered local paths.

| Provider | Auth | Setup |
|---|---|---|
| **ES-DE** (RetroDECK / EmuDeck) | none | `python3 ludodex/config.py media-mount add "<downloaded_media path>" esde [name]` |
| **Steam grid** (local custom art) | none | autodetected (`~/.steam/.../userdata/<id>/config/grid`); or `config.py set steam_grid_path <dir>` |
| **Steam CDN** | none | works for any owned Steam appid |
| **IGDB images** | reuses IGDB (Twitch) creds above | runs once IGDB is configured |
| **SteamGridDB** | **API key** | see below |

- ES-DE roots: RetroDECK → `<retrodeck>/ES-DE/downloaded_media`; EmuDeck →
  `<Emulation>/tools/downloaded_media`. Register each as an `esde` media mount.
- Index local: `python3 ludodex/media_index.py`. Fetch remote: `python3 ludodex/media_fetch.py`
  (`--steamgriddb` for the gap-fill pass).

### SteamGridDB
1. Get a free API key at **https://www.steamgriddb.com/profile/preferences/api**.
2. Store + enable:
   ```
   python3 ludodex/config.py set steamgriddb_api_key <key>
   python3 ludodex/config.py enable steamgriddb
   ```
   (Env override: `STEAMGRIDDB_API_KEY`.)

### Open-web art discovery — no key of its own

The wand's "search the web" toggle needs **no separate credential**. It runs on the AI
provider you already configured, in three legs:

1. **Wikimedia** — keyless. The game's Wikipedia lead image, which is usually the cover.
2. **Grounded web search** — the provider's own search tool (Gemini's `google_search`,
   Anthropic's `web_search`) returns the *pages* a game's art lives on. ludodex fetches each
   page, extracts its declared image (`og:image`, then a real `<img>`), validates that it is
   a live image, and the vision model picks the best of what actually fetched.
3. **Model-proposed direct URLs** — a low-yield last resort.

Leg 2 replaced the Google **Custom Search JSON API**, which Google
[closed to new customers](https://developers.google.com/custom-search/v1/overview) and
discontinues on 2027-01-01. A new project returns `403 This project does not have the access
to Custom Search JSON API` no matter how correctly it is configured — verified 2026-07-22
against a fully correct setup. The `google_cse_key` / `google_cse_cx` settings and their UI
were removed; grounded search reaches the same index through a credential you already have.

**Validation is content-type based, after redirects** — that is what makes leg 2 safe. The
protections in the wild fail differently: Cloudflare answers 403, Anubis answers **HTTP 200
with an HTML challenge body**, hotlink guards 302 to a homepage. Only checking the final
Content-Type catches all three.

**Junk filtering.** Some real, correctly-served images are never the game's art — bot-
challenge mascots (Anubis serves one as its page's `og:image`), database placeholders, site
logos, region flags and YouTube thumbnails all validate perfectly and are all wrong. They are
rejected by URL pattern before reaching the vision picker; see `media_web._is_junk_image`.

---

## Backing store (optional)

Credentials for the external database that holds your durable data. What it does and how
it reconciles is **[SYNC.md](SYNC.md)**; this section is only about the secrets.

### PocketBase
Auth = a dedicated **superuser**.
```
python3 ludodex/config.py set pocketbase_url https://<host>:8090
python3 ludodex/config.py set pocketbase_admin_email <email>
python3 ludodex/config.py set pocketbase_admin_password <password>   # env POCKETBASE_PASSWORD overrides
python3 ludodex/config.py set backingstore_backend pocketbase
```
Create the superuser on the server:
`docker exec <container> /pb/pocketbase superuser upsert <email> <password>`.
Then: `python3 ludodex/dbsync.py pocketbase` (add `--dry-run` to see it first).

### Postgres / Supabase / MySQL
```
python3 ludodex/config.py set backingstore_backend postgres     # or supabase | mysql
python3 ludodex/config.py set postgres_url postgresql://user:pass@host:5432/ludodex
# ...or the discrete fields: postgres_host/_port/_db/_user/_password (mysql_* for MySQL)
```
The password fields are stored in `config.sqlite`, which is gitignored.

### Firebase Firestore
```
python3 ludodex/config.py set firebase_project_id <project>
python3 ludodex/config.py set firebase_sa_json /path/to/service-account.json
python3 ludodex/config.py set backingstore_backend firebase
pip install -r requirements-firebase.txt        # google-auth; the Docker image has it already
```
Get the service-account JSON from the Firebase console → Project settings →
Service accounts → Generate new private key. (Keep the JSON out of git — `*.json`
SA files are gitignored.)

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

`./scripts/setup.sh` runs a guided wizard that initializes config and walks through obtaining
each credential, authenticates the stores, optionally indexes ROMs, and builds the
catalog. It's re-runnable and safe (Enter keeps the current value).
