#!/usr/bin/env bash
# ludodex first-time setup wizard.
# Initializes the config DB and walks you through obtaining + entering each
# credential, then authenticates each store and builds the first catalog.
# Safe to re-run: existing config values are shown as defaults (Enter keeps them).
set -u
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH"

b() { printf '\033[1m%s\033[0m\n' "$*"; }          # bold
dim() { printf '\033[2m%s\033[0m\n' "$*"; }        # dim
rule() { printf '\033[2m%s\033[0m\n' "------------------------------------------------------------"; }
cfg() { python3 config.py get "$1"; }
setcfg() { python3 config.py set "$1" "$2" >/dev/null; }

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
command -v opx >/dev/null && echo "  opx: ok (optional 1Password)" || echo "  opx: not found — optional (only if you store the Steam key in 1Password)"
echo
python3 config.py init
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
echo "  Where should the API key live?"
echo "    1) here, in the local config DB (config.sqlite is gitignored)   [simplest]"
echo "    2) in 1Password, read via the opx CLI"
read -rp "  choice [1]: " skc; skc=${skc:-1}
if [ "$skc" = 2 ]; then
  ask op_vault "  1Password vault"
  ask steam_key_op_item "  1Password item name (its 'apikey' field holds the key)"
  setcfg steam_api_key ""
else
  read -rp "  paste your Steam Web API key (leave blank to skip): " sk
  [ -n "$sk" ] && setcfg steam_api_key "$sk"
fi
if [ -n "$(python3 config.py steam-key)" ]; then echo "  steam key: resolved ok"; else echo "  steam key: not set yet (you can add it later)"; fi
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
if [ -f .gog/tokens.json ]; then
  echo "  already have a cached GOG token."
elif yes "  Authenticate GOG now?"; then
  read -rp "  paste code: " gc
  [ -n "$gc" ] && python3 gog_owned.py --code "$gc" >/dev/null && echo "  GOG: token cached"
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
echo "  Where should the itch.io API key live?"
echo "    1) here, in the local config DB (gitignored)   [simplest]"
echo "    2) in 1Password, read via the opx CLI"
read -rp "  choice [1]: " ikc; ikc=${ikc:-1}
if [ "$ikc" = 2 ]; then
  ask op_vault "  1Password vault"
  ask itch_key_op_item "  1Password item name (its 'apikey' field holds the key)"
  setcfg itch_api_key ""
else
  read -rp "  paste your itch.io API key (leave blank to skip): " ik
  [ -n "$ik" ] && setcfg itch_api_key "$ik"
fi
if [ -n "$(python3 config.py itch-key)" ]; then echo "  itch key: resolved ok"; else echo "  itch key: not set yet (you can add it later)"; fi
echo

# --- emulation ROMs (optional) ----------------------------------------------
rule; b "5) Emulation ROMs (optional)"
cat <<'TXT'
ludodex can fold in a ROM archive. build_romdb.py scans a directory tree into
roms-index.sqlite (parses No-Intro/GoodTools tags for system/region/version).
Set a host + path only if your ROMs live on another machine reached over ssh;
for a local archive, just point roms_index_db at a DB you build with:
  python3 build_romdb.py <filelist.tsv> roms-index.sqlite <roms-root>
TXT
if yes "  Configure a remote ROM host for 'update.sh --roms'?"; then
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

# --- build -------------------------------------------------------------------
rule; b "Verifying auth"
bash auth_status.sh
echo
if yes "Build the catalog now?"; then
  python3 build_library.py
fi
echo
b "Done."
echo "Update anytime with:  bash update.sh        (add --roms to rescan ROMs)"
echo "Inspect settings:     python3 config.py list"
