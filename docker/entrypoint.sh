#!/usr/bin/env bash
# ludodex container entrypoint: prepare the data volume, then exec the server.
set -e

: "${LUDODEX_DATA:=/data}"
mkdir -p "$LUDODEX_DATA"

# Seed config.sqlite + its default keys on first run (idempotent, non-fatal).
python /app/config.py init >/dev/null 2>&1 || true

echo "ludodex → data: ${LUDODEX_DATA}   port: 8001"
exec "$@"
