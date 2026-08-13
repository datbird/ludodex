#!/usr/bin/env bash
# Report auth status for each game source. Prints "<source>: OK ..." or
# "<source>: BROKEN ...". Used by the games-auth skill to decide what to re-auth.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH"

# --- Steam (Web API key + configured SteamID; key never expires) ---
KEY=$(python3 ludodex/config.py steam-key)
if [ -z "$KEY" ]; then
  echo "steam: BROKEN  no API key (run ./setup.sh, or set steam_api_key)"
else
  N=$(STEAM_API_KEY="$KEY" python3 ludodex/steam_owned.py 2>/dev/null | wc -l)
  if [ "$N" -gt 0 ]; then echo "steam: OK  ($N games)"
  else echo "steam: BROKEN  key present but 0 games (key revoked, or wrong config steam_id)"; fi
fi

# --- Epic (legendary cached token) ---
ES=$(legendary status 2>/dev/null | grep -i 'Epic account:' | sed 's/.*: *//')
if [ -n "$ES" ] && [ "$ES" != "<not logged in>" ]; then echo "epic: OK  (account $ES)"
else echo "epic: BROKEN  not logged in — re-auth needed"; fi

# --- GOG (cached OAuth refresh token) ---
if [ -f .gog/tokens.json ] && python3 ludodex/gog_owned.py >/dev/null 2>.gogchk; then
  echo "gog: OK"
else
  echo "gog: BROKEN  $( [ -f .gog/tokens.json ] && echo 'refresh failed (token expired)' || echo 'no cached token' ) — re-auth needed"
fi
rm -f .gogchk

# --- itch.io (API key) ---
if [ -z "$(python3 ludodex/config.py itch-key)" ]; then
  echo "itch: BROKEN  no API key (run ./setup.sh, or set itch_api_key)"
elif OUT=$(python3 ludodex/itch_owned.py 2>.itchchk); then
  echo "itch: OK  ($(printf '%s\n' "$OUT" | grep -c .) games)"
else
  echo "itch: BROKEN  $(tail -1 .itchchk)"
fi
rm -f .itchchk

# --- EA app/Origin (remid cookie -> silent token refresh, or browser token) ---
if [ ! -f .ea/cookies.json ] && [ ! -f .ea/token.json ] && [ -z "$(python3 ludodex/config.py get ea_remid)" ] && [ -z "$EA_REMID" ]; then
  echo "ea: BROKEN  not logged in — see AUTH.md § EA (remid cookie, or --token)"
elif WHO=$(python3 ludodex/ea_owned.py --whoami 2>.eachk); then
  echo "ea: OK  ($WHO)"
else
  echo "ea: BROKEN  $(tail -1 .eachk 2>/dev/null) — re-login: python3 ludodex/ea_owned.py --login"
fi
rm -f .eachk
