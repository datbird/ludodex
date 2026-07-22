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
- **Local config** — `python3 config.py set <key> <value>` (or enter it in the web
  UI: **Settings › Services**); stored in `config.sqlite` (gitignored).

Resolver helpers (used by the pull scripts and `auth_status.sh`):

| Helper | Resolves |
|---|---|
| `python3 config.py steam-key` | Steam Web API key |
| `python3 config.py itch-key` | itch.io API key |
| `config.igdb_creds()` | IGDB (Twitch) Client ID + Secret |
| `config.steamgriddb_key()` | SteamGridDB API key |

**Verify everything at once:** `bash auth_status.sh` → prints `OK` / `BROKEN` per
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
| **ScreenScraper** | metadata + media | software `devid`/`devpassword` + account `ssid`/`sspassword` | forum (devid) + screenscraper.fr (account) | `screenscraper_devid/devpassword/ssid/sspassword` | no (devid is approval-gated) |
| **SteamGridDB** | media | API key | steamgriddb.com/profile/preferences/api | `steamgriddb_api_key` | no |
| **Google image search** | media (last resort) | API key + Search engine ID | console.cloud.google.com + programmablesearchengine.google.com | `google_cse_key`, `google_cse_cx` | no (100 queries/day) |
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
   python3 config.py set steam_id <SteamID64>
   python3 config.py set steam_api_key <key>
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
   python3 config.py set itch_api_key <key>
   ```
   (Env override: `ITCH_API_KEY`.) Pull: `python3 itch_owned.py` → `itch_games.tsv`.
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
   python3 ea_owned.py --login --remid <value>     # validates + caches to .ea/cookies.json
   python3 config.py set ea_remid <value>           # or enter it in the web UI
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
   python3 ea_owned.py --token '{"access_token":"...","expires_in":14400}'
   ```
   Cached in `.ea/token.json` for **~4 hours**; pulls run headless until it expires.

- Pull (either path): `python3 ea_owned.py` → `ea_games.tsv`
- **Durability:** with a working `remid` (Path A) EA is effectively set-and-forget.
  Where Akamai blocks it (Path B), EA is a **re-grab-on-demand** source — re-do the
  URL→JSON step when you want to refresh ownership (EA libraries change rarely). Full
  server-side automation would need a real browser engine (Playwright), as Lutris uses.

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

