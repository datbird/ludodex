#!/usr/bin/env python3
"""ludodex configuration — account/environment settings stored in SQLite.

All personal & environment-specific values (your SteamID, API keys, ROM
host/paths, etc.) live in a `config` table inside config.sqlite, which is
gitignored — they are NOT hardcoded in any committed script. Other scripts read
them with:  `import config; config.get("steam_id")`. Secrets resolve env var >
config value only; ludodex never reads 1Password (or any external store) at runtime.

Only SAFE/PUBLIC defaults ship in this file's SCHEMA. Fill your real values
locally (they go into config.sqlite, never into git):

  python3 ludodex/config.py setup          # interactively fill every key (Enter = keep)
  python3 ludodex/config.py list           # show all keys, values, descriptions
  python3 ludodex/config.py get <key>      # print one value (used by the shell scripts)
  python3 ludodex/config.py set <key> <value>
  python3 ludodex/config.py steam-key      # resolve the Steam key (env > config)
  python3 ludodex/config.py init           # just create/seed config.sqlite

  python3 ludodex/config.py integrations                  # how to get every credential
  python3 ludodex/config.py integrations <id>             # step-by-step for one (e.g. ea, igdb)
  python3 ludodex/config.py sources                       # list sources + metadata + state
  python3 ludodex/config.py enable|disable <name>         # toggle steam/epic/gog/itch/ea/
                                                  #   emulation/playnite, an archive,
                                                  #   or a metadata provider (igdb)
  python3 ludodex/config.py mount add <path> [rom|flat] [name]     # add a crawl mount/path
  python3 ludodex/config.py mounts                         # list crawl paths + mount status
  python3 ludodex/config.py mount rm <name>
  python3 ludodex/config.py archive add <name> <path> [rom|flat]   # (same registry, name-first)

For first-time onboarding with credential how-to guidance, run ./scripts/setup.sh instead.
"""
import json
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
DB = os.path.join(DATA, "config.sqlite")

