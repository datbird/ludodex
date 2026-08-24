#!/usr/bin/env bash
# ludodex first-time setup wizard.
# Initializes the config DB and walks you through obtaining + entering each
# credential, then authenticates each store and builds the first catalog.
# Safe to re-run: existing config values are shown as defaults (Enter keeps them).
set -u
# THE REPO ROOT, not scripts/ — see the note in update.sh. Pinned by
# tests/test_repo_shell_entrypoints.py.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
cd "$ROOT" || exit 1
export PATH="$HOME/.local/bin:$PATH"
export PYTHONPATH="$ROOT/ludodex${PYTHONPATH:+:$PYTHONPATH}"
# Cached store tokens live under the DATA dir, not necessarily beside the repo.
DATA_DIR="${LUDODEX_DATA:-$ROOT}"

b() { printf '\033[1m%s\033[0m\n' "$*"; }          # bold
dim() { printf '\033[2m%s\033[0m\n' "$*"; }        # dim
rule() { printf '\033[2m%s\033[0m\n' "------------------------------------------------------------"; }
cfg() { python3 ludodex/config.py get "$1"; }
setcfg() { python3 ludodex/config.py set "$1" "$2" >/dev/null; }

# ask KEY "Prompt text"  -> prompt showing current value; Enter keeps it
ask() {
  local key="$1" prompt="$2" cur new
  cur=$(cfg "$key")
  read -rp "$prompt [${cur}]: " new
  [ -n "$new" ] && setcfg "$key" "$new"
}
yes() { local a; read -rp "$1 [y/N]: " a; [ "$a" = y ] || [ "$a" = Y ]; }
# prefask KEY "question"  -> y/n toggle defaulting to the current value (Enter keeps it)
prefask() {
  local key="$1" q="$2" cur def a
  cur=$(cfg "$key"); [ "$cur" = 1 ] && def="Y/n" || def="y/N"
  read -rp "  $q [$def]: " a
  [ -z "$a" ] && return
  case "$a" in y|Y) setcfg "$key" 1;; *) setcfg "$key" 0;; esac
}

b "=== ludodex setup ==="
echo "Builds one local catalog of every game you own — emulation ROMs + Steam/Epic/GOG."
echo

# --- prerequisites -----------------------------------------------------------
b "Checking prerequisites"
command -v python3 >/dev/null || { echo "  python3 is required."; exit 1; }
echo "  python3: ok"
command -v sqlite3 >/dev/null && echo "  sqlite3: ok" || echo "  sqlite3: MISSING (needed to query the catalog)"
command -v legendary >/dev/null && echo "  legendary: ok (Epic)" || echo "  legendary: not found — needed only for Epic (pipx install legendary)"
echo
python3 ludodex/config.py init
echo

# --- Steam -------------------------------------------------------------------
rule; b "1) Steam"
cat <<'TXT'
Ownership is read via the Steam Web API — no login/2FA, but you need two things:

  a) A Web API key (free):
       https://steamcommunity.com/dev/apikey
       Log in as the account whose games you want; Domain Name = localhost.
       The key bypasses profile privacy ONLY for the account that created it.

  b) That account's SteamID64 (17 digits). Find it via:
       - https://steamid.io  (paste your profile URL), or
       - the file ~/.steam/steam/config/loginusers.vdf, or
       - your profile URL if it's already numeric (/profiles/7656119...).
     NOTE: a vanity URL (/id/name) can map to a DIFFERENT account — use the
     numeric SteamID64 of the key's owner, or you'll get 0 games.
TXT
echo
ask steam_id "  SteamID64"
echo
read -rp "  paste your Steam Web API key (leave blank to skip): " sk
[ -n "$sk" ] && setcfg steam_api_key "$sk"
if [ -n "$(python3 ludodex/config.py steam-key)" ]; then echo "  steam key: resolved ok"; else echo "  steam key: not set yet (you can add it later)"; fi
echo

# --- Epic --------------------------------------------------------------------
rule; b "2) Epic"
cat <<'TXT'
Epic uses the `legendary` CLI (cached login, auto-refreshes afterward).
  - Install if needed:  pipx install legendary-gl
  - Auth: open https://legendary.gl/epiclogin, log in, copy the
    "authorizationCode" value from the JSON it shows.
