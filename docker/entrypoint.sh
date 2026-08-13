#!/usr/bin/env bash
# ludodex container entrypoint: prepare the data volume, then exec the server.
set -e

: "${LUDODEX_DATA:=/data}"
mkdir -p "$LUDODEX_DATA"

# Keep ALL scratch on the app's own data volume (the dedicated ludodex folder on your
# appdata nvme), never the container overlay or a RAM-backed host /tmp. Point TMPDIR here
# so every tempfile / subprocess temp lands on the app's physical space, and CLEAN stale
# scratch on each start so the backend tidies up after itself.
export LUDODEX_TMP="${LUDODEX_TMP:-$LUDODEX_DATA/tmp}"
export TMPDIR="$LUDODEX_TMP"
mkdir -p "$TMPDIR"
rm -rf "${TMPDIR:?}/"* 2>/dev/null || true

# If a volume is mounted at /media, store bulk art there automatically — no extra
# variable to set. (Detect a real mount, not Debian's stock empty /media dir.)
# An explicit LUDODEX_MEDIA always wins.
if [ -z "${LUDODEX_MEDIA:-}" ] && grep -q ' /media ' /proc/mounts 2>/dev/null; then
    export LUDODEX_MEDIA=/media
fi

# sweep leftover half-downloaded media (*.tmp) from a crashed materialize pass so the
# content-addressed art repo doesn't accumulate junk over time.
find "${LUDODEX_MEDIA:-$LUDODEX_DATA/media}" -maxdepth 1 -name '*.tmp' -type f -delete 2>/dev/null || true

# Seed config.sqlite + its default keys on first run (idempotent, non-fatal).
python /app/ludodex/config.py init >/dev/null 2>&1 || true

echo "ludodex → data: ${LUDODEX_DATA}   media: ${LUDODEX_MEDIA:-$LUDODEX_DATA/media}   tmp: ${TMPDIR}   port: 8001"
exec "$@"
