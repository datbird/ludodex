#!/bin/sh
# ScreenScraper mirror maintenance: fill the KNOWN gaps first, then keep walking.
#
# ORDER MATTERS AND IT IS NOT ARBITRARY. The walk spends the entire daily quota in about
# two hours. If it starts first, the targeted fetch gets nothing until the next day. So
# the known-missing ids are fetched FIRST on every cycle, and the walk gets what is left.
#
# THE TARGETED FETCH MUST BE INSIDE THE LOOP. The first version put it above the loop.
# It ran once at container start, returned {"skipped":"cooldown"} because the quota was
# already spent, and was never called again — the one job it existed for would have been
# silently dropped. A retry that only happens once is not a retry.
#
# WHY A TARGETED FETCH EXISTS AT ALL. The walk finds an id by arriving at it, so it can
# only fix what lies ahead of the cursor. Reconciling the mirror against ScreenScraper's
# own published per-system counts on 2026-08-18 found 739 absent games, and 368 of them
# sat BEHIND the cursor. The walk had already passed those and would never return. Their
# ids came from the web UI, which is not the API and costs nothing against the quota.
IDS=/data/ss-missing-ids.json
MARK=/data/ss-missing-ids.done
LOG=/data/ssmirror.log

while true; do

  # 1. Known-missing ids, once, before the walk can spend the day's quota.
  #    A skip (cooldown or quota) is NOT success, so the marker stays unwritten and this
  #    runs again next cycle.
  if [ -f "$IDS" ] && [ ! -f "$MARK" ]; then
    echo "=== targeted fetch of known-missing ids ===" >> "$LOG"
    OUT=$(python3 /app/ludodex/ss_mirror.py --ids "$IDS" 2>&1)
    echo "$OUT" >> "$LOG"
    if ! echo "$OUT" | grep -q '"skipped"'; then
      date -u +"%Y-%m-%dT%H:%M:%SZ" > "$MARK"
      echo "targeted fetch complete; marker written" >> "$LOG"
    fi
  fi

  # 2. Walk to genuine exhaustion. No --until-id: the catalog decides where the end is.
  #    DEAD_RUN_STOP is 50,000, set from the largest measured hole (12,785) with margin.
  python3 /app/ludodex/ss_mirror.py --walk >> "$LOG" 2>&1
  DONE=$(python3 -c 'import sys; sys.path.insert(0, "/app/ludodex"); import ss_mirror as M; print(1 if M.status()["walk_complete"] else 0)')
  if [ "$DONE" = "1" ]; then
    echo "WALK COMPLETE - catalog exhausted" >> "$LOG"
    break
  fi
  sleep 1800
done
sleep infinity
