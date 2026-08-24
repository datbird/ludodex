# syntax=docker/dockerfile:1

##############################################################################
# Stage 1 — build the React SPA (→ /web/dist, copied into the runtime image)
##############################################################################
# Digest-pinned, not just tag-pinned: `node:22-bookworm-slim` is a moving target, so
# two builds a week apart could differ before a single line of ours changed. The tag is
# kept in the comment so a human can see what the digest IS.
# node:22-bookworm-slim as of 2026-08-05
FROM node@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS web
WORKDIR /web
RUN npm install -g pnpm@11.3.0
# install deps first (cached unless the lockfile changes)
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

##############################################################################
# Stage 2 — Python runtime
##############################################################################
# python:3.12-slim-bookworm as of 2026-08-13 (see the note on the web stage)
FROM python@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134 AS runtime

# LUDODEX_DATA = all durable state (SQLite DBs, config, tokens) — small, critical.
# Bulk media (content-addressed art) defaults to $LUDODEX_DATA/media; mount a
# volume at /media and the entrypoint auto-uses it (or set LUDODEX_MEDIA explicitly).
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LUDODEX_DATA=/data \
    HOME=/data \
    TZ=UTC

# OS tools the app shells out to:
#   openssh-client, rsync            → device sync (ssh / scp / rsync)
#   sshpass                          → password-auth SSH devices (e.g. Steam Deck)
#   p7zip-full, zip, unzip           → fileops archive engine
#   ca-certificates                  → TLS to providers
#
# NOT installed: cifs-utils, nfs-common, smbclient. They were here for an "in-container
# mount" that no code has ever performed — `fileops.py` raises on an SMB transport and
# `devices.test_connection` refuses one, so the packages could only ever be dead weight
# and a misleading signal (the docs grew a SYS_ADMIN + /dev/fuse recipe around them).
# Mount network shares on the HOST and bind-mount them in; see docs/DOCKER.md.
#   bash, findutils                  → scan scripts
#   ffmpeg (brings ffprobe)          → video frame sampling for the vision layer.
#                                      Without it PIL can't open a container, so every
#                                      video candidate is dropped before the model sees
#                                      it — media_video degrades explicitly rather than
#                                      scoring a trailer blind.
# mame-tools ships chdman and dolphin-emu ships dolphin-tool: the two converters the
# publish planner needs. Without them every disc-based and GameCube/Wii item plans as
# BLOCKED — correctly, but nothing can actually be published for those systems.
#
# The cost is lopsided and worth knowing: chdman is ~26 MB, dolphin-tool ~528 MB,
# because Debian's dolphin-emu drags 229 packages in even with --no-install-recommends.
# chdman is the one that matters most (PS1/PS2/Saturn/SegaCD all convert to CHD); if the
# image size ever becomes the problem, dolphin-emu is the line to cut, and RVZ
# conversion falls back to running on the target where RetroDECK already bundles it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client rsync sshpass \
        p7zip-full zip unzip \
        ca-certificates bash findutils tzdata \
        ffmpeg \
        mame-tools dolphin-emu \
    && rm -rf /var/lib/apt/lists/* \
    # Debian installs dolphin-tool into /usr/games, which is not on PATH — so
    # shutil.which() would report it missing and the planner would block every RVZ
    # item on a machine that has the tool sitting right there. Link it where the rest
    # of the toolchain lives rather than editing PATH, so a shell and a subprocess
    # agree about what exists.
    && ln -sf /usr/games/dolphin-tool /usr/local/bin/dolphin-tool

WORKDIR /app

# Python deps (own layer — cached unless requirements change).
# requirements.txt states the intent; requirements.lock pins the exact resolved versions
# so this image is reproducible. Without the constraints file every `>=` floor
# re-resolves at build time and two builds a week apart contain different libraries.
COPY requirements.txt requirements.lock ./
RUN pip install -r requirements.txt -c requirements.lock

# App source. .dockerignore keeps this to what the app NEEDS AT RUNTIME — no local data,
# no secrets, and no test/dev material: the 100-odd tests plus their image corpus, the
# ludodex/verify_*.py one-offs and the agent skills are all development-time things that
# have no business inside a shipped image.
COPY . /app
# Built SPA from stage 1 (web/dist is .dockerignored, so this is the only copy)
COPY --from=web /web/dist /app/web/dist

# All durable state lives here; mount a named volume or host dir at /data.
VOLUME /data
EXPOSE 8001

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8001"]