# (key, default, description). Defaults are SAFE/PUBLIC only. Personal values are
# filled locally via `setup`/`set` and stored in the gitignored config.sqlite.
SCHEMA = [
    ("steam_id", "",
     "Your SteamID64 — the account that owns the Steam Web API key. Find it in "
     "~/.steam/steam/config/loginusers.vdf (17-digit key). NOT the vanity URL, "
     "which can be a different account."),
    ("steam_api_key", "",
     "Steam Web API key stored locally (config.sqlite is gitignored). The "
     "STEAM_API_KEY env var overrides it."),
    ("itch_api_key", "",
     "itch.io API key stored locally (gitignored). Get one at "
     "https://itch.io/user/settings/api-keys. ITCH_API_KEY env overrides it."),
    # BLANK, not an absolute path. Seeding these with os.path.join(DATA, ...) baked the
    # FIRST machine's data dir into config.sqlite forever: a config created on a checkout
    # and later mounted at /data kept pointing at the checkout, so the container wrote its
    # catalog somewhere nothing reads. Blank means "wherever the data dir is now", which
    # is what the docs have always said. See _resolve_data_path.
    ("library_db", "",
     "Output path for the unified deduped catalog. Blank = game-library.sqlite in the "
     "data dir, which is what you want unless you deliberately put it elsewhere."),
    ("roms_index_db", "",
     "Path to the ROM index DB (emulation input, built by build_romdb.py). Blank = "
     "roms-index.sqlite in the data dir."),
    ("unraid_host", "",
     "ssh target where the ROM archive lives, e.g. root@192.168.1.10 "
     "(only needed for `scripts/update.sh --roms`)."),
    ("roms_path", "",
     "Absolute path to the ROM archive on that host (only needed for --roms)."),
    # --- preferences: behavior toggles (1 = on, 0 = off) ---
    ("steam_include_free", "1",
     "[pref] Count played free-to-play games (TF2, Dota, …) as owned in the Steam "
     "pull. Set 0 for strict ownership only."),
    ("dedupe_preserve_years", "1",
     "[pref] Keep a bare 4-digit year in the dedupe key, so distinct releases that "
     "share a base title (a remake vs the original (2005)) stay separate."),
    ("dedupe_strip_editions", "1",
     "[pref] Strip edition/remaster words (Remastered, Definitive Edition, GOTY, …) "
     "when deduping, so a remaster merges with its base game."),
    # --- source toggles (1 = include, 0 = skip) — see `config.py sources` ---
    ("source_steam_enabled", "1", "[source] Pull Steam ownership."),
    ("source_epic_enabled", "1", "[source] Pull Epic ownership."),
    ("source_gog_enabled", "1", "[source] Pull GOG ownership."),
    ("source_itch_enabled", "1", "[source] Pull itch.io ownership."),
    ("source_ea_enabled", "1", "[source] Pull EA app/Origin ownership."),
    ("ea_remid", "",
     "EA 'remid' cookie (durable login) stored locally (gitignored). Grab it from "
     "your browser after logging in to EA; it mints short-lived tokens "
     "non-interactively. The EA_REMID env var overrides it."),
    ("source_emulation_enabled", "1", "[source] Include the emulation ROM index."),
    ("source_playnite_enabled", "1", "[source] Include an imported Playnite library."),
    ("playnite_import_json", "", "Path to the Playnite export JSON produced by "
     "scripts/playnite_bridge.ps1 (-Export); ingested by build_library."),
    ("playnite_export_json", "", "Where playnite_export.py writes the ludodex->"
     "Playnite JSON (blank = ludodex_to_playnite.json next to the scripts). A "
     "portable art bundle is written beside it as <name>_media/ — copy BOTH to the "
     "Playnite machine before running the bridge -Import."),
    ("playnite_media_overwrite", "gaps", "How playnite_export.py treats art already "
     "set in Playnite: 'gaps' (only fill empty cover/background/icon slots), 'all' "
     "(always replace with ludodex's chosen art), or 'playnite-wins' (never clobber "
     "AND make your Playnite art the chosen pick everywhere — it propagates to the "
     "other frontends and the server)."),
    ("playnite_icon_source", "logo", "What playnite_export.py uses for the Playnite "
     "icon (Playnite has no separate logo slot): 'logo' (clear-logo art), 'cover' "
     "(reuse the boxart), or 'none' (leave icons alone)."),
    ("source_launchbox_enabled", "1", "[source] Include an imported LaunchBox "
     "library (a frontend meta-layer, like Playnite — provenance only)."),
    ("launchbox_path", "", "Root of a LaunchBox install (the folder holding Data/, "
     "Images/, Videos/, Manuals/). Used by launchbox_import.py / launchbox_export.py. "
     "On Windows e.g. C:/LaunchBox; via a network/Unraid share, the mounted path."),
    ("launchbox_import_json", "", "Where launchbox_import.py writes the LaunchBox-> "
     "ludodex JSON (blank = ludodex_from_launchbox.json next to the scripts); "
     "ingested by build_library."),
    ("launchbox_media_mode", "copy", "How launchbox_export.py places chosen art: "
     "'copy' (independent copies) or 'link' (symlink Images/.. -> media_repo/<sha1>, "
     "one stored copy backs every frontend — use when Images shares a filesystem "
     "with media_repo, e.g. both on Unraid)."),
    # --- metadata providers: CONSULTED to enrich attributes, NOT sources.
    #     They never add ownership; they only fill in missing attributes. ---
    ("metadata_igdb_enabled", "1",
     "[metadata] Consult IGDB (igdb.com) to fill in MISSING game attributes "
     "(genres, themes, game modes, developers, publishers, release dates, "
     "ratings). Fill-gaps only — never overwrites store/Playnite data. No-ops "
     "without Twitch credentials below."),
    ("igdb_client_id", "",
     "Twitch application Client ID for the IGDB API. Create a free app at "
     "https://dev.twitch.tv/console/apps (any name; OAuth Redirect URL "
     "http://localhost; Category 'Application Integration'). The IGDB_CLIENT_ID "
     "env var overrides this."),
    ("igdb_client_secret", "",
     "Twitch application Client Secret, stored locally (gitignored). "
     "IGDB_CLIENT_SECRET env overrides it."),
    ("igdb_meta_ttl_days", "30",
     "[metadata] Days before a cached IGDB record is considered stale and "
     "re-fetched by igdb_enrich.py."),
    # --- ScreenScraper (screenscraper.fr) — emulation metadata AND media, in one
    #     scrape per game. Needs a software devid/devpassword (request at
    #     screenscraper.fr/forumsujets.php?frub=12) PLUS the end-user's account. ---
    ("metadata_screenscraper_enabled", "1",
     "[metadata] Consult ScreenScraper for emulation game metadata + media "
     "(genres/dev/publisher/players/rating + box/wheel/fanart/screenshots). "
     "No-ops without a devid. Drives both the metadata merge and SS media refs."),
    ("screenscraper_softname", "ludodex",
     "Your software name sent to the ScreenScraper API (they log by softname)."),
    ("screenscraper_devid", "",
     "ScreenScraper developer id (software credential). Request one at "
     "https://www.screenscraper.fr/forumsujets.php?frub=12 — free/non-commercial "
     "software qualifies. Env: SS_DEVID."),
    ("screenscraper_devpassword", "",
     "ScreenScraper developer password (pairs with the devid). Env: SS_DEVPASSWORD."),
    ("screenscraper_ssid", "",
     "Your ScreenScraper account login (sets your tier/quota). Env: SS_SSID."),
    ("screenscraper_sspassword", "",
     "Your ScreenScraper account password. Env: SS_SSPASSWORD."),
    # The catalog walk adapts to whatever tier your account has — ScreenScraper reports
    # its own threads/day/minute limits and those win. These only NARROW that: useful if
    # you want the walk to leave more room for interactive scraping, or to be gentler
    # than your tier technically allows. Blank means "use what the account grants".
    ("screenscraper_walk_threads", "",
     "Concurrent requests for the catalog walk. Blank = whatever your ScreenScraper "
     "tier grants (free is typically 1, contributors get more). A value higher than "
     "your tier is ignored, never applied."),
    ("screenscraper_walk_reserve", "",
     "Requests held back each day for your own scraping while a multi-day catalog walk "
     "runs. Blank = 5% of your daily quota (minimum 200), so it scales with your tier "
     "instead of assuming a large one."),
    ("screenscraper_media", "1",
     "[media] Ingest the media URLs returned by each ScreenScraper scrape into "
     "the media index (no extra API calls — they come free with the metadata)."),
    ("screenscraper_daily_margin", "200",
     "Stop scraping when within this many requests of the daily cap "
     "(maxrequestsperday), leaving headroom. Free tier cap is ~20,000/day."),
    # --- TheGamesDB (api.thegamesdb.net) — the OTHER scraper this audience knows
    #     (ES-DE ships exactly two: ScreenScraper and this one). Bring-your-own-key.
    #     Budgeted PER MONTH, not per day, which is why the numbers below look tiny
    #     next to ScreenScraper's — a free key is 1,000 requests for the whole month. ---
    ("metadata_thegamesdb_enabled", "0",
     "[metadata] Consult TheGamesDB for game metadata + boxart. Off by default: it "
     "needs your own API key, and its monthly allowance is small enough that turning "
     "it on should be a choice rather than something that happens to you."),
    ("thegamesdb_api_key", "",
     "Your TheGamesDB API key. Request one free at "
     "https://api.thegamesdb.net/key.php (approval averages about an hour). "
     "Env: TGDB_API_KEY."),
    ("thegamesdb_monthly_limit", "1000",
     "Requests ludodex will spend per month. 1000 is what a free key grants. Raise it "
     "ONLY if you have bought a higher tier — the $29/year Developer tier grants "
     "12,000 a month, which is the one worth naming. The live allowance reported by "
     "the API always wins, so a value above your real key is ignored, never applied."),
    ("thegamesdb_reserve", "",
     "Requests held back from bulk sweeps so interactive lookups still work. Blank = "
     "5% of the monthly limit (minimum 10), so it scales if you buy a bigger tier."),
    # --- MobyGames (api.mobygames.com) — the cheapest id space in the stack. Its limit
    #     is 720 requests an HOUR, not a monthly budget, so the only cost of a request
    #     is time: 332,414 games at 100 per page is 3,325 pages, about 4.5 hours. ---
    ("metadata_mobygames_enabled", "0",
     "[metadata] Consult MobyGames for metadata + sample art. Off by default: it needs "
     "your own paid key ($9.99/mo Hobbyist, non-commercial only)."),
    ("mobygames_api_key", "",
     "Your MobyGames API key, from your MobyPro API page. Env: MOBYGAMES_API_KEY."),
    ("mobygames_hourly_limit", "720",
     "Requests per hour. 720 is the documented non-commercial ceiling ('one every five "
     "seconds'); legacy keys get 360, a commercial agreement may get more. Pacing is "
     "derived from this — lower it to be gentler, never raise it past what you have."),
    ("mobygames_min_interval_ms", "1000",
     "Never send two requests closer together than this, whatever the hourly limit "
     "works out to. 1000ms is their own 429 advice ('wait at least 1 seconds')."),
    ("mobygames_burst_reserve", "",
     "Requests held out of the burst allowance so a long walk cannot spend the whole "
     "hour in twelve minutes and leave an interactive lookup waiting forty-eight. "
     "Blank = 10% of the hourly limit (minimum 20). Set 0 to allow full-speed bursting."),
    ("mobygames_walk_format", "normal",
     "What the catalogue walk asks for: 'id', 'brief' or 'normal'. All three page at "
     "100 and cost ONE request, so 'normal' is free — it brings genres, platforms, "
     "release dates, alternate titles, score and sample art for the same 3,325 pages."),
    ("mobygames_store_payload", "0",
     "Keep each game's RAW record in the local catalogue instead of just the identity "
     "fields. Off by default: descriptions and art urls are most of the bytes and can be "
     "re-fetched per game on demand, so the mirror stays ~20-30 MB rather than several "
     "hundred. Turn it on if you would rather spend disk than a second 4.6-hour walk."),
    ("mobygames_media", "1",
     "[media] Ingest the sample cover and screenshots that ride along with each game "
     "record (no extra requests; they arrive with width/height already)."),
    ("mobygames_product_codes", "0",
     "Fetch DISC SERIALS / product codes. OFF, and think before turning it on: these "
     "live at /games/{id}/platforms/{pid}, ONE REQUEST PER GAME PER PLATFORM with no "
     "batching — roughly 430,000 requests, about 25 DAYS at 720/hour. Scope it to the "
     "platforms you own rather than running it across the catalogue."),
    ("mobygames_full_covers", "0",
     "[media] Fetch the COMPLETE cover set per game/platform (front, back, media, "
     "spine, per country) instead of the single sample cover. Same cost shape as "
     "product codes — one request per game per platform. Off by default."),
    # --- free, no-key specialists. Each covers a platform family the big providers
    #     serve badly: arcade (Steam and IGDB know nothing about a cabinet) and the
    #     ZX Spectrum (13,859 hashed titles here, zero covered by TheGamesDB's map). ---
    ("metadata_arcadedb_enabled", "1",
     "[metadata] Consult ArcadeDB (adb.arcadeitalia.net) for MAME sets — year, "
     "manufacturer, genre, players, controls, screen orientation, plus marquee, "
     "cabinet, flyer and short-play video. No key, no quota; keyed on the MAME set "
     "name, so there is no name matching to get wrong."),
    ("metadata_zxinfo_enabled", "1",
     "[metadata] Consult ZXInfo (api.zxinfo.dk) for ZX Spectrum titles — machine "
     "variant, authors with roles, publisher, loading screens. No key, no quota."),
    ("arcadedb_media", "1",
     "[media] Ingest ArcadeDB's marquee, cabinet, control-panel, flyer and short-play "
     "video URLs alongside its metadata (they come in the same response)."),
    ("zxinfo_media", "1",
     "[media] Ingest ZXInfo's loading and in-game screens alongside its metadata."),
    ("libretro_dats_collections", "redump,no-intro",
     "Which DAT collections to fold into the match index, in order. 'redump' is the "
     "one carrying disc serials and is a quarter the size, so it leads. Others in the "
     "repo (tosec, hacks, homebrew) are available but noisier."),
    ("libretro_dats_cache_days", "30",
     "How long a downloaded DAT is trusted before it is re-fetched. They change when a "
     "dump is added or corrected — real, but not weekly."),
    ("matchindex_libretro_dats", "1",
     "Fold the No-Intro and Redump dump databases (via libretro-database, CC BY-SA) "
     "into the match index when it is rebuilt. This is where DISC SERIALS come from — "
     "the only identifier that survives conversion to CHD or RVZ, which every hash "
     "does not. ~174,000 canonical dumps, free, cached 30 days."),
    ("matchindex_wikidata_ids", "1",
     "Fold Wikidata's cross-database ids into the match index. FREE and CC0: 33,956 "
     "MobyGames pointers, 60,376 Redump, 1,460 TheGamesDB, joined to the IGDB slug the "
     "mirror already carries. A cross-reference is a POINTER, not content — it is the "
     "coordinate you use to go and ask a provider, and it carries none of their data."),
    ("wikidata_ids_namespaces", "mobygames,thegamesdb,redump",
     "Which id namespaces to pull. Steam and GOG are available but left out by default: "
     "IGDB already publishes 666,417 store ids of its own and those are authoritative, "
     "so re-importing them from a third party adds rows without adding knowledge."),
    ("wikidata_ids_cache_days", "30",
     "How long the pulled id set is trusted before it is re-queried. Wikidata edits "
     "continuously, but cross-database identifiers are stable once added."),
    ("matchindex_tgdb_freemap", "1",
     "Fold the free SHA1->TheGamesDB-id map (sselph/scraper hash.csv, MIT) into the "
     "match index when it is rebuilt. 32,045 hashes covering 10,688 games — ids that "
     "would otherwise cost one API request each against a 1,000/MONTH budget. "
     "Downloaded to your machine and cached for 30 days; a failed download is skipped, "
     "never fatal."),
    ("matchindex_tgdb_freemap_url", "",
     "Where to fetch that map from. Blank = sselph/scraper's hash.csv on GitHub. Point "
     "it at a local copy or your own mirror if you prefer."),
    ("thegamesdb_media", "1",
     "[media] Ingest the boxart TheGamesDB returns alongside metadata (no extra "
     "requests — it rides on the same call)."),
    # --- media providers: image/video assets indexed by REFERENCE, keyed by
    #     norm_key. Local providers read registered media mounts; remote ones
    #     fetch by id. Indexed by media_index.py into media-index.sqlite. ---
    ("media_esde_enabled", "1",
     "[media] Index ES-DE downloaded_media sets (shared by RetroDECK AND "
     "EmuDeck) registered via 'config.py media-mount add <path> esde'. Covers "
     "covers/marquees/screenshots/titlescreens/physicalmedia/miximages/videos/"
     "manuals, matched to emulation games by system + ROM filename."),
    ("media_steamgrid_enabled", "1",
     "[media] Index local Steam custom artwork (userdata/<id>/config/grid), "
     "keyed by appid -> cover/background/logo/icon. Autodetected, or set "
     "steam_grid_path."),
    ("media_playnite_enabled", "1",
     "[media] Index cover/background/icon from an imported Playnite library."),
    ("media_steam_enabled", "1",
     "[media] Resolve Steam store CDN art (capsule/hero/logo) by appid. Remote, "
     "no auth."),
    ("media_igdb_enabled", "1",
     "[media] Resolve IGDB cover/artwork/screenshots by IGDB id. Remote; reuses "
     "the Twitch creds already configured for metadata."),
    ("media_steamgriddb_enabled", "0",
     "[media] Resolve SteamGridDB community art (grids/heroes/logos/icons). "
     "Remote; needs an API key (steamgriddb_api_key)."),
    ("ai_art_auto_pick", "0",
     "[media] If 1, the wand AI-vision-picks the nicest art per kind automatically the "
     "FIRST time it touches a game (once per game, not every rebuild). Default 0 — costs "
     "money per game, so it's off; deterministic ranking still chooses art. Use the "
     "per-game 'AI: pick nicest art' button to run it on demand regardless of this flag."),
    ("steam_grid_path", "",
     "Steam userdata grid folder for the steamgrid media provider. Blank = "
     "autodetect ~/.steam/steam/userdata/<id>/config/grid."),
    ("steamgriddb_api_key", "",
     "SteamGridDB API key (https://www.steamgriddb.com/profile/preferences/api)."),
    ("media_repo", "",
     "Local content-addressed repo where CHOSEN media is materialized for "
     "export/sync/serving. Blank = <scripts-dir>/media."),
    ("commercial_safe_only", "0",
     "If 1, run only providers cleared for commercial use without a separate "
     "license (see 'config.py integrations'). Excludes ScreenScraper and "
     "community/publisher art until you have authorization. Default 0."),
    # --- RETIRED. sync.py, the one-way "publish the catalog" mirror, is gone: nothing
    #     ever read what it published and its name kept being confused with the two-way
    #     backing store below, which is the one that protects your data. The key stays so
    #     an existing config.sqlite does not look corrupt; nothing reads it. ---
    ("sync_target", "",
     "RETIRED — has no effect. The one-way catalog mirror it controlled was removed; "
     "use backingstore_backend (the two-way backing store) instead."),
    ("pocketbase_url", "",
     "PocketBase base URL, e.g. https://pb.example.com (no trailing slash)."),
    ("pocketbase_admin_email", "",
     "PocketBase admin/superuser email used for the sync."),
    ("pocketbase_admin_password", "",
     "PocketBase admin password, stored locally (gitignored). POCKETBASE_PASSWORD "
     "env wins."),
    # --- two-way BACKING STORE (dbsync.py): SQLite is the local cache, this backend the
    #     durable truth. Pick one; fill its config. ---
    ("backingstore_backend", "",
     "Two-way backing store backend: blank (off) | 'pocketbase' | 'postgres' | "
     "'supabase' | 'mysql' | 'firebase'. SQLite stays the fast local cache; this holds "
     "the durable data."),
    ("backingstore_auto_minutes", "0",
     "Auto-sync the backing store every N minutes (0 = off / manual only). Runs the "
     "two-way sync in the background; skipped while one is already running."),
    ("postgres_url", "",
     "Postgres connection string (overrides the discrete fields), e.g. "
     "postgresql://user:pass@host:5432/ludodex."),
    ("postgres_host", "", "Postgres host (if not using postgres_url)."),
    ("postgres_port", "5432", "Postgres port."),
    ("postgres_db", "ludodex", "Postgres database name."),
    ("postgres_user", "ludodex", "Postgres user."),
    ("postgres_password", "", "Postgres password (gitignored)."),
    ("supabase_url", "",
     "Supabase Postgres connection string (from Project Settings → Database → "
     "Connection string), e.g. postgresql://postgres:pass@db.<ref>.supabase.co:5432/postgres."),
    ("mysql_host", "", "MySQL/MariaDB host."),
    ("mysql_port", "3306", "MySQL port."),
    ("mysql_db", "ludodex", "MySQL database name."),
    ("mysql_user", "ludodex", "MySQL user."),
    ("mysql_password", "", "MySQL password (gitignored)."),
    ("firebase_project_id", "",
     "Firebase/GCP project id for the Firestore sync."),
    ("firebase_sa_json", "",
     "Path to a Firebase service-account JSON key (gitignored). Needs the "
     "google-auth package; used to mint a Firestore access token."),
    ("firebase_database", "(default)",
     "Firestore database id. Most projects use the default; set this only if you "
     "created a named (non-default) Firestore database."),
    ("firebase_collection_prefix", "",
     "Optional prefix for the Firestore collection names (e.g. 'ludodex_')."),
    # --- media storage policy ---
    ("media_mode", "chosen",
     "How much media to download into the local repo: 'ondemand' (keep refs, "
     "fetch-on-serve), 'chosen' (download the shown asset per game/kind on sync), "
     "or 'all' (every candidate — a full archive)."),
    ("screenshot_limit", "0",
     "Max screenshots to keep per game (0 = no limit). Applies to every screenshot "
     "provider so a game's set is consistent regardless of source."),
]
DEFAULTS = {k: d for k, d, _ in SCHEMA}
DESCS = {k: c for k, _, c in SCHEMA}


