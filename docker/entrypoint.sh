#!/usr/bin/env bash
# ludodex container entrypoint: prepare the data volume, then exec the server.
set -e

: "${LUDODEX_DATA:=/data}"
mkdir -p "$LUDODEX_DATA"

# If a volume is mounted at /media, store bulk art there automatically — no extra
# variable to set. (Detect a real mount, not Debian's stock empty /media dir.)
# An explicit LUDODEX_MEDIA always wins.
if [ -z "${LUDODEX_MEDIA:-}" ] && grep -q ' /media ' /proc/mounts 2>/dev/null; then
    export LUDODEX_MEDIA=/media
fi

# Seed config.sqlite + its default keys on first run (idempotent, non-fatal).
python /app/config.py init >/dev/null 2>&1 || true

echo "ludodex → data: ${LUDODEX_DATA}   media: ${LUDODEX_MEDIA:-$LUDODEX_DATA/media}   port: 8001"
exec "$@"
