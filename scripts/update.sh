#!/usr/bin/env bash
# Refresh PC-store ownership (Steam/Epic/GOG), rebuild the unified game library,
# and report what's new since the last run. ROM index is reused as-is unless you
# pass --roms (which re-scans the ROM host; slow). All auth is cached — no prompts.
# Account/environment-specific values come from config (see config.py).
# THE REPO ROOT, not scripts/. This file lived at the root until it moved into
# scripts/ (608e201) and the `cd` did not follow, so every `python3 ludodex/...` below
# resolved to `scripts/ludodex/...` and could not be opened. Pinned by
# tests/test_repo_shell_entrypoints.py.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
cd "$ROOT" || exit 1
export PATH="$HOME/.local/bin:$HOME/.local/share/pnpm:$PATH"
# The package is not installed, so `import config` inside a -c one-liner needs it on
# the path. Same class of breakage as the `cd`: a bare `import config` from the root
# is a ModuleNotFoundError.
export PYTHONPATH="$ROOT/ludodex${PYTHONPATH:+:$PYTHONPATH}"

# Ownership TSVs must land wherever the app keeps its state, NOT beside the scripts.
# build_library reads them from $LUDODEX_DATA and only falls back to the script dir when
# NO store TSV exists there — so a CLI run writing here while the app has its own copies
# produced a split brain: the fresh CLI pull was silently ignored.
DATA_DIR="${LUDODEX_DATA:-$(pwd)}"
mkdir -p "$DATA_DIR"

cfg() { python3 ludodex/config.py get "$1"; }
enabled() { [ "$(cfg "source_$1_enabled")" != 0 ]; }   # default on
meta_enabled() { [ "$(cfg "metadata_$1_enabled")" != 0 ]; }   # default on
LIB=$(cfg library_db)
ROM_DB=$(cfg roms_index_db)

echo "== snapshotting current library for diff =="
if [ -f "$LIB" ]; then
  sqlite3 "$LIB" "SELECT norm_key FROM games" | sort > .prev_keys
else
  : > .prev_keys
fi

echo "== Steam =="
if ! enabled steam; then echo "  steam: disabled"; else
  KEY=$(python3 ludodex/config.py steam-key)
  if [ -n "$KEY" ]; then
    STEAM_API_KEY="$KEY" python3 ludodex/steam_owned.py > "$DATA_DIR/steam_games.tsv" 2>steam.err \
      && echo "  steam: $(wc -l < "$DATA_DIR/steam_games.tsv") games" \
      || echo "  steam FAILED: $(tail -1 steam.err)"
    STEAM_API_KEY="$KEY" python3 ludodex/steam_wishlist.py > "$DATA_DIR/steam_wishlist.tsv" 2>steam_wl.err \
      && echo "  steam wishlist: $(wc -l < "$DATA_DIR/steam_wishlist.tsv") wanted" \
      || echo "  steam wishlist: $(tail -1 steam_wl.err 2>/dev/null)"
  else echo "  steam: no API key (run ./setup.sh, or set steam_api_key)"; fi
fi

echo "== Epic =="
if ! enabled epic; then echo "  epic: disabled"; else
  python3 ludodex/epic_owned.py 2>epic.err && echo "  epic: $(wc -l < "$DATA_DIR/epic_games.tsv") games" \
    || echo "  epic FAILED: $(tail -1 epic.err)"
fi

echo "== GOG =="
if ! enabled gog; then echo "  gog: disabled"; else
  python3 ludodex/gog_owned.py > "$DATA_DIR/gog_games.tsv" 2>gog.err && echo "  gog: $(wc -l < "$DATA_DIR/gog_games.tsv") games" \
    || echo "  gog FAILED: $(tail -1 gog.err)"
  python3 ludodex/gog_wishlist.py > "$DATA_DIR/gog_wishlist.tsv" 2>gog_wl.err && echo "  gog wishlist: $(wc -l < "$DATA_DIR/gog_wishlist.tsv") wanted" \
    || echo "  gog wishlist: $(tail -1 gog_wl.err 2>/dev/null)"
fi

echo "== itch.io =="
if ! enabled itch; then echo "  itch: disabled"; else
  if [ -n "$(python3 ludodex/config.py itch-key)" ]; then
    python3 ludodex/itch_owned.py > "$DATA_DIR/itch_games.tsv" 2>itch.err && echo "  itch: $(wc -l < "$DATA_DIR/itch_games.tsv") games" \
      || echo "  itch FAILED: $(tail -1 itch.err)"
  else echo "  itch: no API key (run ./setup.sh, or set itch_api_key)"; fi
fi

