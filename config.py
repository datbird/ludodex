#!/usr/bin/env python3
"""ludodex configuration — account/environment settings stored in SQLite.

All personal & environment-specific values (your SteamID, 1Password item name,
ROM host/paths, etc.) live in a `config` table inside config.sqlite, which is
gitignored — they are NOT hardcoded in any committed script. Other scripts read
them with:  `import config; config.get("steam_id")`.

Only SAFE/PUBLIC defaults ship in this file's SCHEMA. Fill your real values
locally (they go into config.sqlite, never into git):

  python3 config.py setup          # interactively fill every key (Enter = keep)
  python3 config.py list           # show all keys, values, descriptions
  python3 config.py get <key>      # print one value (used by the shell scripts)
  python3 config.py set <key> <value>
  python3 config.py steam-key      # resolve the Steam key (env > config > 1Password)
  python3 config.py init           # just create/seed config.sqlite

  python3 config.py sources                       # list sources + metadata + state
  python3 config.py enable|disable <name>         # toggle steam/epic/gog/itch/
                                                  #   emulation/playnite, an archive,
                                                  #   or a metadata provider (igdb)
  python3 config.py mount add <path> [rom|flat] [name]     # add a crawl mount/path
  python3 config.py mounts                         # list crawl paths + mount status
  python3 config.py mount rm <name>
  python3 config.py archive add <name> <path> [rom|flat]   # (same registry, name-first)

For first-time onboarding with credential how-to guidance, run ./setup.sh instead.
"""
import os
import sqlite3
import subprocess
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "config.sqlite")

# (key, default, description). Defaults are SAFE/PUBLIC only. Personal values are
# filled locally via `setup`/`set` and stored in the gitignored config.sqlite.
SCHEMA = [
    ("steam_id", "",
     "Your SteamID64 — the account that owns the Steam Web API key. Find it in "
     "~/.steam/steam/config/loginusers.vdf (17-digit key). NOT the vanity URL, "
     "which can be a different account."),
    ("op_vault", "",
     "1Password vault holding the Steam Web API key item (read via the `opx` CLI)."),
    ("steam_key_op_item", "",
     "1Password item name whose 'apikey' field is your Steam Web API key "
     "(used only if steam_api_key below is blank)."),
    ("steam_api_key", "",
     "Steam Web API key stored locally (config.sqlite is gitignored). Optional — "
     "leave blank to fetch it from 1Password via op_vault/steam_key_op_item. The "
     "STEAM_API_KEY env var overrides both."),
    ("itch_api_key", "",
     "itch.io API key stored locally (gitignored). Get one at "
     "https://itch.io/user/settings/api-keys. Optional — leave blank to use "
     "1Password via op_vault/itch_key_op_item. ITCH_API_KEY env overrides both."),
    ("itch_key_op_item", "",
     "1Password item name whose 'apikey' field is your itch.io API key "
     "(used only if itch_api_key is blank)."),
    ("library_db", os.path.join(DIR, "game-library.sqlite"),
     "Output path for the unified deduped catalog."),
    ("roms_index_db", os.path.join(DIR, "roms-index.sqlite"),
     "Path to the ROM index DB (emulation input, built by build_romdb.py)."),
    ("unraid_host", "",
     "ssh target where the ROM archive lives, e.g. root@192.168.1.10 "
     "(only needed for `update.sh --roms`)."),
    ("roms_path", "",
     "Absolute path to the ROM archive on that host (only needed for --roms)."),
    ("gog_client_id", "46899977096215655",
     "GOG Galaxy public OAuth client id (the shipped default works for everyone)."),
    ("gog_client_secret",
     "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9",
     "GOG Galaxy public OAuth client secret (the shipped default works for everyone)."),
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
    ("ea_op_item", "",
     "1Password item holding the EA 'remid' cookie (durable login) in its "
     "'credential'/'password' field. Used if .ea/cookies.json is absent. Set up "
     "the login once with: python3 ea_owned.py --login."),
    ("source_emulation_enabled", "1", "[source] Include the emulation ROM index."),
    ("source_playnite_enabled", "1", "[source] Include an imported Playnite library."),
    ("playnite_import_json", "", "Path to the Playnite export JSON produced by "
     "playnite_bridge.ps1 (-Export); ingested by build_library."),
    ("playnite_export_json", "", "Where playnite_export.py writes the ludodex->"
     "Playnite JSON (blank = ludodex_to_playnite.json next to the scripts)."),
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
     "Twitch application Client Secret, stored locally (gitignored). Optional — "
     "leave blank to fetch from 1Password via igdb_op_item. IGDB_CLIENT_SECRET "
     "env overrides both."),
    ("igdb_op_item", "",
     "1Password item holding the Twitch/IGDB creds: its 'username' field = Client "
     "ID, its 'credential' (or 'password') field = Client Secret. Used only if "
     "the local values above are blank."),
    ("igdb_meta_ttl_days", "30",
     "[metadata] Days before a cached IGDB record is considered stale and "
     "re-fetched by igdb_enrich.py."),
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
     "Remote; needs an API key (steamgriddb_api_key or steamgriddb_op_item)."),
    ("steam_grid_path", "",
     "Steam userdata grid folder for the steamgrid media provider. Blank = "
     "autodetect ~/.steam/steam/userdata/<id>/config/grid."),
    ("steamgriddb_api_key", "",
     "SteamGridDB API key (https://www.steamgriddb.com/profile/preferences/api). "
     "Blank = fetch from 1Password via steamgriddb_op_item."),
    ("steamgriddb_op_item", "",
     "1Password item holding the SteamGridDB API key in its 'credential' (or "
     "'password') field. Used only if steamgriddb_api_key is blank."),
    ("media_repo", "",
     "Local content-addressed repo where CHOSEN media is materialized for "
     "export/sync/serving. Blank = <scripts-dir>/media."),
    # --- remote sync (push the catalog to a remote DB mirror) ---
    ("sync_target", "",
     "Where `update.sh` pushes the catalog after a rebuild: blank (off), "
     "'pocketbase', 'firebase', or 'both'."),
    ("pocketbase_url", "",
     "PocketBase base URL, e.g. https://pb.example.com (no trailing slash)."),
    ("pocketbase_admin_email", "",
     "PocketBase admin/superuser email used for the sync."),
    ("pocketbase_admin_password", "",
     "PocketBase admin password, stored locally (gitignored). Optional — leave "
     "blank to use 1Password via pocketbase_op_item. POCKETBASE_PASSWORD env wins."),
    ("pocketbase_op_item", "",
     "1Password item (its 'password' field) for the PocketBase admin, if not local."),
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


