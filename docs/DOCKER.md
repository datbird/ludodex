# Running ludodex in Docker

ludodex ships as a single self-contained image: the FastAPI server, the built
React UI, and every OS tool it shells out to (ssh/rsync for device sync, 7z/zip
for the file-ops engine, ffmpeg for video, chdman/dolphin-tool for publishing).
The only thing you provide is a data volume and, optionally, some API keys.

## Quick start

```bash
cp .env.example .env        # fill in the keys you use (all optional)
docker compose up -d        # builds the image and starts on :8001
```

Open <http://localhost:8001>. First run creates a fresh library — add your
devices and providers from **Settings**.

To build the image by hand instead of via compose:

```bash
docker build -t ludodex:latest .
docker run -d --name ludodex -p 8001:8001 \
  -v ludodex-data:/data --env-file .env ludodex:latest
```

## Data & configuration

- **All durable state lives in `/data`** (mount a named volume or host dir there):
  the config, device connections, tokens, the catalog, and caches. These are
  small and critical (not regenerable) — this is the volume to back up. Nothing
  durable is written into the image, so upgrades are just a re-pull.
- **Media (downloaded art) defaults to `/data/media`.** It's bulk and
  regenerable (re-fetchable from providers), so you can keep it on separate,
  larger storage: just **mount a volume at `/media`** and ludodex uses it
  automatically — no env var to set. Put `/data` on fast storage and `/media` on
  bulk storage. Leave `/media` unmounted to keep everything in one volume.

  ```yaml
  volumes:
    - ludodex-data:/data
    - /srv/ludodex-media:/media   # auto-detected; art lands here — must be WRITABLE
  ```

  > **`/media` is the art REPO, not a place to expose your ROM share.** ludodex
  > *writes* into it — it materializes chosen art there and sweeps its own
  > half-finished downloads out of it. Mounting a read-only share at `/media`
  > used to hand the art repo a filesystem it could never write to; the
  > entrypoint now detects that, refuses the mount, says so on stdout and falls
  > back to `/data/media`. Your ROM/media share goes somewhere else — see
  > *Using a network share* below.

  (To point media somewhere else, set `LUDODEX_MEDIA=/your/path` explicitly — an
  explicit value always wins over the `/media` auto-detect.)
- **Scratch defaults to `/data/tmp`** and is emptied on every start, so a crashed
  run cannot leave temp files accumulating. `LUDODEX_TMP` moves it. That
  directory is deleted at boot, so the entrypoint refuses a value that is the
  data dir or a parent of it (`LUDODEX_TMP=/data` would otherwise have emptied
  the whole volume), and it only ever clears a directory carrying its own
  `.ludodex-scratch` marker — point it at a directory that already has files in
  it and ludodex leaves them alone.
- **Secrets** come from `.env` (see `.env.example`) *or* the in-app Settings
  page (persisted to `/data/config.sqlite`). ludodex never reads an external
  secret store at runtime. `.env` is gitignored.
- **Device credentials** (SSH keys/passwords for your Steam Deck, NAS, etc.) are
  stored in `/data/connections.sqlite` — never in the image, never in git.

## Using a network share (NFS / SMB)

**Mount it on the host, bind it into the container.** That is the only supported
way, and it needs no special privileges:

```yaml
volumes:
  - /srv/roms:/library:ro     # any path EXCEPT /media
```

Then add a **Local** device in Connections pointing at `/library`.

Two details that bite:

- **Not at `/media`.** That path is claimed by the art repo (above), and ludodex
  writes there.
- **`:ro` is fine here** — nothing writes to a share you add as a Local device
  unless you ask it to publish.

> Earlier versions of this page described a second option: granting `SYS_ADMIN`,
> `DAC_READ_SEARCH` and `/dev/fuse` so the container could mount SMB/NFS itself.
> **No such code path has ever existed.** `fileops.py` raises on an SMB transport
> and `devices.test_connection` refuses one, so the capabilities bought nothing
> and the `cifs-utils`/`nfs-common`/`smbclient` packages behind them have been
> removed from the image. If you granted those capabilities, take them back.

## Updating

```bash
docker compose pull   # or: docker compose build --pull
docker compose up -d
```

Your `/data` volume carries the library and settings across the upgrade.