echo "== EA =="
if ! enabled ea; then echo "  ea: disabled"; else
  if python3 ludodex/ea_owned.py > "$DATA_DIR/ea_games.tsv" 2>ea.err; then
    echo "  ea: $(wc -l < "$DATA_DIR/ea_games.tsv") games"
  else echo "  ea: $(tail -1 ea.err 2>/dev/null) (set up once: python3 ludodex/ea_owned.py --login)"; fi
fi

if [ "$1" = "--roms" ]; then
  echo "== ROM rescan =="
  HOST=$(cfg unraid_host); RPATH=$(cfg roms_path)
  if [ -z "$HOST" ] || [ -z "$RPATH" ]; then
    echo "  skipped: set unraid_host + roms_path via config.py"
  else
    scp -q ludodex/build_romdb.py ludodex/romtags.py "$HOST":/tmp/ \
      && ssh "$HOST" "find \"$RPATH\" -type f -printf '%s\t%T@\t%P\n' > /tmp/romscan.tsv 2>/dev/null && python3 /tmp/build_romdb.py /tmp/romscan.tsv /tmp/roms-index.sqlite \"$RPATH\"" \
      && scp -q "$HOST":/tmp/roms-index.sqlite "$ROM_DB" \
      && echo "  roms reindexed" || echo "  rom rescan FAILED"
  fi
fi

echo "== local archives =="
python3 ludodex/crawl.py          # stage 1: append new files to the inventory
python3 ludodex/process.py        # stage 2: extract system/title/attributes from new files

# LaunchBox: a frontend meta-layer (like Playnite). Read its Platform XMLs into
# the interchange JSON build_library merges, and index its Images/ as media.
# No-ops unless source enabled and launchbox_path points at a real install.
if [ "$(python3 -c 'import config; print(int(config.source_enabled("launchbox")))')" = "1" ] && \
   [ -n "$(cfg launchbox_path)" ] && [ -d "$(cfg launchbox_path)" ]; then
  echo "== LaunchBox import =="
  python3 ludodex/launchbox_import.py 2>&1 | sed 's/^/  /'
fi

echo "== rebuilding unified library =="
python3 ludodex/build_library.py

# metadata providers enrich attributes AFTER the catalog exists, then we re-merge
if meta_enabled igdb && \
   [ -n "$(python3 -c 'import config; print("1" if all(config.igdb_creds()) else "")')" ]; then
  echo "== IGDB metadata enrichment =="
  python3 ludodex/igdb_enrich.py && python3 ludodex/build_library.py
fi

# ScreenScraper: emulation metadata + media in one scrape (tier-aware, resumable,
# stops at the daily quota). No-ops without a devid.
if meta_enabled screenscraper && \
   [ -n "$(python3 -c 'import config; print("1" if config.screenscraper_creds() else "")')" ]; then
  echo "== ScreenScraper (emulation metadata + media) =="
  python3 ludodex/ss_scrape.py 2>&1 | sed 's/^/  /'
  python3 ludodex/build_library.py >/dev/null      # re-merge ScreenScraper metadata
fi

echo "== media index =="
python3 ludodex/media_index.py 2>&1 | sed 's/^/  /'      # local: ES-DE + Steam grid
# Playnite's own cover/background/icon art, indexed as the 'playnite' provider.
if [ "$(python3 -c 'import config; print(int(config.source_enabled("playnite")))')" = "1" ] && \
   [ -n "$(cfg playnite_import_json)" ] && [ -f "$(cfg playnite_import_json)" ]; then
  python3 ludodex/playnite_import.py 2>&1 | sed 's/^/  /'
fi
python3 ludodex/media_fetch.py 2>&1 | sed 's/^/  /'      # remote refs: Steam CDN + IGDB + ScreenScraper
python3 ludodex/media_choose.py 2>&1 | tail -1 | sed 's/^/  /'   # pick the best per game
# (materialize is on-demand: python3 ludodex/media_choose.py --materialize; ~17GB if all)

# Push the consolidated catalog + chosen art BACK to the desktop frontends. Opt-in
# (writes into your LaunchBox/Playnite installs) — run manually when they're reachable:
#   python3 ludodex/launchbox_export.py            # -> launchbox_path (--link for shared/Unraid)
#   python3 ludodex/playnite_export.py             # -> Playnite interchange JSON (bridge -Import)

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

# The two-way BACKING STORE (dbsync.py). This used to run `sync.py`, the one-way
# "publish catalog" mirror, which was retired 2026-07-21 — the file has not existed
# since, so this step failed silently on every run. dbsync.py is the replacement and
# is the thing that actually protects the data.
BACKEND=$(cfg backingstore_backend)
if [ -n "$BACKEND" ]; then
  echo "== syncing backing store ($BACKEND) =="
  python3 ludodex/dbsync.py "$BACKEND" || echo "  backing-store sync FAILED (see above)"
fi
