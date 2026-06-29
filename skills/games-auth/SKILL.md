---
name: games-auth
description: Check and (re)authenticate the user's game-ownership sources — Steam, Epic, GOG — for the unified game library. Use when the user wants to re-auth / fix / set up / log in to a game store, when a games-update pull fails with an auth error, or asks to check whether their game-store logins still work. Walks through the minimal one-time login per store.
---

# Game-source authentication

The unified game library (`~/game-ownership/`, see the `games-update` skill) pulls
ownership from **Steam, Epic, GOG**. All three cache credentials so normal updates
need no interaction — this skill is only for the rare re-auth (token expired, password
changed, fresh machine).

## Step 1 — check what's broken

```bash
bash ~/game-ownership/auth_status.sh
```
Prints `OK`/`BROKEN` per source. Only re-auth the BROKEN ones.

## Step 2 — re-auth the broken source(s)

### Steam — Web API key (rarely needed; the key does not expire)
Steam works via a Web API key, NOT a login. The key's privacy-bypass works **only for
its owner's SteamID**, which is **`<steam-id>`** (account name `datbird`, the
account the Deck is logged into per `~/.steam/steam/config/loginusers.vdf`).
⚠️ The vanity `/id/datbird` is a DIFFERENT account (`<steam-id-2>`) — never use it.
Do NOT pursue Steam *password* login — the stored password is stale and Steam rejects
both the CM and WebAuth login flows; the API key is the only working path.

If `steam: BROKEN`:
1. Ask the user to generate a key at **https://steamcommunity.com/dev/apikey** while
   logged into the `datbird` (`<steam-id>`) account; domain field = `localhost`.
2. Save it: update 1Password item `<vault> › Steam Web API (datbird main)` field
   `apikey`, or `opx item edit "Steam Web API (datbird main)" --vault <vault> "apikey[password]=<KEY>"`.
3. Verify: `bash ~/game-ownership/auth_status.sh`.

### Epic — legendary (browser authorization code)
1. Ask the user to open **https://legendary.gl/epiclogin**, log in (their browser/2FA),
   and copy the **`authorizationCode`** value from the JSON shown.
2. Run: `legendary auth --code <CODE>`  (PATH includes `~/.local/bin`).
3. Verify: `legendary status` should show `Epic account: datbird`.
Token then auto-refreshes; cached in `~/.config/legendary`.

### GOG — Galaxy OAuth (browser code)
1. Ask the user to open this URL, log in, and copy the **`code=`** value from the final
   redirect URL (`embed.gog.com/on_login_success?...&code=XXXX`):
   `https://auth.gog.com/auth?client_id=46899977096215655&redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient&response_type=code&layout=client2`
2. Run: `python3 ~/game-ownership/gog_owned.py --code <CODE>`
3. It caches a refresh token in `~/game-ownership/.gog/tokens.json` (auto-refreshes).

## Step 3 — confirm + refresh data
After fixing auth, run the **games-update** skill (`bash ~/game-ownership/update.sh`) to
pull and rebuild.

## Notes
- Codes (Epic/GOG) are single-use and expire in minutes — request a fresh one if it sits.
- Steam Guard codes rotate ~30s; not needed for the API-key path anyway.
- `opx` = the 1Password CLI wrapper (service-account token).