def _con():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS config "
                "(key TEXT PRIMARY KEY, value TEXT, description TEXT)")
    return con


def init():
    """Create config.sqlite and seed any missing keys with their defaults."""
    con = _con()
    for k, d, c in SCHEMA:
        con.execute("INSERT OR IGNORE INTO config(key,value,description) "
                    "VALUES(?,?,?)", (k, d, c))
        con.execute("UPDATE config SET description=? WHERE key=?", (c, k))
    con.commit()
    con.close()


# Keys whose value is a path that BELONGS IN THE DATA DIR, with the filename it takes
# there. Earlier versions seeded these with an absolute path at first init, so the answer
# was pinned to whatever the data dir happened to be on the machine that ran `init` —
# and moving the install (a checkout later mounted as /data, the docker image, a restored
# backup) left the stored value pointing at a directory that no longer exists.
_DATA_PATH_KEYS = {"library_db": "game-library.sqlite",
                   "roms_index_db": "roms-index.sqlite"}


def _resolve_data_path(key, val):
    """Honour a DELIBERATE custom path; heal a STALE one.

    A stored value is kept when it is inside the current data dir, when the file is
    actually there, or when its parent directory exists (a custom location that has not
    been written to yet). Anything else is a path from another machine, and the answer is
    the data dir — because a catalog written to a directory that does not exist here is
    not a preference, it is a bug that presents as an empty library."""
    default = os.path.join(DATA, _DATA_PATH_KEYS[key])
    if not val:
        return default
    p = os.path.abspath(val)
    if p.startswith(os.path.abspath(DATA) + os.sep) or os.path.exists(p) \
            or os.path.isdir(os.path.dirname(p)):
        return val
    return default


def get(key, default=None):
    """Return a config value, falling back to the SCHEMA default (or `default`).

    Treats unset/empty as 'not set' so safe defaults apply."""
    val = None
    if os.path.exists(DB):
        con = _con()
        row = con.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        con.close()
        if row is not None:
            val = row[0]
    if key in _DATA_PATH_KEYS:
        return _resolve_data_path(key, val)
    if val:
        return val
    if default is not None:
        return default
    return DEFAULTS.get(key, "")


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def get_bool(key, default=False):
    """Interpret a config value as a boolean (1/true/yes/on vs 0/false/no/off)."""
    v = (get(key) or "").strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return default


