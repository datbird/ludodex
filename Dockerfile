# syntax=docker/dockerfile:1

##############################################################################
# Stage 1 — build the React SPA (→ /web/dist, copied into the runtime image)
##############################################################################
FROM node:22-bookworm-slim AS web
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
FROM python:3.12-slim-bookworm AS runtime

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
#   cifs-utils, nfs-common, smbclient→ mount SMB / NFS shares
#   ca-certificates                  → TLS to providers
#   bash, findutils                  → scan scripts
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client rsync sshpass \
        p7zip-full zip unzip \
        cifs-utils nfs-common smbclient \
        ca-certificates bash findutils tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps (own layer — cached unless requirements change)
COPY requirements.txt ./
RUN pip install -r requirements.txt

# App source (see .dockerignore — no local data/secrets come along)
COPY . /app
# Built SPA from stage 1 (web/dist is .dockerignored, so this is the only copy)
COPY --from=web /web/dist /app/web/dist

# All durable state lives here; mount a named volume or host dir at /data.
VOLUME /data
EXPOSE 8001

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8001"]
