#!/usr/bin/env bash
# Refresh PC-store ownership (Steam/Epic/GOG), rebuild the unified game library,
# and report what's new since the last run. ROM index is reused as-is unless you
# pass --roms (which re-scans Unraid; slow). All auth is cached — no prompts.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.local/share/pnpm:$PATH"

echo "== snapshotting current library for diff =="
if [ -f game-library.sqlite ]; then
  sqlite3 game-library.sqlite "SELECT norm_key FROM games" | sort > .prev_keys
else
  : > .prev_keys
fi

echo "== Steam =="
KEY=$(opx item get "Steam Web API (datbird main)" --vault <vault> --fields apikey --reveal 2>/dev/null)
if [ -n "$KEY" ]; then
  STEAM_API_KEY="$KEY" python3 steam_owned.py > steam_games.tsv 2>steam.err \
    && echo "  steam: $(wc -l < steam_games.tsv) games" \
    || echo "  steam FAILED: $(tail -1 steam.err)"
else echo "  steam: no API key in 1Password"; fi

echo "== Epic =="
python3 epic_owned.py 2>epic.err && echo "  epic: $(wc -l < epic_games.tsv) games" \
  || echo "  epic FAILED: $(tail -1 epic.err)"

echo "== GOG =="
python3 gog_owned.py > gog_games.tsv 2>gog.err && echo "  gog: $(wc -l < gog_games.tsv) games" \
  || echo "  gog FAILED: $(tail -1 gog.err)"

if [ "$1" = "--roms" ]; then
  echo "== ROM rescan (Unraid) =="
  ssh root@<docker-host> 'find "<media-share>/media/software/emulation/roms" -type f -printf "%s\t%T@\t%P\n" > /tmp/romscan.tsv 2>/dev/null && python3 /tmp/build_romdb.py /tmp/romscan.tsv /tmp/roms-index.sqlite "<media-share>/media/software/emulation/roms"' \
    && scp -q root@<docker-host>:/tmp/roms-index.sqlite /home/deck/roms-index.sqlite \
    && echo "  roms reindexed" || echo "  rom rescan FAILED"
fi

echo "== rebuilding unified library =="
python3 build_library.py

echo "== new since last run =="
sqlite3 game-library.sqlite "SELECT norm_key FROM games" | sort > .cur_keys
comm -13 .prev_keys .cur_keys > .new_keys
python3 - <<'PY'
import sqlite3
keys = [l.strip() for l in open('/home/deck/game-ownership/.new_keys') if l.strip()]
print("%d new game(s)" % len(keys))
if keys:
    con = sqlite3.connect('/home/deck/game-ownership/game-library.sqlite')
    q = "SELECT canonical_title, sources_summary FROM games WHERE norm_key IN (%s) ORDER BY canonical_title" % ",".join("?"*len(keys))
    for t, s in con.execute(q, keys):
        print("  + %s  [%s]" % (t, s))
PY
