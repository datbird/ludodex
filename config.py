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


def _resolve_key(env_var, local_key, op_item_key, field="apikey"):
    """Resolve a secret: env var > local config value > 1Password (opx)."""
    k = os.environ.get(env_var, "").strip()
    if k:
        return k
    k = get(local_key)
    if k:
        return k
    vault, item = get("op_vault"), get(op_item_key)
    if vault and item:
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


def steam_key():
    return _resolve_key("STEAM_API_KEY", "steam_api_key", "steam_key_op_item")


def itch_key():
    return _resolve_key("ITCH_API_KEY", "itch_api_key", "itch_key_op_item")


def pocketbase_password():
    return _resolve_key("POCKETBASE_PASSWORD", "pocketbase_admin_password",
                        "pocketbase_op_item", field="password")


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
        sys.exit("unknown command %r — use init|setup|list|get|set|steam-key|itch-key" % cmd)


if __name__ == "__main__":
    main(sys.argv[1:])
