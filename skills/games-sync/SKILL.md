---
name: games-sync
description: Sync the user's durable ludodex data (tags, ownership, art pins, manual entries, overrides) two ways with an external database — PocketBase, Postgres, Supabase, MySQL or Firebase Firestore. Use when the user asks to sync / back / mirror their game library to a remote DB, to move it to another machine, or to check or configure that sync.
---

# The backing store — two-way sync

`ludodex/dbsync.py` reconciles the data the user AUTHORED with an external database, in
both directions. SQLite stays the fast local working store; the backend holds the durable
truth. Backends: **PocketBase**, **Postgres**, **Supabase**, **MySQL**, **Firebase
Firestore**.

Paths below are relative to a ludodex checkout — set `LUDODEX` to wherever it lives, or
`cd` there first.

> The one-way `sync.py` catalog mirror this skill used to describe was **retired
> 2026-07-21** and deleted. Nothing read what it published. If the user has
> `sync_target` set in an old config, it does nothing; `backingstore_backend` is the
> live setting.

## What syncs

Only user-authored durable stores: `user_tags`, `overrides`, `art_pins`, `framing`,
`hero_pref`, `manual_games`, `ownership`. The catalog (`game-library.sqlite`) is a build
OUTPUT, rebuilt locally from these, so it is never synced — that is why "sync" is not a
backup of the catalog and does not need to be.

## Run it

Settings → **Database** in the web UI is the normal way. From a shell:

```bash
cd "$LUDODEX"
python3 ludodex/dbsync.py pocketbase --dry-run   # reconcile and report, write nothing
python3 ludodex/dbsync.py pocketbase             # do it
python3 ludodex/dbsync.py                        # backend defaults to pocketbase
```

`scripts/update.sh` runs a sync after each rebuild whenever `backingstore_backend` is
set, so a normal **games-update** already syncs. The server can also run it on a timer —
`backingstore_auto_minutes` (0 = manual only).

There is no `--reconcile` flag; reconciliation is what every run does. The only flag is
`--dry-run`.

## It is a three-way merge, not a push

Each run compares local, remote, and a per-record **shadow** hash from the previous sync
(kept in `sync_cache.sqlite`). That separates a genuine local change from a genuine
remote one, so adds, edits and deletes flow **both** ways without either side clobbering
the other. A record changed on both sides resolves last-writer-wins by the record's
timestamp column; failing that an **edit beats a delete** (never lose data silently);
failing that local wins.

To rebuild a machine from the remote, use Settings → Database → **Restore**
(`POST /api/backingstore/restore`), which pulls the stores back out and overwrites the
local copies. Preview it with `dry_run` first.

## Configure (all config keys — see `python3 ludodex/config.py list`)

- `backingstore_backend` — `pocketbase` | `postgres` | `supabase` | `mysql` | `firebase`
  (blank = off)
- `backingstore_auto_minutes` — auto-sync every N minutes (0 = off)
- **PocketBase**: `pocketbase_url`, `pocketbase_admin_email`, and a password via
  `pocketbase_admin_password` (local, gitignored) or the `POCKETBASE_PASSWORD` env.
  Collections are auto-created, one per store.
- **Postgres / Supabase**: `postgres_url` (or the discrete `postgres_host`,
  `postgres_port`, `postgres_db`, `postgres_user`, `postgres_password`); Supabase uses
  `supabase_url`. One table per store. Needs `psycopg[binary]`, which is in
  `requirements.txt`.
- **MySQL / MariaDB**: `mysql_host`, `mysql_port`, `mysql_db`, `mysql_user`,
  `mysql_password`. Needs `PyMySQL`, also in `requirements.txt`.
- **Firebase**: `firebase_project_id` + `firebase_sa_json` (service-account key path),
  optional `firebase_database` (named DB) and `firebase_collection_prefix`. Needs
  `google-auth` (in `requirements.txt`). Create a Firestore DB in Native mode plus a
  service account with the *Cloud Datastore User* role in the Firebase/GCP console;
  `bash scripts/setup.sh` step 8 walks through it.

Set values with `python3 ludodex/config.py set <key> <value>`, or run
`bash scripts/setup.sh` (step 8).

## Notes
- A backing store holds LIVE data, so a local mistake propagates to it. For real
  backups — scheduled, retained, optionally encrypted snapshots — that is the
  **Database** section in Settings (`ludodex/backups.py`), a separate mechanism.
- If a sync fails, check the URL/credentials first (`python3 ludodex/config.py list`),
  then re-run `python3 ludodex/dbsync.py <backend>` to see the error.
- Full detail: `docs/SYNC.md`.
