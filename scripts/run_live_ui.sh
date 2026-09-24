#!/usr/bin/env bash
# Run tests/browser/ui_full.py inside a container that has Chromium (with CDP open) and
# Python Playwright, optionally on another machine over ssh.
#
# The password never appears on a command line: it is read here from the environment or
# a file, and travels to the container on STDIN as part of the script itself. Nothing is
# copied into the container except the screenshots the suite writes, and those are
# pulled back out and deleted when the run ends, pass or fail.
#
#   LUDODEX_URL=http://<host>:8001 LUDODEX_USER=<user> LUDODEX_PASS_FILE=<file> \
#   BROWSER_CONTAINER=<name> [BROWSER_SSH=<ssh destination>] \
#     scripts/run_live_ui.sh
#
# Environment:
#   LUDODEX_URL, LUDODEX_USER          required
#   LUDODEX_PASS or LUDODEX_PASS_FILE  one of them is required
#   BROWSER_CONTAINER                  required: the container running the browser
#   BROWSER_SSH                        optional: ssh destination that runs docker
#   BROWSER_PYTHON                     python with playwright in the container (python3)
#   LUDODEX_CDP                        CDP endpoint as seen from inside the container
#   LUDODEX_UI_WRITES=1                also round-trip reversible settings (admin login)
#   LUDODEX_UI_VIEWPORTS               desktop,phone (default both)
#   SHOTS_OUT                          where screenshots + report.json land locally
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
suite="$here/tests/browser/ui_full.py"

: "${LUDODEX_URL:?set LUDODEX_URL}"
: "${LUDODEX_USER:?set LUDODEX_USER}"
: "${BROWSER_CONTAINER:?set BROWSER_CONTAINER to the browser container name}"
if [[ -z "${LUDODEX_PASS:-}" && -z "${LUDODEX_PASS_FILE:-}" ]]; then
  echo "set LUDODEX_PASS or LUDODEX_PASS_FILE" >&2
  exit 2
fi
py="${BROWSER_PYTHON:-python3}"
out="${SHOTS_OUT:-${TMPDIR:-/tmp}/ludodex-ui-shots}"
remote_shots="/tmp/ludodex-ui-shots-$$"

# `dk ARGS` runs docker locally, or on BROWSER_SSH when set.
dk() {
  if [[ -n "${BROWSER_SSH:-}" ]]; then
    # ssh joins its arguments into one remote shell line, so quote each one for it
    ssh -o BatchMode=yes "$BROWSER_SSH" "docker $(printf '%q ' "$@")"
  else
    docker "$@"
  fi
}

cleanup() {
  mkdir -p "$out"
  # Pull the screenshots and report out, then remove them from the container.
  if dk exec "$BROWSER_CONTAINER" tar -C "$remote_shots" -cf - . | tar -C "$out" -xf -; then
    echo "screenshots and report.json in $out"
  else
    echo "could not copy screenshots out of the container" >&2
  fi
  dk exec "$BROWSER_CONTAINER" rm -rf "$remote_shots" || echo "cleanup of $remote_shots failed" >&2
}
trap cleanup EXIT

# The environment the suite needs goes in as the first line of the script on stdin, as
# JSON, so no secret is ever an argument to ssh, docker or python.
{
  LUDODEX_SHOTS="$remote_shots" python3 - <<'PY'
import json, os
env = {k: os.environ[k] for k in ("LUDODEX_URL", "LUDODEX_USER", "LUDODEX_CDP",
                                  "LUDODEX_UI_WRITES", "LUDODEX_UI_VIEWPORTS",
                                  "LUDODEX_SHOTS") if os.environ.get(k)}
pw = os.environ.get("LUDODEX_PASS")
if not pw:
    with open(os.environ["LUDODEX_PASS_FILE"]) as fh:
        pw = fh.read().strip()
env["LUDODEX_PASS"] = pw
env["LUDODEX_LIVE_TESTS"] = "1"
print("import os, json; os.environ.update(json.loads(%r))" % json.dumps(env))
PY
  cat "$suite"
} | dk exec -i "$BROWSER_CONTAINER" "$py" -u -