def set_(key, value):
    con = _con()
    con.execute("INSERT INTO config(key,value,description) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value, DESCS.get(key, "")))
    con.commit()
    con.close()


# Credentials resolve env var > local config value only. ludodex never reads
# 1Password (or any external secret store) at runtime — populate config.sqlite.
# --------------------------------------------------------------------------- #
#  Shared helpers for the store scripts. They live here rather than in a module of
#  their own because config.py is the one thing every store script already imports,
#  and both of these are rules that must hold for the NEXT store as well as the nine
#  that exist.
# --------------------------------------------------------------------------- #
def write_private_json(path, obj):
    """Write a cached credential: owner-only, and atomically.

    A cached token IS a password. PSN's refresh token lasts about two months, GOG's
    rotates but is equally live, and Xbox's mints Xbox Live tokens on demand — yet five
    of these scripts wrote them with a bare `json.dump(tok, open(path, "w"))`, which
    takes whatever the umask allows: group-readable on a shell, 0644 under Docker, where
    every other container on the same /data can read it.

    ATOMIC for a second reason: `open(path, "w")` truncates before it writes, so a crash
    or a full disk in between leaves an EMPTY token file and the next run reports "not
    connected" for a session that was perfectly good. Write beside it, chmod, rename.

    The temp file is created 0600 from the start — chmod'ing after the write would leave
    the credential world-readable for the length of the write."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)          # the directory listing alone says which stores are linked
    except OSError:
        pass
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
    except BaseException:
        try:
            os.unlink(tmp)          # never leave a half-written credential lying around
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    return path


# A field separator cannot also be data. Every store script printed "%s\t%s" with no
# escaping, and build_library's loader splits on \t and strips only \n — so a title
# containing a tab lost its tail, and a stray \r rode into the catalog title, where it
# joins the norm_key and forks one game into two entries. Collapsing the three
# characters that mean something structural to a space keeps the row readable, loses
# nothing a person would recognise in a game's name, and needs no loader change.
_TSV_BREAKS = str.maketrans({"\t": " ", "\r": " ", "\n": " ", "\x00": " "})


def tsv_field(value):
    """One TSV cell: no tabs, no line breaks, no NULs, no leading/trailing space."""
    return str("" if value is None else value).translate(_TSV_BREAKS).strip()


def tsv_row(*fields):
    """A whole TSV line (without the newline) from already-sanitised cells."""
    return "\t".join(tsv_field(f) for f in fields)


def steam_key():
    return os.environ.get("STEAM_API_KEY", "").strip() or get("steam_api_key")


def itch_key():
    return os.environ.get("ITCH_API_KEY", "").strip() or get("itch_api_key")


def pocketbase_password():
    return os.environ.get("POCKETBASE_PASSWORD", "").strip() or \
        get("pocketbase_admin_password")


def igdb_creds():
    """Twitch (IGDB) Client ID + Secret -> (client_id, client_secret).
    env (IGDB_CLIENT_ID / IGDB_CLIENT_SECRET) > local config."""
    cid = os.environ.get("IGDB_CLIENT_ID", "").strip() or get("igdb_client_id")
    csec = os.environ.get("IGDB_CLIENT_SECRET", "").strip() or get("igdb_client_secret")
    return cid, csec


# --------------------------------------------------------------------------- #
#  sources: built-in store/emulation toggles + a registry of crawl archives
# --------------------------------------------------------------------------- #
BUILTIN_SOURCES = ("steam", "epic", "gog", "itch", "ea", "emulation", "playnite",
                   "launchbox")

# Metadata providers are CONSULTED to enrich attributes — they are NOT sources
# (they add no ownership). Toggled like sources but tracked separately.
METADATA_PROVIDERS = ("igdb", "screenscraper", "steamspy", "thegamesdb",
                      "arcadedb", "zxinfo", "mobygames")


def metadata_enabled(name):
    """True if a metadata provider (e.g. igdb) is enabled."""
    return get_bool("metadata_%s_enabled" % name, True)


def gog_creds():
    """GOG Galaxy public OAuth client (client_id, client_secret) — see _gogauth.py.
    Shared by every install (GOG has no per-app registration), so it ships embedded
    rather than living in the user-config DB. Resolves env > config override >
    embedded default; override only if GOG ever rotates the client."""
    try:
        import _gogauth
        cid_def, sec_def = _gogauth.client()
    except Exception:
        cid_def, sec_def = "", ""
    cid = os.environ.get("GOG_CLIENT_ID", "").strip() or get("gog_client_id") or cid_def
    sec = os.environ.get("GOG_CLIENT_SECRET", "").strip() or get("gog_client_secret") or sec_def
    return cid, sec


def _ss_dev_embedded():
    """The app's built-in ScreenScraper devid/devpassword (see _ssauth.py). Ships
    with ludodex so any deployment authenticates as this one app. Obfuscated, not
    secret — overridable via env/config below."""
    try:
        import _ssauth
        return _ssauth.dev_credentials()
    except Exception:
        return ("", "")


def screenscraper_creds():
    """ScreenScraper API params -> dict, or {} if no devid is available.

    devid/devpassword identify the SOFTWARE (ludodex) and are shipped embedded so
    every deployment authenticates as this app; they resolve env > config >
    embedded. ssid/sspassword identify the END USER's account (optional; they
    raise the request tier) and resolve env > config only."""
    _edev, _epw = _ss_dev_embedded()
    devid = (os.environ.get("SS_DEVID", "").strip()
             or get("screenscraper_devid") or _edev)
    devpw = (os.environ.get("SS_DEVPASSWORD", "").strip()
             or get("screenscraper_devpassword") or _epw)
    ssid = os.environ.get("SS_SSID", "").strip() or get("screenscraper_ssid")
    sspw = os.environ.get("SS_SSPASSWORD", "").strip() or get("screenscraper_sspassword")
    if not (devid and devpw):
        return {}                       # API unusable without the software devid
    creds = {"devid": devid, "devpassword": devpw,
             "softname": get("screenscraper_softname") or "ludodex"}
    if ssid and sspw:
        creds["ssid"], creds["sspassword"] = ssid, sspw
    return creds


# Media providers: local mount-based (esde, steamgrid, playnite, launchbox) + remote
# (steam, igdb, steamgriddb, thegamesdb, arcadedb, zxinfo, mobygames).
#
# TAKEN FROM media.py, not copied from it. The comment here used to CLAIM it was "kept in
# sync with media.MEDIA_PROVIDERS" while listing four fewer providers, so every one of
# them was invisible to `config.py enable <provider>` and to the per-provider scope
# settings. A list that has to be kept in sync by hand is a list that drifts; importing
# it means it cannot. media imports config, so this is deferred to avoid the cycle —
# and the literal below is only the fallback for a CLI running without media's deps.
def _media_providers():
    try:
        import media
        return tuple(media.MEDIA_PROVIDERS)
    except Exception:                            # noqa: BLE001
        return ("esde", "steamgrid", "playnite", "launchbox", "steam", "igdb",
                "steamgriddb", "thegamesdb", "arcadedb", "zxinfo", "mobygames")


MEDIA_PROVIDERS = _media_providers()


def media_enabled(name):
    """True if a media provider is enabled (steamgriddb defaults off)."""
    return get_bool("media_%s_enabled" % name, name != "steamgriddb")


# Per-provider SCOPE. A provider is on for every source and every platform by default;
# these hold only the EXCLUSIONS, so "default on" needs no migration and a newly-imported
# platform or store is automatically included rather than silently skipped.
#
# Why it exists: ScreenScraper costs ~10s per game it has and ~2min per game it does not
# (its search is per-name, and it has no `pc` system id, so PC titles get the slow
# cross-system path). On a 2000-game PC library that is hours of wall clock for a provider
# that mostly covers consoles. Being able to say "ScreenScraper: consoles only" turns that
# into minutes without giving up the coverage it is actually good at.
def provider_off_sources(name):
    """Ownership sources this provider is switched OFF for (e.g. {'steam'})."""
    raw = get("provider_%s_off_sources" % name) or ""
    return {x.strip() for x in raw.split(",") if x.strip()}


def provider_off_platforms(name):
    """Platforms this provider is switched OFF for (e.g. {'pc'})."""
    raw = get("provider_%s_off_platforms" % name) or ""
    return {x.strip() for x in raw.split(",") if x.strip()}


def set_provider_scope(name, off_sources=None, off_platforms=None):
    if off_sources is not None:
        set_("provider_%s_off_sources" % name,
             ",".join(sorted({str(x).strip() for x in off_sources if str(x).strip()})))
    if off_platforms is not None:
        set_("provider_%s_off_platforms" % name,
             ",".join(sorted({str(x).strip() for x in off_platforms if str(x).strip()})))


def provider_allowed(name, platform=None, sources=()):
    """Should `name` be consulted for a game on `platform` owned via `sources`?

    Master switch first, then the exclusions. A game is skipped when its platform is
    switched off, or when EVERY source it came from is switched off — "every", because a
    game owned on both Steam and as a ROM is still a ROM, and turning Steam off should not
    remove it from a provider that covers the ROM side.
    """
    # Its OWN switch, deliberately not the media_/metadata_ enable flag. Those govern
    # whether art or metadata is TAKEN from a provider; this governs whether the provider
    # is asked what the game IS. A match is not an ingest — and reusing the media flag
    # would have silently stopped SteamGridDB matching entirely, since
    # media_steamgriddb_enabled defaults to OFF.
    if not get_bool("provider_%s_enabled" % name, True):
        return False
    if platform and platform in provider_off_platforms(name):
        return False
    srcs = {s for s in (sources or []) if s}
    if srcs and srcs <= provider_off_sources(name):
        return False
    return True


def rate_limits(service):
    """Resolved rate-limit settings for a service (Settings › Services › Limits).
    Returns {cooldown_ms, per_min, per_day}; 0 means unset / unlimited."""
    def n(field):
        try:
            return int(get("%s_limit_%s" % (service, field)) or 0)
        except (TypeError, ValueError):
            return 0
    return {"cooldown_ms": n("cooldown_ms"), "per_min": n("per_min"),
            "per_day": n("per_day")}


def steamgriddb_key():
    """SteamGridDB API key: env > local config."""
    return os.environ.get("STEAMGRIDDB_API_KEY", "").strip() or get("steamgriddb_api_key")


def _media_con():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS media_mounts("
                "name TEXT PRIMARY KEY, path TEXT, provider TEXT, "
                "enabled INTEGER DEFAULT 1, kinds TEXT DEFAULT '')")
    # migrate older tables that predate the per-mount kinds filter
    cols = {r[1] for r in con.execute("PRAGMA table_info(media_mounts)")}
    if "kinds" not in cols:
        con.execute("ALTER TABLE media_mounts ADD COLUMN kinds TEXT DEFAULT ''")
    return con


def media_mounts_list(only_enabled=False, provider=None):
    con = _media_con()
    q = "SELECT name,path,provider,enabled,kinds FROM media_mounts"
    cond = []
    if only_enabled:
        cond.append("enabled=1")
    if provider:
        cond.append("provider=%r" % provider)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    rows = [{"name": n, "path": p, "provider": pr, "enabled": e,
             # kinds: which canonical media kinds to index; empty list = all
             "kinds": [k for k in (kd or "").split(",") if k]}
            for n, p, pr, e, kd in con.execute(q + " ORDER BY name")]
    con.close()
    return rows


def media_mount_set(name, path, provider="esde", enabled=1, kinds=None):
    con = _media_con()
    kstr = ",".join(kinds) if kinds else ""
    con.execute("INSERT INTO media_mounts(name,path,provider,enabled,kinds) "
                "VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "path=excluded.path, provider=excluded.provider, "
                "enabled=excluded.enabled, kinds=excluded.kinds",
                (name, path, provider, int(enabled), kstr))
    con.commit()
    con.close()


def media_mount_rm(name):
    con = _media_con()
    con.execute("DELETE FROM media_mounts WHERE name=?", (name,))
    con.commit()
    con.close()


def media_mount_set_enabled(name, enabled):
    con = _media_con()
    cur = con.execute("UPDATE media_mounts SET enabled=? WHERE name=?",
                      (1 if enabled else 0, name))
    con.commit()
    n = cur.rowcount
    con.close()
    return n > 0


def _arch_con():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS archives("
                "name TEXT PRIMARY KEY, path TEXT, kind TEXT, "
                "enabled INTEGER DEFAULT 1)")
    return con


def archives_list(only_enabled=False):
    con = _arch_con()
    q = ("SELECT name,path,kind,enabled FROM archives" +
         (" WHERE enabled=1" if only_enabled else "") + " ORDER BY name")
    rows = [{"name": n, "path": p, "kind": k, "enabled": e}
            for n, p, k, e in con.execute(q)]
    con.close()
    return rows


def archive_set(name, path, kind="rom", enabled=1):
    con = _arch_con()
    con.execute("INSERT INTO archives(name,path,kind,enabled) VALUES(?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET path=excluded.path, "
                "kind=excluded.kind, enabled=excluded.enabled",
                (name, path, kind, int(enabled)))
    con.commit()
    con.close()


def archive_rm(name):
    con = _arch_con()
    con.execute("DELETE FROM archives WHERE name=?", (name,))
    con.commit()
    con.close()


def archive_set_enabled(name, enabled):
    con = _arch_con()
    cur = con.execute("UPDATE archives SET enabled=? WHERE name=?",
                      (1 if enabled else 0, name))
    con.commit()
    n = cur.rowcount
    con.close()
    return n


def source_enabled(name):
    """True if a source (built-in store/emulation, or an archive) is enabled."""
    if name in BUILTIN_SOURCES:
        return get_bool("source_%s_enabled" % name, True)
    for a in archives_list():
        if a["name"] == name:
            return bool(a["enabled"])
    return True


def path_status(path):
    """How a crawl path/mount currently looks on disk."""
    if not path:
        return "unset"
    if os.path.isdir(path):
        return "mounted" if os.path.ismount(path) else "present"
    return "MISSING"


# --------------------------------------------------------------------------- #
#  integrations registry — the single structured source of "how do I get the
#  credential" for every integration. Consumed by `config.py integrations`, the
#  setup wizard, and (later) the web UI. Keep in sync with AUTH.md.
# --------------------------------------------------------------------------- #
INTEGRATIONS = [
    {"id": "steam", "name": "Steam", "kind": "source",
     "purpose": "Owned Steam games via the Web API (no login/2FA needed).",
     "url": "https://steamcommunity.com/dev/apikey",
     "steps": [
         "Sign in at https://steamcommunity.com/dev/apikey and register a key "
         "(any domain name works).",
         "Find the SteamID64 of the account that OWNS the key — it's the "
         "'steamid' in ~/.steam/steam/config/loginusers.vdf. Do NOT use a vanity "
         "URL (/id/<name>) — that can be a different account.",
         "Store: config.py set steam_id <id>  and  config.py set steam_api_key "
         "<key>  (or enter both in the web UI: Settings › Services)."],
     "config_keys": ["steam_id", "steam_api_key"],
     "env": ["STEAM_API_KEY"],
     "verify": "python3 ludodex/steam_owned.py | head"},

    {"id": "epic", "name": "Epic Games", "kind": "source",
     "purpose": "Owned Epic games via the legendary CLI (token auto-refreshes).",
     "url": "https://legendary.gl/epiclogin",
     "steps": [
         "Run: legendary auth",
         "Open https://legendary.gl/epiclogin, log in, and copy the "
         "'authorizationCode' value.",
         "Paste it when legendary prompts. Token is cached in ~/.config/legendary."],
     "config_keys": [], "env": [],
     "verify": "legendary status"},

    {"id": "gog", "name": "GOG", "kind": "source",
     "purpose": "Owned GOG games via Galaxy OAuth (refresh token auto-renews).",
     "url": "(login URL is printed by gog_owned.py)",
     "steps": [
         "Run: python3 ludodex/gog_owned.py   (prints a GOG login URL on first run).",
         "Log in, then copy the 'code=...' value from the redirected URL.",
         "Run: python3 ludodex/gog_owned.py --code <code>   (caches .gog/tokens.json)."],
     "config_keys": [], "env": [],   # GOG's shared public client ships embedded (_gogauth.py)
     "verify": "python3 ludodex/gog_owned.py | head"},

    {"id": "itch", "name": "itch.io", "kind": "source",
     "purpose": "Owned itch.io games via a personal API key.",
     "url": "https://itch.io/user/settings/api-keys",
     "steps": [
         "Generate a key at https://itch.io/user/settings/api-keys.",
         "Store: config.py set itch_api_key <key>  (or enter it in the web UI). "
         "The itch *login* is separate — you only need the API key."],
     "config_keys": ["itch_api_key"], "env": ["ITCH_API_KEY"],
     "verify": "python3 ludodex/itch_owned.py | head"},

    {"id": "ea", "name": "EA app / Origin", "kind": "source",
     "purpose": "Owned EA games (EA-exclusive titles). EA's Akamai shield blocks "
                "headless refresh, so use a browser-minted token (lasts ~4h).",
     "url": "https://accounts.ea.com/connect/auth?client_id=ORIGIN_JS_SDK"
            "&response_type=token&redirect_uri=nucleus:rest&prompt=none",
     "steps": [
         "Log in to your EA account in a normal browser.",
         "Visit this URL in that browser (it returns raw JSON): "
         "https://accounts.ea.com/connect/auth?client_id=ORIGIN_JS_SDK"
         "&response_type=token&redirect_uri=nucleus:rest&prompt=none",
         "Inject the token: python3 ludodex/ea_owned.py --token '<the JSON>'  (cached in "
         ".ea/token.json). Or store the durable 'remid' cookie for headless "
         "refresh: config.py set ea_remid <cookie> (or enter it in the web UI)."],
     "config_keys": ["ea_remid"], "env": ["EA_REMID"],
     "verify": "python3 ludodex/ea_owned.py --whoami"},

    {"id": "launchbox", "name": "LaunchBox (frontend)", "kind": "source",
     "purpose": "Two-way sync with a LaunchBox install: import its library + art "
                "as a meta-layer (provenance only), and export ludodex's "
                "consolidated metadata + chosen art back into it. No credentials — "
                "it's plain files on disk; just point ludodex at the folder.",
     "url": "(no signup — a local/networked LaunchBox install)",
     "steps": [
         "Find the LaunchBox root (the folder containing Data/, Images/, Videos/, "
         "Manuals/) — on Windows e.g. C:/LaunchBox, or its path via a network/Unraid "
         "share mounted on this machine.",
         "Set it: config.py set launchbox_path <root>   (then it imports on every "
         "update, and `python3 ludodex/launchbox_export.py` pushes back).",
         "To share ONE media copy with Unraid instead of duplicating: store media on "
         "the same filesystem and run launchbox_export.py --link (or set "
         "launchbox_media_mode=link). Close LaunchBox before exporting."],
     "config_keys": ["launchbox_path", "launchbox_media_mode",
                     "launchbox_import_json"],
     "env": [],
     "verify": "python3 ludodex/launchbox_import.py --no-media | head"},

    {"id": "playnite", "name": "Playnite (frontend)", "kind": "source",
     "purpose": "Two-way sync with Playnite (metadata + cover/background/icon art). "
                "Playnite stores its library in LiteDB, so a small PowerShell bridge "
                "runs inside Playnite; ludodex speaks one canonical JSON both ways. "
                "Like LaunchBox it's a meta-layer (provenance only, in_playnite).",
     "url": "(no signup — runs inside your Playnite install)",
     "steps": [
         "In Playnite (Extensions > Execute script) run the bridge to EXPORT its "
         "library + art paths to JSON:  .\\scripts/playnite_bridge.ps1 -Export -Path "
         "playnite_games.json   — copy it to this machine and set: config.py set "
         "playnite_import_json <path>  (then update.sh imports it).",
         "To push BACK: python3 ludodex/playnite_export.py  (writes ludodex_to_playnite.json "
         "+ a <name>_media/ art bundle). Copy BOTH to the Playnite machine and run: "
         ".\\scripts/playnite_bridge.ps1 -Import -Path ludodex_to_playnite.json",
         "Tune behavior: playnite_media_overwrite (gaps|all|playnite-wins) and "
         "playnite_icon_source (logo|cover|none)."],
     "config_keys": ["playnite_import_json", "playnite_export_json",
                     "playnite_media_overwrite", "playnite_icon_source"],
     "env": [],
     "verify": "python3 ludodex/playnite_export.py --no-media /tmp/pn_check.json"},

    {"id": "igdb", "name": "IGDB (metadata)", "kind": "metadata",
     "purpose": "Enrich attributes (genres/themes/devs/ratings) + cover/artwork "
                "images. Auth = a free Twitch application.",
     "url": "https://dev.twitch.tv/console/apps",
     "steps": [
         "Go to https://dev.twitch.tv/console/apps and Register Your Application: "
         "any name; OAuth Redirect URL http://localhost; Category 'Application "
         "Integration'.",
         "Copy the Client ID and generate a Client Secret.",
         "Store: config.py set igdb_client_id <id>  and  config.py set "
         "igdb_client_secret <secret>  (or enter both in the web UI), then: "
         "config.py enable igdb."],
     "config_keys": ["igdb_client_id", "igdb_client_secret"],
     "env": ["IGDB_CLIENT_ID", "IGDB_CLIENT_SECRET"],
     "verify": "python3 ludodex/igdb_enrich.py --limit 1"},

    {"id": "screenscraper", "name": "ScreenScraper (metadata + media)",
     "kind": "metadata",
     "purpose": "The emulation community's database — genres/dev/publisher/players/"
                "rating PLUS box/wheel/fanart/screenshots/video for your ROMs, in "
                "one scrape per game. Fills emulation metadata + backgrounds.",
     "url": "https://www.screenscraper.fr/forumsujets.php?frub=12",
     "steps": [
         "Make a free account at screenscraper.fr (your login = ssid). Past "
         "contribution / Patreon raises your tier (threads + daily quota).",
         "Request a software devid/devpassword on the dev forum "
         "https://www.screenscraper.fr/forumsujets.php?frub=12 (free, "
         "non-commercial use qualifies; can take days/weeks — request early). "
         "This is MANDATORY and separate from your account.",
         "Store dev creds: config.py set screenscraper_devid <id>; config.py set "
         "screenscraper_devpassword <pw>. Store your account: "
         "screenscraper_ssid / screenscraper_sspassword (or enter all in the web UI).",
         "Check tier/quota: python3 ludodex/ss_scrape.py --status. Scrape: python3 "
         "ss_scrape.py (resumable; stops at the daily cap). Tiers: free ≈1 "
         "thread / ~20k req-day; ~5€/mo +1 thread; ~10€/mo +5 threads / ~50k "
         "req-day. The engine reads your live quota and adapts."],
     "config_keys": ["screenscraper_devid", "screenscraper_devpassword",
                     "screenscraper_ssid", "screenscraper_sspassword",
                     "screenscraper_softname"],
     "env": ["SS_DEVID", "SS_DEVPASSWORD", "SS_SSID", "SS_SSPASSWORD"],
     "verify": "python3 ludodex/ss_scrape.py --status"},

    {"id": "thegamesdb", "name": "TheGamesDB (metadata + boxart)",
     "kind": "metadata",
     "purpose": "The other scraper the emulation world knows — ES-DE ships exactly "
                "two built-in scrapers and this is one of them, alongside "
                "ScreenScraper. Smaller coverage than IGDB or ScreenScraper, so it "
                "sits below both, but it needs no account beyond a key and is the "
                "usual fallback when ScreenScraper is throttled or down.",
     "url": "https://api.thegamesdb.net/key.php",
     "steps": [
         "Request a key at https://api.thegamesdb.net/key.php — application name, a "
         "sentence on how you will use it, and an application URL if you have a "
         "public one. Approval averages about an hour.",
         "Store it: config.py set thegamesdb_api_key <key> (or paste it in the web "
         "UI), then: config.py enable thegamesdb.",
         "A free key is 1,000 requests PER MONTH — not per day. ludodex batches 20 "
         "games per request and takes boxart on the same call, which is what makes "
         "that budget workable; leave it batching.",
         "Increases are BOUGHT, not requested, and the one worth naming is the "
         "$29/year DEVELOPER tier: 12,000 requests a month, enough to walk the whole "
         "id space (about 6,900 requests) in one evening. The small Patreon tiers "
         "raise the free 1,000 by amounts they do not publish, so they are the wrong "
         "product to point at. Raise thegamesdb_monthly_limit to what you were "
         "granted; the live allowance the API reports always wins, so setting it too "
         "high is ignored rather than overspent."],
     "config_keys": ["thegamesdb_api_key", "thegamesdb_monthly_limit",
                     "thegamesdb_reserve"],
     "env": ["TGDB_API_KEY"],
     "verify": "python3 ludodex/thegamesdb.py --status"},

    {"id": "mobygames", "name": "MobyGames (metadata + art)",
     "kind": "metadata",
     "purpose": "332,414 games — the largest curated catalogue here after IGDB, and the "
                "cheapest id space. Its limit is 720 requests an HOUR rather than a "
                "monthly budget, and /games pages 100 at a time, so the WHOLE id space "
                "is 3,325 requests (~4.5 hours). Editorial descriptions, Moby Score, "
                "genre categories that split cleanly into genres/themes/perspectives, "
                "and 260,337 product codes (disc serials) if you want them.",
     "url": "https://www.mobygames.com/info/api/",
     "steps": [
         "Subscribe on their API page — $9.99/mo Hobbyist. NON-COMMERCIAL ONLY: this "
         "data can never end up in anything sold, and that restriction travels with it.",
         "Store it: config.py set mobygames_api_key <key>, then: config.py enable "
         "mobygames.",
         "Pacing comes from mobygames_hourly_limit (720 documented; legacy keys 360). "
         "Lower it to be gentler; never raise it past what your key has.",
         "Leave mobygames_walk_format on 'normal' — id, brief and normal all page at "
         "100 and cost one request each, so the metadata is free.",
         "PRODUCT CODES ARE OFF ON PURPOSE. They live at /games/{id}/platforms/{pid}, "
         "one request per game per platform, ~430,000 requests / ~25 days. Turn "
         "mobygames_product_codes on only after scoping it to platforms you own."],
     "config_keys": ["mobygames_api_key", "mobygames_hourly_limit",
                     "mobygames_walk_format", "mobygames_product_codes",
                     "mobygames_full_covers"],
     "env": ["MOBYGAMES_API_KEY"],
     "verify": "python3 ludodex/mobygames.py --status"},

    {"id": "arcadedb", "name": "ArcadeDB (arcade metadata + media)",
     "kind": "metadata",
     "purpose": "Arcade only, and the best thing available for it. Built on the official "
                "MAME files, keyed on the SET NAME (pacman, mslug3) so there is no name "
                "matching to get wrong. Supplies year/manufacturer/genre/players plus "
                "marquee, cabinet, control panel, flyer and short-play video — and "
                "cabinet facts nothing else has (screen orientation, input controls).",
     "url": "https://adb.arcadeitalia.net",
     "steps": [
         "Nothing to sign up for — no key, no account, no quota.",
         "It is on by default. To turn it off: config.py disable arcadedb.",
         "Be polite: it is a volunteer-run preservation site. The default 700ms "
         "cooldown is deliberate; raise it, do not lower it "
         "(Settings > Services > Limits, service 'arcadedb')."],
     "config_keys": ["metadata_arcadedb_enabled"],
     "env": [],
     "verify": "python3 ludodex/arcadedb.py pacman"},

    {"id": "zxinfo", "name": "ZXInfo (ZX Spectrum)",
     "kind": "metadata",
     "purpose": "The World of Spectrum archive. Narrow and deep: exact machine variant "
                "(48K / 128K / +2), authors named individually with their roles, "
                "publisher, original price, loading screens. Covers a corner of an "
                "emulation library that IGDB and TheGamesDB barely touch.",
     "url": "https://zxinfo.dk",
     "steps": [
         "Nothing to sign up for — no key, no account, no quota.",
         "On by default; config.py disable zxinfo to stop consulting it.",
         "NB the live API is /v3. Skyscraper's documentation points at an older path "
         "that now 404s."],
     "config_keys": ["metadata_zxinfo_enabled"],
     "env": [],
     "verify": "python3 ludodex/zxinfo.py manic miner"},

    {"id": "libretro-dats", "name": "No-Intro / Redump DATs (disc serials)",
     "kind": "metadata",
     "purpose": "The canonical dump databases, via libretro-database (CC BY-SA). This "
                "is where DISC SERIALS come from — the only identifier that survives "
                "conversion to CHD or RVZ, which every hash does not. ~174,000 dumps "
                "with crc/md5/sha1, region and serial. Folded into the match index.",
     "url": "https://github.com/libretro/libretro-database",
     "steps": [
         "Nothing to sign up for — no key, no account, no quota.",
         "Fetch the DATs: python3 ludodex/libretro_dats.py --fetch  (cached 30 days).",
         "They fold in on the next match-index build: "
         "python3 ludodex/matchindex.py --build.",
         "To skip the layer: config.py set matchindex_libretro_dats 0."],
     "config_keys": ["matchindex_libretro_dats"],
     "env": [],
     "verify": "python3 ludodex/libretro_dats.py"},

    {"id": "steamgriddb", "name": "SteamGridDB (media)", "kind": "media",
     "purpose": "Community grids/heroes/logos/icons — a media gap-filler.",
     "url": "https://www.steamgriddb.com/profile/preferences/api",
     "steps": [
         "Get a free API key at "
         "https://www.steamgriddb.com/profile/preferences/api.",
         "Store: config.py set steamgriddb_api_key <key>  (or enter it in the web "
         "UI), then: config.py enable steamgriddb."],
     "config_keys": ["steamgriddb_api_key"],
     "env": ["STEAMGRIDDB_API_KEY"],
     "verify": "python3 ludodex/media_fetch.py --steamgriddb --limit 1"},

    {"id": "esde", "name": "ES-DE media (RetroDECK / EmuDeck)", "kind": "media",
     "purpose": "Local emulation art (covers/marquees/screenshots/…). No auth — "
                "register the downloaded_media folder as a media mount.",
     "url": "(local filesystem)",
     "steps": [
         "RetroDECK: <retrodeck>/ES-DE/downloaded_media. EmuDeck: "
         "<Emulation>/tools/downloaded_media.",
         "Register it: config.py media-mount add \"<path>\" esde [name].",
         "Index: python3 ludodex/media_index.py."],
     "config_keys": [], "env": [],
     "verify": "python3 ludodex/config.py media-mounts"},

    {"id": "pocketbase", "name": "PocketBase (sync)", "kind": "sync",
     "purpose": "Mirror the catalog to a PocketBase instance for other apps/devices.",
     "url": "(your PocketBase admin UI)",
     "steps": [
         "Create a superuser on the server: docker exec <container> "
         "/pb/pocketbase superuser upsert <email> <password>.",
         "Store: config.py set pocketbase_url <url>; config.py set "
         "pocketbase_admin_email <email>; config.py set pocketbase_admin_password "
         "<pw>  (or enter it in the web UI).",
         "Point the BACKING STORE at it: config.py set backingstore_backend "
         "pocketbase  (sync_target was the retired one-way mirror; dbsync.py is the "
         "two-way store that actually protects your data)."],
     "config_keys": ["pocketbase_url", "pocketbase_admin_email",
                     "pocketbase_admin_password"],
     "env": ["POCKETBASE_PASSWORD"],
     "verify": "python3 ludodex/dbsync.py pocketbase --dry-run"},

    {"id": "firebase", "name": "Firebase Firestore (sync)", "kind": "sync",
     "purpose": "Mirror the catalog to Firestore (alternative/addition to PocketBase).",
     "url": "https://console.firebase.google.com/",
     "steps": [
         "Firebase console -> Project settings -> Service accounts -> Generate "
         "new private key (downloads a JSON).",
         "Store: config.py set firebase_project_id <project>; config.py set "
         "firebase_sa_json /path/to/service-account.json.",
         "pip install -r requirements-firebase.txt; config.py set "
         "backingstore_backend firebase."],
     "config_keys": ["firebase_project_id", "firebase_sa_json"], "env": [],
     "verify": "python3 ludodex/dbsync.py firebase --dry-run"},
]

NO_AUTH_NOTE = ("emulation/archive (local ROM index + crawl mounts), Steam-grid "
                "local art, Steam CDN art, and IGDB images (reuses IGDB creds) "
                "need no separate credential.")

# Commercial-use posture per integration WITHOUT separate licensing. Conservative
# by design: this drives an opt-in "commercial mode" (commercial_safe_only) that
# excludes data sources not cleared for a paid/hosted product, so the codebase is
# ready for that pivot without rework. ScreenScraper is free/non-commercial unless
# you obtain their prior authorization; community/publisher ART is IP-gated; your
# own ownership data and your own infrastructure are fine. (id -> (ok, note))
COMMERCIAL = {
    "steam":        (True,  "reading your own owned-games list"),
    "epic":         (True,  "your own ownership data"),
    "gog":          (True,  "your own ownership data"),
    "itch":         (True,  "your own ownership data"),
    "ea":           (True,  "your own ownership data"),
    "igdb":         (True,  "commercial terms exist (verify current terms + attribution)"),
    "screenscraper": (False, "free/non-commercial by default — needs ScreenScraper's prior authorization"),
    "steamgriddb":  (False, "community/publisher art — IP-gated"),
    "esde":         (False, "your local files, but the art derives from ScreenScraper — same IP posture"),
    "pocketbase":   (True,  "your own infrastructure"),
    "firebase":     (True,  "your own infrastructure"),
}


def commercial_ok(provider):
    """True if this provider's data is cleared for commercial use without a
    separate license. Unknown providers default to True (treated as own-data)."""
    return COMMERCIAL.get(provider, (True, ""))[0]


def commercial_safe_only():
    """When set, the engine should run only commercially-cleared providers."""
    return get_bool("commercial_safe_only", False)


def _has_cred(it):
    """Best-effort 'is this integration configured?' for the status column."""
    if it["id"] == "steam":
        return bool(steam_key() and get("steam_id"))
    if it["id"] == "itch":
        return bool(itch_key())
    if it["id"] == "igdb":
        return all(igdb_creds())
    if it["id"] == "steamgriddb":
        return bool(steamgriddb_key())
    if it["id"] == "screenscraper":
        return bool(screenscraper_creds())
    if it["id"] == "ea":
        return os.path.exists(os.path.join(DATA, ".ea", "token.json")) or \
            bool(get("ea_remid"))
    if it["id"] == "epic":
        return os.path.exists(os.path.expanduser("~/.config/legendary/user.json"))
    if it["id"] == "gog":
        return os.path.exists(os.path.join(DATA, ".gog", "tokens.json"))
    if it["id"] == "esde":
        return bool(media_mounts_list(only_enabled=True, provider="esde"))
    for k in it.get("config_keys", []):
        if get(k):
            return True
    return False


def print_integration(it, detail=True):
    mark = "[ok]" if _has_cred(it) else "[  ]"
    cok, cnote = COMMERCIAL.get(it["id"], (True, ""))
    tag = "commercial:yes" if cok else "commercial:NO"
    print("%s %-14s %-9s %-34s %s" % (mark, it["id"], it["kind"], it["name"], tag))
    if not detail:
        return
    print("    %s" % it["purpose"])
    print("    commercial use: %s%s" % ("OK without a separate license" if cok
                                        else "NOT without authorization",
                                        " — " + cnote if cnote else ""))
    if it.get("url") and it["url"].startswith("http"):
        print("    obtain: %s" % it["url"])
    for i, s in enumerate(it["steps"], 1):
        print("      %d. %s" % (i, s))
    if it.get("config_keys"):
        print("    config keys: %s" % ", ".join(it["config_keys"]))
    if it.get("env"):
        print("    env override: %s" % ", ".join(it["env"]))
    if it.get("verify"):
        print("    verify: %s" % it["verify"])
    print()


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = argv[0]
    if cmd == "init":
        init()
        print("config.sqlite ready at %s" % DB)
    elif cmd == "list":
        init()
        con = _con()
        for k, v, c in con.execute(
                "SELECT key,value,description FROM config ORDER BY key"):
            print("%-20s = %s" % (k, v if v else "(unset)"))
            print("    %s" % c)
        con.close()
    elif cmd == "get":
        if len(argv) < 2:
            sys.exit("usage: config.py get <key>")
        sys.stdout.write(get(argv[1]) or "")
    elif cmd == "steam-key":
        sys.stdout.write(steam_key())
    elif cmd == "itch-key":
        sys.stdout.write(itch_key())
    elif cmd in ("enable", "disable"):
        if len(argv) < 2:
            sys.exit("usage: config.py %s <source>" % cmd)
        name, on = argv[1], cmd == "enable"
        if name in BUILTIN_SOURCES:
            set_("source_%s_enabled" % name, "1" if on else "0")
            print("%sd source %s" % (cmd, name))
        elif name in METADATA_PROVIDERS:
            set_("metadata_%s_enabled" % name, "1" if on else "0")
            print("%sd metadata provider %s" % (cmd, name))
        elif name in MEDIA_PROVIDERS:
            set_("media_%s_enabled" % name, "1" if on else "0")
            print("%sd media provider %s" % (cmd, name))
        elif archive_set_enabled(name, on) or media_mount_set_enabled(name, on):
            print("%sd mount %s" % (cmd, name))
        else:
            sys.exit("unknown source %r (built-ins: %s; metadata: %s; media: %s;"
                     " or a mount name)" % (name, ", ".join(BUILTIN_SOURCES),
                                            ", ".join(METADATA_PROVIDERS),
                                            ", ".join(MEDIA_PROVIDERS)))
    elif cmd == "sources":
        print("built-in sources:")
        for s in BUILTIN_SOURCES:
            mark = "x" if get_bool("source_%s_enabled" % s, True) else " "
            print("  [%s] %s" % (mark, s))
        print("metadata providers (enrich attributes, not sources):")
        for m in METADATA_PROVIDERS:
            mark = "x" if metadata_enabled(m) else " "
            extra = "" if m != "igdb" else (
                "" if all(igdb_creds()) else "  (no Twitch creds — set igdb_client_id"
                "/igdb_client_secret)")
            print("  [%s] %s%s" % (mark, m, extra))
        archs = archives_list()
        print("crawl mounts/paths:" if archs else
              "crawl mounts/paths: (none — add: config.py mount add <path> [rom|flat])")
        for a in archs:
            print("  [%s] %-16s %-7s %-8s %s" %
                  ("x" if a["enabled"] else " ", a["name"], a["kind"],
                   path_status(a["path"]), a["path"]))
        print("media providers (image/video assets, indexed by reference):")
        for m in MEDIA_PROVIDERS:
            mark = "x" if media_enabled(m) else " "
            extra = ""
            if m == "steamgriddb" and media_enabled(m) and not steamgriddb_key():
                extra = "  (no API key — set steamgriddb_api_key)"
            print("  [%s] %s%s" % (mark, m, extra))
        mms = media_mounts_list()
        if mms:
            print("media mounts:")
            for a in mms:
                print("  [%s] %-16s %-9s %-8s %s" %
                      ("x" if a["enabled"] else " ", a["name"], a["provider"],
                       path_status(a["path"]), a["path"]))
    elif cmd in ("mount", "mounts"):
        sub = argv[1] if cmd == "mount" and len(argv) > 1 else "list"
        if sub == "add" and len(argv) >= 3:
            path = os.path.abspath(os.path.expanduser(argv[2]))
            opts = argv[3:]
            kind = next((o for o in opts if o in ("rom", "flat")), "rom")
            named = [o for o in opts if o not in ("rom", "flat")]
            name = (named[0] if named else os.path.basename(path.rstrip("/"))
                    or "root").replace(" ", "_")
            archive_set(name, path, kind)
            print("mount %r -> %s  (%s) [%s]" %
                  (name, path, kind, path_status(path)))
        elif sub == "rm" and len(argv) >= 3:
            archive_rm(argv[2])
            print("removed mount %r" % argv[2])
        else:                                   # list (default)
            archs = archives_list()
            if not archs:
                print("no crawl mounts — add: config.py mount add <path> [rom|flat]")
            for a in archs:
                print("[%s] %-16s %-7s %-8s %s" %
                      ("on" if a["enabled"] else "off", a["name"], a["kind"],
                       path_status(a["path"]), a["path"]))
    elif cmd in ("media-mount", "media-mounts"):
        sub = argv[1] if cmd == "media-mount" and len(argv) > 1 else "list"
        if sub == "add" and len(argv) >= 3:
            path = os.path.abspath(os.path.expanduser(argv[2]))
            opts = argv[3:]
            provider = next((o for o in opts if o in MEDIA_PROVIDERS), "esde")
            named = [o for o in opts if o not in MEDIA_PROVIDERS]
            name = (named[0] if named else os.path.basename(path.rstrip("/"))
                    or "root").replace(" ", "_")
            media_mount_set(name, path, provider)
            print("media mount %r -> %s  (%s) [%s]" %
                  (name, path, provider, path_status(path)))
        elif sub == "rm" and len(argv) >= 3:
            media_mount_rm(argv[2])
            print("removed media mount %r" % argv[2])
        else:
            mms = media_mounts_list()
            if not mms:
                print("no media mounts — add: config.py media-mount add <path> "
                      "[esde|steamgrid]")
            for a in mms:
                print("[%s] %-16s %-9s %-8s %s" %
                      ("on" if a["enabled"] else "off", a["name"],
                       a["provider"], path_status(a["path"]), a["path"]))
    elif cmd == "archive":
        sub = argv[1] if len(argv) > 1 else ""
        if sub == "add" and len(argv) >= 4:
            kind = argv[4] if len(argv) > 4 else "rom"
            if kind not in ("rom", "flat"):
                sys.exit("kind must be 'rom' or 'flat'")
            archive_set(argv[2], os.path.abspath(os.path.expanduser(argv[3])), kind)
            print("added archive %r (%s) -> %s" % (argv[2], kind, argv[3]))
        elif sub == "rm" and len(argv) >= 3:
            archive_rm(argv[2])
            print("removed archive %r" % argv[2])
        elif sub == "list":
            for a in archives_list():
                print("%-16s %s  (%s, %s)" % (a["name"], a["path"], a["kind"],
                                              "on" if a["enabled"] else "off"))
        else:
            sys.exit("usage: config.py archive add <name> <path> [rom|flat]"
                     " | rm <name> | list")
    elif cmd in ("integrations", "integration", "guide", "creds"):
        if len(argv) > 1:                       # detail for one integration
            want = argv[1].lower()
            hit = next((i for i in INTEGRATIONS if i["id"] == want), None)
            if not hit:
                sys.exit("unknown integration %r — one of: %s"
                         % (want, ", ".join(i["id"] for i in INTEGRATIONS)))
            print_integration(hit, detail=True)
        else:                                   # overview of all
            print("Integrations — credential setup ([ok] = looks configured).\n"
                  "Details for one: config.py integrations <id>\n")
            for kind in ("source", "metadata", "media", "sync"):
                print("%s:" % kind.upper())
                for it in INTEGRATIONS:
                    if it["kind"] == kind:
                        print_integration(it, detail=False)
                print()
            print("No credential needed: %s" % NO_AUTH_NOTE)
            print("\nFull guide: AUTH.md   |   verify all: bash scripts/auth_status.sh")
    elif cmd == "set":
        if len(argv) < 3:
            sys.exit("usage: config.py set <key> <value>")
        set_(argv[1], argv[2])
        print("set %s" % argv[1])
    elif cmd == "setup":
        init()
        print("Fill each setting (press Enter to keep the current value).")
        for k, _d, c in SCHEMA:
            cur = get(k)
            print("\n# %s — %s" % (k, c))
            new = input("%s [%s]: " % (k, cur)).strip()
            if new:
                set_(k, new)
        print("\nSaved to config.sqlite. Verify with: bash scripts/auth_status.sh")
    else:
        sys.exit("unknown command %r — use init|setup|list|get|set|steam-key|"
                 "itch-key|sources|enable|disable|mount|mounts|archive|"
                 "media-mount|media-mounts|integrations" % cmd)


if __name__ == "__main__":
    main(sys.argv[1:])
