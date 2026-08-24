#!/usr/bin/env bash
# ludodex container entrypoint: prepare the data volume, then exec the server.
set -e

: "${LUDODEX_DATA:=/data}"
mkdir -p "$LUDODEX_DATA"

# --------------------------------------------------------------------------- #
#  Scratch (TMPDIR)
# --------------------------------------------------------------------------- #
# Keep ALL scratch on the app's own data volume, never the container overlay or a
# RAM-backed host /tmp. Point TMPDIR here so every tempfile / subprocess temp lands on
# the app's physical space, and CLEAN stale scratch on each start so the backend tidies
# up after itself.
#
# THAT CLEANUP IS `rm -rf`, SO THE PATH IS NOT TAKEN ON TRUST. LUDODEX_TMP is an
# environment variable anyone can set, and `LUDODEX_TMP=/data` used to mean the
# entrypoint emptied the entire data volume — every database, every token — at boot,
# silently, before the server even started. Two guards now stand in the way:
#
#   1. the path must not be /, the data dir, or an ANCESTOR of the data dir;
#   2. the wipe only ever touches a directory carrying our own marker file, so
#      pointing LUDODEX_TMP at somebody's existing directory cannot erase it.
#
# A rejected value is not fatal — it falls back to the default and says so.
_default_tmp="$LUDODEX_DATA/tmp"
_want_tmp="${LUDODEX_TMP:-$_default_tmp}"

_tmp_is_safe() {
    local d="$1" data="${LUDODEX_DATA%/}"
    case "$d" in ""|"/") return 1 ;; esac       # empty, or the filesystem root
    [ "${d%/}" = "$data" ] && return 1          # the data dir itself
    case "$data/" in "${d%/}/"*) return 1 ;; esac   # an ancestor of the data dir
    return 0
}

if ! _tmp_is_safe "$_want_tmp"; then
    echo "ludodex: REFUSING LUDODEX_TMP=$_want_tmp — it is (or contains) the data dir." >&2
    echo "ludodex: scratch is wiped on every start, so that would delete your data." >&2
    echo "ludodex: falling back to $_default_tmp" >&2
    _want_tmp="$_default_tmp"
fi

export LUDODEX_TMP="$_want_tmp"
export TMPDIR="$LUDODEX_TMP"
mkdir -p "$TMPDIR"

# Only wipe a directory we own. The marker is created when the directory is empty (or
# was just created by the mkdir above), so a first run adopts it and every run after
# that cleans it — but a directory with somebody else's files in it never gets one, and
# so is never cleared.
_marker="$TMPDIR/.ludodex-scratch"
if [ -e "$_marker" ]; then
    find "$TMPDIR" -mindepth 1 -not -name '.ludodex-scratch' -exec rm -rf {} + 2>/dev/null || true
elif [ -z "$(ls -A "$TMPDIR" 2>/dev/null)" ]; then
    : > "$_marker" 2>/dev/null || true
else
    echo "ludodex: $TMPDIR is not empty and is not ludodex scratch — leaving it alone." >&2
fi

# --------------------------------------------------------------------------- #
#  Media (the content-addressed art repo)
# --------------------------------------------------------------------------- #
# If a volume is mounted at /media, store bulk art there automatically — no extra
# variable to set. (Detect a real mount, not Debian's stock empty /media dir.)
# An explicit LUDODEX_MEDIA always wins.
#
# THE MOUNT MUST BE WRITABLE. This is the art REPO: ludodex materializes chosen art into
# it and sweeps its own leftovers out of it. A read-only bind — a ROM or media share
# mounted `:ro`, which the docs at one point suggested putting at /media — would be
# adopted here and then fail every write for the life of the container. Mount read-only
# shares somewhere else (/library is the compose file's example) and point a "Local"
# device at them instead.
if [ -z "${LUDODEX_MEDIA:-}" ] && grep -q ' /media ' /proc/mounts 2>/dev/null; then
    if [ -w /media ] && touch /media/.ludodex-write-test 2>/dev/null; then
        rm -f /media/.ludodex-write-test 2>/dev/null || true
        export LUDODEX_MEDIA=/media
    else
        echo "ludodex: /media is mounted READ-ONLY — not using it as the art repo." >&2
        echo "ludodex: art stays in \$LUDODEX_DATA/media. Mount read-only shares at" >&2
        echo "ludodex: /library (or any other path) and add them as a Local device." >&2
    fi
fi

# sweep leftover half-downloaded media (*.tmp) from a crashed materialize pass so the
# content-addressed art repo doesn't accumulate junk over time. Only ever runs against
# the art repo resolved above, which the check makes sure is ours to write to.
find "${LUDODEX_MEDIA:-$LUDODEX_DATA/media}" -maxdepth 1 -name '*.tmp' -type f -delete 2>/dev/null || true

# Seed config.sqlite + its default keys on first run (idempotent, non-fatal).
python /app/ludodex/config.py init >/dev/null 2>&1 || true

echo "ludodex → data: ${LUDODEX_DATA}   media: ${LUDODEX_MEDIA:-$LUDODEX_DATA/media}   tmp: ${TMPDIR}   port: 8001"
exec "$@"
