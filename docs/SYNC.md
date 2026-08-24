# The backing store — two-way sync of your durable data

Optional. `ludodex/dbsync.py` keeps the data **you authored** in an external database, in
both directions. SQLite stays the fast local working store; the remote backend is the
durable one. Backends: **PocketBase**, **Postgres**, **Supabase**, **MySQL** and
**Firebase Firestore**.

> **`sync.py` is gone.** Until 2026-07-21 this page documented a one-way "publish
> catalog" mirror (`sync.py`, `sync_target`, `--reconcile`). It was retired: nothing ever
> read what it published, and its name was persistently confused with the thing that
> actually protects your data. Its PocketBase/Firestore transport was extracted into
> `ludodex/remote_db.py`, which `dbsync.py` is built on. If you have `sync_target` set in
> an old config it no longer does anything — set `backingstore_backend` instead.

## What syncs, and what does not

Only **user-authored durable stores**. The catalog (`game-library.sqlite`) is a build
OUTPUT, regenerated locally from these, so it is never synced.

| store | local database | natural key |
|---|---|---|
| `user_tags` | `tags.sqlite` | `norm_key`, `tag` |
| `overrides` | `attr-overrides.sqlite` | `norm_key`, `kind` |
| `art_pins` | `pins.sqlite` | `norm_key`, `kind`, `provider`, `ref` |
| `framing` | `framing.sqlite` | `norm_key`, `kind` |
| `hero_pref` | `framing.sqlite` | `norm_key` |
| `manual_games` | `manual-games.sqlite` | `norm_key`, `source`, `platform` |
| `ownership` | `ownership.sqlite` | `norm_key`, `form`, `platform`, `state` |

Keys are natural and portable across machines — never an autoincrement id. Columns are
introspected from the local table, so a schema that gains a column syncs it without a
code change.

## Running it

Settings → **Database** drives all of this from the UI, and is the normal way to use it.
From a shell:

```bash
python3 ludodex/config.py set backingstore_backend pocketbase   # or postgres|supabase|mysql|firebase
python3 ludodex/dbsync.py pocketbase --dry-run   # reconcile and report, write nothing
python3 ludodex/dbsync.py pocketbase             # do it
python3 ludodex/dbsync.py                        # backend defaults to pocketbase
```

`scripts/update.sh` runs a sync after every rebuild whenever `backingstore_backend` is
set. The server can also run it on a timer: `backingstore_auto_minutes` (0 = manual
only), skipped while a sync is already in flight.

## Three-way merge, not an overwrite

Each sync compares three things: local, remote, and a per-record **shadow** hash kept
from the previous sync (in `sync_cache.sqlite`). That is what separates a genuine local
change from a genuine remote one, so adds, edits and deletes flow in **both** directions
without either side clobbering the other.

A record changed on both sides since the last sync is a conflict, resolved in this order:

1. last writer wins, by the record's own timestamp column (`updated`, `updated_at`,
   `modified`, `mtime`, `created`, `added` or `ts`, whichever the table has);
2. otherwise an **edit beats a delete** — never lose data silently;
3. otherwise local wins.

Everything is compared and hashed in its **string** form. Remote backends coerce numbers
to text, so a local float `created` and its stringified remote copy would otherwise hash
differently and re-sync forever. `None` and `''` both canonicalise to "missing".

## Rebuilding a machine from the remote

`restore_from_remote()` (Settings → Database → **Restore**, or
`POST /api/backingstore/restore`) pulls the durable stores back OUT of the backend and
overwrites the local copies. That is the point of a backing store: set the same backend
on a second machine, restore, and your tags, ownership, art pins and manual entries are
there. Pass `dry_run` first to see the counts.

## PocketBase

```bash
python3 ludodex/config.py set pocketbase_url         https://…
python3 ludodex/config.py set pocketbase_admin_email you@example.com
python3 ludodex/config.py set pocketbase_admin_password …   # or the POCKETBASE_PASSWORD env
```

Collections are created automatically, one per store. Upserts self-heal per record
(create ↔ patch), and the batch API is used when the server has it enabled, falling back
to parallel per-record writes.

## Postgres / Supabase / MySQL

One table per store. The merge engine is identical across every backend — the adapters
differ only in transport.

```bash
python3 ludodex/config.py set backingstore_backend postgres
python3 ludodex/config.py set postgres_url postgresql://user:pass@host:5432/ludodex
# ...or the discrete fields: postgres_host / postgres_port / postgres_db / postgres_user
```

Run `python3 ludodex/config.py list` to see every field a given backend reads.

## Firebase (Firestore)

One-time setup:

1. Create or pick a project at <https://console.firebase.google.com>.
2. **Build → Firestore Database → Create database**, in Native mode.
3. **Project settings → Service accounts → Generate new private key**, which downloads a
   JSON. (In Google Cloud instead: a service account with the *Cloud Datastore User*
   role.)
4. Put the JSON on the machine and point ludodex at it:

   ```bash
   python3 ludodex/config.py set firebase_sa_json <path>     # gitignored
   python3 ludodex/config.py set firebase_project_id <id>
   # optional:
   python3 ludodex/config.py set firebase_database <name>            # a non-default DB
   python3 ludodex/config.py set firebase_collection_prefix <prefix>
   ```

5. Firestore needs `google-auth`, which the Docker image already has. On a bare
   checkout:

   ```bash
   python3 -m pip install --user -r requirements.txt
   ```

## Not a backup

A backing store holds your live data, so a mistake propagates to it. For actual backups —
scheduled, retained, optionally encrypted snapshots — see the **Database** section in
Settings, which is a separate mechanism (`ludodex/backups.py`).
