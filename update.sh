#!/usr/bin/env bash
# Refresh PC-store ownership (Steam/Epic/GOG), rebuild the unified game library,
# and report what's new since the last run. ROM index is reused as-is unless you
# pass --roms (which re-scans the ROM host; slow). All auth is cached — no prompts.
# Account/environment-specific values come from config (see config.py).
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.local/share/pnpm:$PATH"

cfg() { python3 config.py get "$1"; }
LIB=$(cfg library_db)
ROM_DB=$(cfg roms_index_db)

echo "== snapshotting current library for diff =="
if [ -f "$LIB" ]; then
  sqlite3 "$LIB" "SELECT norm_key FROM games" | sort > .prev_keys
else
  : > .prev_keys
fi

echo "== Steam =="
KEY=$(python3 config.py steam-key)
if [ -n "$KEY" ]; then
  STEAM_API_KEY="$KEY" python3 steam_owned.py > steam_games.tsv 2>steam.err \
    && echo "  steam: $(wc -l < steam_games.tsv) games" \
    || echo "  steam FAILED: $(tail -1 steam.err)"
else echo "  steam: no API key (run ./setup.sh, or set steam_api_key / op_vault+steam_key_op_item)"; fi

echo "== Epic =="
python3 epic_owned.py 2>epic.err && echo "  epic: $(wc -l < epic_games.tsv) games" \
  || echo "  epic FAILED: $(tail -1 epic.err)"

echo "== GOG =="
python3 gog_owned.py > gog_games.tsv 2>gog.err && echo "  gog: $(wc -l < gog_games.tsv) games" \
  || echo "  gog FAILED: $(tail -1 gog.err)"

echo "== itch.io =="
if [ -n "$(python3 config.py itch-key)" ]; then
  python3 itch_owned.py > itch_games.tsv 2>itch.err && echo "  itch: $(wc -l < itch_games.tsv) games" \
    || echo "  itch FAILED: $(tail -1 itch.err)"
else echo "  itch: no API key (run ./setup.sh, or set itch_api_key)"; fi

if [ "$1" = "--roms" ]; then
  echo "== ROM rescan =="
  HOST=$(cfg unraid_host); RPATH=$(cfg roms_path)
  if [ -z "$HOST" ] || [ -z "$RPATH" ]; then
    echo "  skipped: set unraid_host + roms_path via config.py"
  else
    scp -q build_romdb.py "$HOST":/tmp/build_romdb.py \
      && ssh "$HOST" "find \"$RPATH\" -type f -printf '%s\t%T@\t%P\n' > /tmp/romscan.tsv 2>/dev/null && python3 /tmp/build_romdb.py /tmp/romscan.tsv /tmp/roms-index.sqlite \"$RPATH\"" \
      && scp -q "$HOST":/tmp/roms-index.sqlite "$ROM_DB" \
      && echo "  roms reindexed" || echo "  rom rescan FAILED"
  fi
fi

echo "== rebuilding unified library =="
python3 build_library.py

echo "== new since last run =="
sqlite3 "$LIB" "SELECT norm_key FROM games" | sort > .cur_keys
comm -13 .prev_keys .cur_keys > .new_keys
LIB="$LIB" python3 - <<'PY'
import os, sqlite3
here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd()
keys = [l.strip() for l in open(os.path.join(here, ".new_keys")) if l.strip()]
print("%d new game(s)" % len(keys))
if keys:
    con = sqlite3.connect(os.environ["LIB"])
    q = ("SELECT canonical_title, sources_summary FROM games WHERE norm_key IN (%s) "
         "ORDER BY canonical_title" % ",".join("?" * len(keys)))
    for t, s in con.execute(q, keys):
        print("  + %s  [%s]" % (t, s))
PY

TARGET=$(cfg sync_target)
if [ -n "$TARGET" ]; then
  echo "== syncing to remote ($TARGET) =="
  python3 sync.py || echo "  sync FAILED (see above)"
fi
