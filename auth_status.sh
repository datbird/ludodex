#!/usr/bin/env bash
# Report auth status for each game source. Prints "<source>: OK ..." or
# "<source>: BROKEN ...". Used by the games-auth skill to decide what to re-auth.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH"

# --- Steam (Web API key + fixed SteamID; key never expires) ---
KEY=$(opx item get "Steam Web API (datbird main)" --vault <vault> --fields apikey --reveal 2>/dev/null)
if [ -z "$KEY" ]; then
  echo "steam: BROKEN  no API key in 1Password (<vault> > 'Steam Web API (datbird main)')"
else
  N=$(STEAM_API_KEY="$KEY" python3 steam_owned.py 2>/dev/null | wc -l)
  if [ "$N" -gt 0 ]; then echo "steam: OK  ($N games)"
  else echo "steam: BROKEN  key present but 0 games (key revoked, or wrong SteamID — must be <steam-id>)"; fi
fi

# --- Epic (legendary cached token) ---
ES=$(legendary status 2>/dev/null | grep -i 'Epic account:' | sed 's/.*: *//')
if [ -n "$ES" ] && [ "$ES" != "<not logged in>" ]; then echo "epic: OK  (account $ES)"
else echo "epic: BROKEN  not logged in — re-auth needed"; fi

# --- GOG (cached OAuth refresh token) ---
if [ -f .gog/tokens.json ] && python3 gog_owned.py >/dev/null 2>.gogchk; then
  echo "gog: OK"
else
  echo "gog: BROKEN  $( [ -f .gog/tokens.json ] && echo 'refresh failed (token expired)' || echo 'no cached token' ) — re-auth needed"
fi
rm -f .gogchk
