# Running ludodex in Docker

ludodex ships as a single self-contained image: the FastAPI server, the built
React UI, and every OS tool it shells out to (ssh/rsync for device sync, 7z/zip
for the file-ops engine, and cifs-utils/nfs-common for mounting shares). The
only thing you provide is a data volume and, optionally, some API keys.

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
  larger storage: set `LUDODEX_MEDIA=/media` and mount a second volume at
  `/media`. On Unraid, put `/data` on the SSD cache (appdata) and `/media` on the
  array. Leave it unset to keep everything in one volume.

  ```yaml
  environment:
    - LUDODEX_MEDIA=/media
  volumes:
    - ludodex-data:/data
    - <media-share>:/media
  ```
- **Secrets** come from `.env` (see `.env.example`) *or* the in-app Settings
  page (persisted to `/data/config.sqlite`). ludodex never reads an external
  secret store at runtime. `.env` is gitignored.
- **Device credentials** (SSH keys/passwords for your Steam Deck, NAS, etc.) are
  stored in `/data/connections.sqlite` — never in the image, never in git.

## Mounting NFS / SMB shares

Two ways to get a network share into ludodex; pick one:

1. **Host-mount (recommended, no special privileges).** Mount the share on the
   Docker host, bind-mount it into the container, then add a **Local** device in
   Connections pointing at that path:

   ```yaml
   volumes:
     - /mnt/media:/media:ro
   ```

2. **In-container mount.** ludodex includes `cifs-utils`/`nfs-common`, but
   mounting from inside a container needs elevated privileges. Uncomment the
   `cap_add` / `security_opt` / `devices` block in `docker-compose.yml`
   (`SYS_ADMIN`, `DAC_READ_SEARCH`, `/dev/fuse`). Prefer option 1 unless you
   specifically need the container to do the mounting.

## Updating

```bash
docker compose pull   # or: docker compose build --pull
docker compose up -d
```

Your `/data` volume carries the library and settings across the upgrade.
