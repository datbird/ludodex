# Mirroring the catalog to a remote database

Optional. `sync.py` pushes the catalog (`games` + `sources`) one way to a remote backend
so other devices or apps can read it. Targets: **PocketBase** (self-hosted) or
**Firebase Firestore**, or both.

Set `sync_target` and `scripts/update.sh` pushes after every rebuild, or run it directly:

```bash
python3 sync.py both --dry-run     # show what would be pushed, write nothing
python3 sync.py pocketbase         # force one target
python3 sync.py --reconcile        # self-heal: ignore the cache, repair remote drift
python3 sync.py                    # use the configured sync_target
```

## Incremental and idempotent

Record ids are deterministic — a hash of the natural key — and a local content-hash
cache (`sync_cache.sqlite`) means each run pushes only what is new, changed or removed.
A no-op re-sync takes about a second.

Transient HTTP failures (429, 5xx, network) retry with exponential backoff.

`--reconcile` ignores the cache entirely, re-asserts every record, and prunes any remote
document with no local counterpart. Use it after losing the cache, after editing the
remote by hand, or after a run that failed partway.

## PocketBase

```bash
python3 ludodex/config.py set pocketbase_url         https://…
python3 ludodex/config.py set pocketbase_admin_email you@example.com
python3 ludodex/config.py set pocketbase_admin_password …   # or the POCKETBASE_PASSWORD env
```

The `games` and `sources` collections are created automatically. Upserts self-heal per
record (create ↔ patch), and the batch API is used when the server has it enabled,
falling back to parallel per-record writes.

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

5. Install the one extra dependency:

   ```bash
   python3 -m pip install --user -r requirements-firebase.txt
   ```

Collections `<prefix>games` and `<prefix>sources` are upserted by deterministic document
id (`norm_key` for games), and documents no longer present locally are pruned, so the
remote mirrors local rather than accumulating. `has_*` fields are stored as booleans and
counts as integers.

## Not a backup

This is a one-way read mirror for other apps. For actual backups — including the
databases that hold decisions rather than derivable data — see the **Database** section
in Settings, which does scheduled, retained, optionally encrypted snapshots.