def _op_field(item, vault, field):
    """Read one field of a 1Password item via the opx CLI ("" on any failure)."""
    if not (item and vault):
        return ""
    try:
        r = subprocess.run(
            ["opx", "item", "get", item, "--vault", vault,
             "--fields", field, "--reveal"],
            capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _resolve_key(env_var, local_key, op_item_key, field="apikey"):
    """Resolve a secret: env var > local config value > 1Password (opx)."""
    k = os.environ.get(env_var, "").strip()
    if k:
        return k
    k = get(local_key)
    if k:
        return k
    return _op_field(get(op_item_key), get("op_vault"), field)


def steam_key():
    return _resolve_key("STEAM_API_KEY", "steam_api_key", "steam_key_op_item")


def itch_key():
    return _resolve_key("ITCH_API_KEY", "itch_api_key", "itch_key_op_item")


def pocketbase_password():
    return _resolve_key("POCKETBASE_PASSWORD", "pocketbase_admin_password",
                        "pocketbase_op_item", field="password")


def igdb_creds():
    """Resolve the Twitch (IGDB) Client ID + Secret -> (client_id, client_secret).

    env (IGDB_CLIENT_ID / IGDB_CLIENT_SECRET) > local config > 1Password
    (igdb_op_item: 'username' = id, 'credential'/'password' = secret)."""
    cid = os.environ.get("IGDB_CLIENT_ID", "").strip() or get("igdb_client_id")
    csec = os.environ.get("IGDB_CLIENT_SECRET", "").strip() or get("igdb_client_secret")
    if cid and csec:
        return cid, csec
    vault, item = get("op_vault"), get("igdb_op_item")
    if vault and item:
        if not cid:
            cid = _op_field(item, vault, "username")
        if not csec:
            csec = _op_field(item, vault, "credential") or \
                _op_field(item, vault, "password")
    return cid, csec


# --------------------------------------------------------------------------- #
#  sources: built-in store/emulation toggles + a registry of crawl archives
# --------------------------------------------------------------------------- #
BUILTIN_SOURCES = ("steam", "epic", "gog", "itch", "ea", "emulation", "playnite")

# Metadata providers are CONSULTED to enrich attributes — they are NOT sources
# (they add no ownership). Toggled like sources but tracked separately.
METADATA_PROVIDERS = ("igdb",)


def metadata_enabled(name):
    """True if a metadata provider (e.g. igdb) is enabled."""
    return get_bool("metadata_%s_enabled" % name, True)


# Media providers: local mount-based (esde, steamgrid, playnite) + remote
# (steam, igdb, steamgriddb). Kept in sync with media.MEDIA_PROVIDERS.
MEDIA_PROVIDERS = ("esde", "steamgrid", "playnite", "steam", "igdb",
                   "steamgriddb")


def media_enabled(name):
    """True if a media provider is enabled (steamgriddb defaults off)."""
    return get_bool("media_%s_enabled" % name, name != "steamgriddb")


def steamgriddb_key():
    """Resolve the SteamGridDB API key: env > local config > 1Password."""
    k = os.environ.get("STEAMGRIDDB_API_KEY", "").strip() or get("steamgriddb_api_key")
    if k:
        return k
    vault, item = get("op_vault"), get("steamgriddb_op_item")
    if vault and item:
        return _op_field(item, vault, "credential") or \
            _op_field(item, vault, "password") or ""
    return ""


def _media_con():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS media_mounts("
                "name TEXT PRIMARY KEY, path TEXT, provider TEXT, "
                "enabled INTEGER DEFAULT 1)")
    return con


def media_mounts_list(only_enabled=False, provider=None):
    con = _media_con()
    q = "SELECT name,path,provider,enabled FROM media_mounts"
    cond = []
    if only_enabled:
        cond.append("enabled=1")
    if provider:
        cond.append("provider=%r" % provider)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    rows = [{"name": n, "path": p, "provider": pr, "enabled": e}
            for n, p, pr, e in con.execute(q + " ORDER BY name")]
    con.close()
    return rows


def media_mount_set(name, path, provider="esde", enabled=1):
    con = _media_con()
    con.execute("INSERT INTO media_mounts(name,path,provider,enabled) "
                "VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "path=excluded.path, provider=excluded.provider, "
                "enabled=excluded.enabled", (name, path, provider, int(enabled)))
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
                "/igdb_client_secret or igdb_op_item)")
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
                extra = "  (no API key — set steamgriddb_api_key/op_item)"
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
        print("\nSaved to config.sqlite. Verify with: bash auth_status.sh")
    else:
        sys.exit("unknown command %r — use init|setup|list|get|set|steam-key|"
                 "itch-key|sources|enable|disable|mount|mounts|archive|"
                 "media-mount|media-mounts" % cmd)


if __name__ == "__main__":
    main(sys.argv[1:])