TXT
if command -v legendary >/dev/null; then
  if legendary status 2>/dev/null | grep -qi 'Epic account:'; then
    echo "  already logged in: $(legendary status 2>/dev/null | grep -i 'Epic account:' | sed 's/.*: *//')"
  elif yes "  Authenticate Epic now?"; then
    read -rp "  paste authorizationCode: " ec
    [ -n "$ec" ] && legendary auth --code "$ec"
  fi
else
  echo "  (skipping — legendary not installed)"
fi
echo

# --- GOG ---------------------------------------------------------------------
rule; b "3) GOG"
cat <<'TXT'
GOG uses a one-time OAuth code (then a cached refresh token).
  Open this URL, log in, and copy the code= value from the final redirect
  (embed.gog.com/on_login_success?...&code=XXXX):
TXT
echo "  https://auth.gog.com/auth?client_id=$(cfg gog_client_id)&redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient&response_type=code&layout=client2"
echo
if [ -f "$DATA_DIR/.gog/tokens.json" ]; then
  echo "  already have a cached GOG token."
elif yes "  Authenticate GOG now?"; then
  read -rp "  paste code: " gc
  [ -n "$gc" ] && python3 ludodex/gog_owned.py --code "$gc" >/dev/null && echo "  GOG: token cached"
fi
echo

# --- itch.io -----------------------------------------------------------------
rule; b "4) itch.io"
cat <<'TXT'
itch.io ownership is read with a personal API key.
  Generate one at:  https://itch.io/user/settings/api-keys
  Click "Generate new API key" and copy it (any scope can read your library).
TXT
echo
read -rp "  paste your itch.io API key (leave blank to skip): " ik
[ -n "$ik" ] && setcfg itch_api_key "$ik"
if [ -n "$(python3 ludodex/config.py itch-key)" ]; then echo "  itch key: resolved ok"; else echo "  itch key: not set yet (you can add it later)"; fi
echo

# --- emulation ROMs (optional) ----------------------------------------------
rule; b "5) Emulation ROMs (optional)"
cat <<'TXT'
ludodex can fold in a ROM archive. build_romdb.py scans a directory tree into
roms-index.sqlite (parses No-Intro/GoodTools tags for system/region/version).
Set a host + path only if your ROMs live on another machine reached over ssh;
for a local archive, just point roms_index_db at a DB you build with:
  python3 ludodex/build_romdb.py <filelist.tsv> roms-index.sqlite <roms-root>
TXT
if yes "  Configure a remote ROM host for 'scripts/update.sh --roms'?"; then
  ask unraid_host "  ssh target (e.g. root@192.168.1.10)"
  ask roms_path "  ROM archive path on that host"
fi
ask roms_index_db "  ROM index DB path"
ask library_db "  output catalog DB path"
echo

# --- preferences -------------------------------------------------------------
rule; b "6) Preferences"
echo "These tune what counts as owned and how titles are deduped (Enter keeps current)."
echo
prefask steam_include_free   "Count played free-to-play Steam games (TF2, Dota) as owned?"
prefask dedupe_preserve_years "Keep release years so remakes stay separate from originals?"
prefask dedupe_strip_editions "Merge remasters/editions into their base game?"
echo

# --- metadata enrichment (optional) -----------------------------------------
rule; b "7) Metadata enrichment — IGDB (optional)"
cat <<'TXT'
IGDB (igdb.com) is a metadata PROVIDER, not a source: it fills in MISSING
attributes (genres, themes, developers, release dates, ratings) on games you
already have — owned-source data is never overwritten. It needs free Twitch app
credentials:
  1. Sign in at https://dev.twitch.tv/console/apps and "Register Your Application"
     (any name; OAuth Redirect URL: http://localhost; Category: Application
     Integration).
  2. Open the app -> copy the Client ID, then "New Secret" -> copy the Client
     Secret. Leave both blank below to skip IGDB (you can add them later).
TXT
ask igdb_client_id "  IGDB/Twitch Client ID (blank to skip)"
if [ -n "$(cfg igdb_client_id)" ]; then
  read -rp "  IGDB/Twitch Client Secret (blank to skip; env IGDB_CLIENT_SECRET overrides): " igs
  [ -n "$igs" ] && setcfg igdb_client_secret "$igs"
fi
echo