### Playnite (desktop frontend)
No cloud auth — a small PowerShell bridge (`playnite_bridge.ps1`) runs **inside**
Playnite (Playnite stores its library in LiteDB, which needs .NET, so unlike
LaunchBox ludodex can't touch it directly). Both sides speak one canonical JSON.
Like LaunchBox it's a **meta-layer**, not a source (`in_playnite` provenance only).

```powershell
# in Playnite (Extensions > Execute script): export its library + art paths
.\playnite_bridge.ps1 -Export -Path playnite_games.json
```
```
python3 config.py set playnite_import_json <path to playnite_games.json>  # update.sh ingests it
python3 playnite_export.py        # catalog + chosen art -> ludodex_to_playnite.json + _media/ bundle
# copy the JSON AND its <name>_media/ folder to the Playnite machine, then:
#   .\playnite_bridge.ps1 -Import -Path ludodex_to_playnite.json
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
   python3 config.py set igdb_client_id <id>
   python3 config.py set igdb_client_secret <secret>
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
   higher daily request cap). Store it:
   ```
   python3 config.py set screenscraper_ssid <login>
   python3 config.py set screenscraper_sspassword <password>
   ```
2. **`devid`/`devpassword`** — a *software* credential the API **requires** (you
   cannot call it with only an account). Request one on the **dev forum**
   **https://www.screenscraper.fr/forumsujets.php?frub=12** describing your free,
   non-commercial use. ⚠️ Manual approval, **can take days–weeks — request early.**
   ```
   python3 config.py set screenscraper_devid <id>
   python3 config.py set screenscraper_devpassword <pw>
   ```
   (Env overrides: `SS_DEVID`, `SS_DEVPASSWORD`, `SS_SSID`, `SS_SSPASSWORD`.)

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
   python3 config.py set steamgriddb_api_key <key>
   python3 config.py enable steamgriddb
   ```
   (Env override: `STEAMGRIDDB_API_KEY`.)

### Google image search (optional, last resort)

The wand's "search the web" toggle uses this **only** for games that IGDB, ScreenScraper
and SteamGridDB all failed to supply art for. It needs two values from two different Google
consoles. Neither works without the other.

> **Read this first.** Google **deprecated "Search the entire web"** for Programmable Search
> Engines — the toggle exists but can no longer be enabled on a new engine. Your engine can
> therefore only search sites you explicitly list, which makes the site list (step 8) the
> difference between this feature working and returning nothing at all.

**Part A — the Search engine ID (`cx`)**

1. Go to **https://programmablesearchengine.google.com/controlpanel/all** and click **Add**.
2. Name it anything (`ludodex image search`).
3. **Sites to search** cannot be left empty — the **Create** button stays disabled. Add one
   throwaway entry (`en.wikipedia.org`) to get past the form; you replace it in step 8.
4. Turn **Image search** **On**. This is the one that matters.
5. Leave **SafeSearch** off — box art occasionally trips it.
6. Tick the reCAPTCHA and click **Create**.
7. Open the engine → **Customise** / **Overview → Basic** and copy the **Search engine ID**.
   That is your `cx` — a bare alphanumeric string (older accounts show `0123456789:abcdefghij`).

**Part B — the sites to search (this is the important step)**

8. In **Sites to search**, click **Add**, and paste the list below (one per line), then delete
   the `en.wikipedia.org` placeholder from step 3.

   Every domain here was live-probed and confirmed to return **raw image bytes** to a
   non-browser client — no Cloudflare challenge, no JS rendering, no referer check:

   ```
   *.archive.org
   art.gametdb.com
   segaretro.org
   retrocdn.net
   thumbnails.libretro.com
   coverproject.sfo2.cdn.digitaloceanspaces.com
   adb.arcadeitalia.net
   upload.wikimedia.org
   cdn.thegamesdb.net
   cdn.cloudflare.steamstatic.com
   ```

   - `*.archive.org` — the strongest single entry: multi-megabyte originals, referer-
     independent, CORS-open. Note it redirects across hosts; ludodex follows that.
   - `art.gametdb.com` — Nintendo only (Wii/GameCube/Wii U/3DS/Switch) but high-res with real
     JA/EN/DE regional variants. It is `art.`, not `www.`
   - `segaretro.org` / `retrocdn.net` — full-size scans up to ~1.2 MB. Their HTML pages sit
     behind a proof-of-work gate; the image paths do not.
   - `thumbnails.libretro.com` — the broadest platform coverage of the set, but hard-capped
     at **512 px wide**. Good as a fallback, never as a hero image.
   - `coverproject.sfo2.cdn.digitaloceanspaces.com` — the CDN behind The Cover Project. Use
     this, not `thecoverproject.net`, which is challenge-walled.

   **The more the merrier — with a caveat.** A wider list genuinely finds more art, and you
   have up to **50 domains**. But every extra domain is more index for Google to search and
   more candidate URLs for ludodex to fetch and validate, so each one costs time on every
   lookup. A domain that never returns a usable result is pure overhead — it slows every
   search and buys nothing. Add sites you have reason to believe hold art for the platforms
   *you* actually own, and prune anything that never produces a hit.

   **Do not add these** — each was tested and fails, so it would waste a slot and slow every
   query for nothing:

   | Domain | Why not |
   |---|---|
   | `gamefaqs.gamespot.com` | Cloudflare-challenged on **every** path incl. images. 100% failure; cannot be worked around with a UA or referer. |
   | `www.mobygames.com` | Returned the identical Cloudflare 403 signature. |
   | `amiga.abime.net` | Worst case: returns **HTTP 200 with an HTML challenge body**. Only a content-type check catches it. |
   | `progettosnaps.net` | Bulk zip/7z archives only — no per-game image URLs. Use `adb.arcadeitalia.net`, which republishes the same media per game. |
   | `thecoverproject.net` | Challenge-walled, and its assets are 300-DPI print wraps (front+spine+back, often rotated), not usable as covers. |

**Part C — the API key**

9. Enable the API: **https://console.cloud.google.com/apis/library/customsearch.googleapis.com**
   → check the project selector at the top → **Enable**. (If it already reads *Manage*, it is
   enabled.)
10. **https://console.cloud.google.com/apis/credentials** → **+ CREATE CREDENTIALS** →
    **API key**. Copy the `AIza…` string.
11. Recommended: **Edit API key** → *API restrictions* → **Restrict key** → tick **Custom
    Search API**, so the key cannot be used for anything else.

**Part D — verify before entering it**

12. Paste this in a browser, substituting both values:

    ```
    https://www.googleapis.com/customsearch/v1?key=YOUR_KEY&cx=YOUR_CX&q=chrono+trigger+box+art&searchType=image&num=1
    ```

    - JSON with an `items` array → working.
    - `API key not valid` → wrong key, or step 9 was skipped.
    - `Invalid Value` on cx → wrong ID, or Image search was never switched on.
    - `accessNotConfigured` → the API is enabled on a *different* project than the key.

13. Store it:
    ```
    python3 config.py set google_cse_key <key>
    python3 config.py set google_cse_cx  <search-engine-id>
    ```
    or paste both into **Settings → Connections → Stores & providers → Google image search**.

**Built-in tester.** Once the key and `cx` are saved, the Google image search card gains
*"Is a site worth adding?"* — enter a domain and a game you own and it runs both checks that
matter: it asks your engine for that site, then fetches every result and validates it the way
the wand will. Three verdicts:

- **Keep it** — results came back AND fetched as real images.
- **Drop it** — Google indexes the site but nothing fetched (challenge, hotlink guard, or a
  login wall). Costs you time on every lookup and returns nothing.
- **Nothing found** — either the domain isn't in your engine's site list yet (add it, then
  re-test), or Google has no image index for it. If the site also refuses a plain request,
  the tester says so, which tells you adding it would be pointless.

Use it before spending a slot, and re-test occasionally: sites put up bot protection over
time, and a domain that worked last year can silently become dead weight.

**Quota:** the free tier is **100 queries/day**, after which it errors rather than billing.
ludodex only reaches this provider for games still art-less after every other provider, and
only when the wand's web-search toggle is on.

---

## Remote sync (optional)

### PocketBase
Mirrors the catalog to a PocketBase instance. Auth = a dedicated **superuser**.
```
python3 config.py set pocketbase_url https://<host>:8090
python3 config.py set pocketbase_admin_email <email>
python3 config.py set pocketbase_admin_password <password>
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
