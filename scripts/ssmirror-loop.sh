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
#
# DONE IS A CLAIM, AND IT HAS TO BE MADE OUT LOUD. This used to write the completion
# marker whenever the output did NOT contain the word "skipped" — so a day on which all
# 739 ids failed (quota, bad credentials, timeouts: none of which say "skipped") was
# recorded as complete and never retried. ss_mirror now returns "complete": true only
# when every requested id got an ANSWER, and that positive statement is what we key on.
#
# AND THE LOOP DOES NOT END. It used to `break` on the first "walk complete" and then
# sleep forever. ScreenScraper gains ids above the ceiling continuously, so completion
# is a fact about a moment; after it neither the walk nor the targeted fetch ever ran
# again until the container was restarted. Now completion only slows the cycle down.
#
# Every path is overridable so the loop can be exercised offline — see
# tests/test_prov_ssmirror_loop.py — with the production values as the defaults.
IDS=${SSMIRROR_IDS:-/data/ss-missing-ids.json}
MARK=${SSMIRROR_MARK:-/data/ss-missing-ids.done}
LOG=${SSMIRROR_LOG:-/data/ssmirror.log}
APP=${SSMIRROR_APP:-/app}
SLEEP=${SSMIRROR_SLEEP:-1800}
DONE_SLEEP=${SSMIRROR_DONE_SLEEP:-86400}   # exhausted: re-probe daily, not never
MAX_CYCLES=${SSMIRROR_MAX_CYCLES:-0}       # 0 = run forever, which is production

CYCLE=0
while true; do
  CYCLE=$((CYCLE + 1))

  # 1. Known-missing ids, once, before the walk can spend the day's quota.
  #    Only a run that reports "complete" retires them; a skip, a failure or a
  #    partially-deferred list all leave the marker unwritten and come round again.
  if [ -f "$IDS" ] && [ ! -f "$MARK" ]; then
    echo "=== targeted fetch of known-missing ids ===" >> "$LOG"
    OUT=$(python3 "$APP/ludodex/ss_mirror.py" --ids "$IDS" 2>&1)
    echo "$OUT" >> "$LOG"
    if echo "$OUT" | grep -q '"complete": *true'; then
      date -u +"%Y-%m-%dT%H:%M:%SZ" > "$MARK"
      echo "targeted fetch complete; marker written" >> "$LOG"
    else
      echo "targeted fetch did not complete — will retry next cycle" >> "$LOG"
    fi
  fi

  # 2. Walk to genuine exhaustion. No --until-id: the catalog decides where the end is.
  #    DEAD_RUN_STOP is 50,000, set from the largest measured hole (12,785) with margin.
  python3 "$APP/ludodex/ss_mirror.py" --walk >> "$LOG" 2>&1

  # Exhausted AND nothing owed. An id that never ANSWERED (timeout, 5xx, maintenance
  # page) is in the retry ledger, and a walk carrying that debt has not seen the whole
  # catalogue — so it keeps the short cycle until the ledger is empty.
  DONE=$(python3 -c "import sys; sys.path.insert(0, '$APP/ludodex'); import ss_mirror as M; s = M.status(); print(1 if (s['walk_complete'] and not s.get('gaps')) else 0)")
  if [ "$DONE" = "1" ]; then
    echo "WALK COMPLETE - catalog exhausted; re-probing in ${DONE_SLEEP}s" >> "$LOG"
    NAP=$DONE_SLEEP
  else
    NAP=$SLEEP
  fi

  if [ "$MAX_CYCLES" -gt 0 ] && [ "$CYCLE" -ge "$MAX_CYCLES" ]; then
    break
  fi
  sleep "$NAP"
done