# --- remote sync (optional) --------------------------------------------------
rule; b "8) Backing store (optional)"
cat <<'TXT'
Keep your durable data (ownership, tags, manual fixes) in an external database.
SQLite stays the fast local cache; the backend holds the truth, and dbsync.py
reconciles the two both ways after every update. Backends: pocketbase, postgres,
supabase, mysql, firebase — or blank to disable.

NB this replaced the old one-way "publish catalog" mirror (`sync_target`), which
was retired in 2026-07: nothing ever read what it published.
TXT
ask backingstore_backend "  backend (blank/pocketbase/postgres/supabase/mysql/firebase)"
T=$(cfg backingstore_backend)
case "$T" in
  pocketbase)
    ask pocketbase_url "  PocketBase URL (https://...)"
    ask pocketbase_admin_email "  PocketBase admin email"
    read -rp "  PocketBase admin password (blank to skip; env POCKETBASE_PASSWORD overrides): " pbp
    [ -n "$pbp" ] && setcfg pocketbase_admin_password "$pbp" ;;
  postgres|supabase)
    echo "  A connection URL is enough; the discrete fields are the alternative."
    ask postgres_url "  connection URL (postgresql://user:pass@host:5432/ludodex)"
    if [ -z "$(cfg postgres_url)" ]; then
      ask postgres_host "  Postgres host"
      ask postgres_port "  Postgres port"
      ask postgres_db   "  database name"
      ask postgres_user "  user"
      read -rp "  password (blank to skip): " pgp
      [ -n "$pgp" ] && setcfg postgres_password "$pgp"
    fi
    [ "$T" = supabase ] && ask supabase_url "  Supabase project URL"
    ;;
  mysql)
    ask mysql_host "  MySQL/MariaDB host"
    ask mysql_port "  port"
    ask mysql_db   "  database name"
    ask mysql_user "  user"
    read -rp "  password (blank to skip): " myp
    [ -n "$myp" ] && setcfg mysql_password "$myp" ;;
esac
case "$T" in
  firebase)
    cat <<'TXT'
  Firebase (Firestore) — how to get the credentials:
    1. Create/pick a project at https://console.firebase.google.com
    2. Build > Firestore Database > Create database  (choose Native mode).
    3. Project settings (gear) > Service accounts > "Generate new private key"
       -> downloads a JSON key. (Equivalent in Google Cloud: IAM > Service
        Accounts, grant the role "Cloud Datastore User".)
    4. Copy that JSON onto this machine and give its path below.
TXT
    ask firebase_project_id "  Firebase project id"
    ask firebase_sa_json "  path to the service-account JSON"
    ask firebase_database "  Firestore database id (blank = (default))"
    ask firebase_collection_prefix "  collection name prefix (optional, e.g. ludodex_)"
    if ! python3 -c "import google.oauth2.service_account" 2>/dev/null; then
      echo "  google-auth (required for Firebase) is not installed."
      if yes "  Install it now?"; then
        python3 -m pip install --user --quiet google-auth \
          && echo "  google-auth installed" \
          || echo "  install failed — run: python3 -m pip install --user google-auth"
      fi
    fi ;;
esac
echo

# --- local mounts / archives (optional) -------------------------------------
rule; b "9) Local mounts / archives (optional)"
cat <<'TXT'
Add local folders or drives (SD card, USB, NAS mount) for the crawler to scan.
kind 'rom' recurses (first subfolder = system, ROM files only, tags cleaned);
kind 'flat' = each immediate child is a title. Add as many as you like; blank
path to finish. Toggle later with `config.py enable|disable <name>`; an unplugged
drive is skipped automatically (its indexed games stay).
TXT
while true; do
  read -rp "  mount path (blank to finish): " ap
  [ -z "$ap" ] && break
  read -rp "    kind [rom/flat]: " ak; ak=${ak:-rom}
  read -rp "    name [auto from path]: " an
  python3 ludodex/config.py mount add "$ap" "$ak" $an
done
echo

# --- build -------------------------------------------------------------------
rule; b "Verifying auth"
bash scripts/auth_status.sh
echo
if yes "Build the catalog now?"; then
  python3 ludodex/build_library.py
fi
echo
b "Done."
echo "Update anytime with:  bash scripts/update.sh   (add --roms to rescan ROMs)"
echo "Inspect settings:     python3 ludodex/config.py list"
