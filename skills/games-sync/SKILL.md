---
name: games-sync
description: Push the user's unified game catalog to a remote database (PocketBase and/or Firebase Firestore) so other devices or apps can read it. Use when the user asks to sync / publish / mirror / upload their game library to PocketBase, Firebase, or a remote DB, or to check/configure that sync.
---

# Sync the catalog to a remote DB

`sync.py` (in `~/game-ownership/`) mirrors the local catalog one-way to a remote
backend: the `games` and `sources` tables are pushed so the remote ends up matching
local exactly. Targets: **PocketBase** (self-hosted) and/or **Firebase Firestore**.

## Run it

```bash
cd ~/game-ownership
python3 sync.py both --dry-run    # preview counts, write nothing
python3 sync.py                   # use the configured sync_target
python3 sync.py pocketbase        # force one target (pocketbase|firebase|both)
python3 sync.py --reconcile       # self-heal: repair remote drift / lost cache
```

`scripts/update.sh` also runs the sync automatically after a rebuild whenever `sync_target`
is set, so a normal **games-update** already pushes to the remote.

**It's incremental + idempotent:** deterministic record ids + a local content-hash
cache (`sync_cache.sqlite`) mean each run pushes only new/changed/removed records (a
no-op re-sync is ~1s); transient errors retry with backoff. If the remote looks wrong
(drifted, edited by hand, or a prior run died mid-way), run `python3 sync.py --reconcile`
to re-assert every record and prune anything stale — it converges with no duplicates.

## Configure (all in the config/profile table — see `config.py list`)

- `sync_target` — `pocketbase` | `firebase` | `both` (blank = off)
- **PocketBase**: `pocketbase_url`, `pocketbase_admin_email`, and a password via
  `pocketbase_admin_password` (local, gitignored) or the `POCKETBASE_PASSWORD` env.
  Collections `games`/`sources` are auto-created;
  each run is a full replace.
- **Firebase**: `firebase_project_id` + `firebase_sa_json` (service-account key path),
  optional `firebase_database` (named DB) and `firebase_collection_prefix`. Requires
  `google-auth` (`python3 -m pip install --user -r requirements-firebase.txt`). Create a
  Firestore DB (Native mode) + a service account (role Cloud Datastore User) in the
  Firebase/GCP console — `./scripts/setup.sh` step 7 walks through it. Upserts by doc id and
  prunes removed docs.

Set values with `python3 config.py set <key> <value>`, or run `./scripts/setup.sh` (step 7).

## Notes
- Sync is **one-way** (local → remote); the local `game-library.sqlite` is the source
  of truth. Re-running is safe/idempotent.
- If a sync fails, check the URL/credentials first (`config.py list`), then re-run
  `python3 sync.py <target>` to see the error.
