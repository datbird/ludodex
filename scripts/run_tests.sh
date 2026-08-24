#!/usr/bin/env bash
# Run the offline test suite.
#
# WHERE THIS RUNS IS A SAFETY QUESTION, NOT A CONVENIENCE ONE.
#
# Most of these tests need fastapi and the provider drivers, which only exist in the
# image. The obvious move is `docker exec -i ludodex` — and that is what this runner
# used to tell you to do. `ludodex` is the RUNNING PRODUCTION CONTAINER. Its
# LUDODEX_DATA is /data, the real library, and a test that seeds fixtures there is one
# `DELETE FROM` away from the 2026-08-02 incident that erased a 66,280-row media index.
#
# So the default is a THROWAWAY container: the same image, `docker run --rm`, the
# checkout mounted at /src, and no data volume anywhere near it. Nothing it can do
# survives the run. Testing against the real data is still possible — sometimes you
# genuinely need to — but it is now a flag you type (--live-data), not the path of least
# resistance, and even then it is a throwaway container rather than the live one.
#
#   ./scripts/run_tests.sh                  throwaway container (DEFAULT, safe)
#   ./scripts/run_tests.sh --build          build the image first, then as above
#   ./scripts/run_tests.sh --local          this checkout, host python3 (needs the deps)
#   ./scripts/run_tests.sh --live-data      throwaway container + the REAL data volume
#   ./scripts/run_tests.sh --offline        as default, plus --network none
#   ./scripts/run_tests.sh test_foo test_ba run only the named tests
#
# LIVE GATES ARE RESET, ALWAYS. LUDODEX_LIVE_AI gates tests/test_vision_live.py, which
# makes real, billed model calls. It was never reset here, so an inherited shell
# variable — exported hours earlier for one deliberate check — turned a routine sweep
# into a spend. Every live gate is now forced off at the top of this file and can only
# be re-enabled by editing the command line, never by inheriting an environment.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --------------------------------------------------------------------------- #
#  Reset EVERY live gate. Inheriting any of these is how a sweep becomes an
#  incident (data) or an invoice (AI). Add new gates to this list, not elsewhere.
# --------------------------------------------------------------------------- #
export LUDODEX_LIVE_TESTS=0     # tests that mutate the running instance + backing store
export LUDODEX_LIVE_AI=0        # tests that make real, BILLED model calls
unset LUDODEX_LIVE_CONFIG       # points live tests at a real config.sqlite
unset LUDODEX_DATA              # each test gets its own scratch dir below

IMAGE="${LUDODEX_TEST_IMAGE:-ludodex:latest}"
VOLUME="${LUDODEX_DATA_VOLUME:-ludodex-data}"
MODE=throwaway
NETWORK=()
BUILD=0
ONLY=()

while [ $# -gt 0 ]; do
  case "$1" in
    --local)          MODE=local ;;
    --live-data)      MODE=live-data ;;
    --throwaway)      MODE=throwaway ;;
    --build)          BUILD=1 ;;
    --offline)        NETWORK=(--network none) ;;
    --image)          shift; IMAGE="$1" ;;
    --volume)         shift; VOLUME="$1" ;;
    -h|--help)        sed -n '2,29p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*)               echo "unknown flag: $1" >&2; exit 2 ;;
    *)                ONLY+=("$1") ;;
  esac
  shift
done

# --------------------------------------------------------------------------- #
#  --in-place: the actual loop. Everything above dispatches INTO this.
# --------------------------------------------------------------------------- #
run_suite() {
  local root="$1"; shift
  local tests="$root/tests"
  local pass=0 fail=0 skip=0
  local failed=()
  local list=()

  if [ $# -gt 0 ]; then
    for n in "$@"; do list+=("$tests/${n%.py}.py"); done
  else
    for t in "$tests"/test_*.py; do list+=("$t"); done
  fi

  for t in "${list[@]}"; do
    name="$(basename "$t" .py)"
    [ "$name" = "test_support" ] && continue
    if [ ! -f "$t" ]; then
      printf "  FAIL  %-34s no such test\n" "$name"; fail=$((fail+1)); failed+=("$name"); continue
    fi
    # Every test gets its OWN scratch data dir. test_support.assert_isolated() refuses to
    # run against a live one regardless — belt and braces, because the runner is the
    # thing that has to be trustworthy.
    scratch="$(mktemp -d "${TMPDIR:-/tmp}/ludodex-suite-XXXXXX")"
    out="$(cd "$root" && LUDODEX_DATA="$scratch" PYTHONDONTWRITEBYTECODE=1 \
           python3 "$t" 2>&1)"
    rc=$?
    rm -rf "$scratch"
    if [ $rc -ne 0 ]; then
      if printf '%s' "$out" | grep -q '^SKIPPED:'; then
        printf "  SKIP  %-34s %s\n" "$name" "$(printf '%s' "$out" | head -1 | cut -c10-70)"
        skip=$((skip+1)); continue
      fi
      printf "  FAIL  %-34s %s\n" "$name" "$(printf '%s' "$out" | tail -1 | cut -c1-80)"
      fail=$((fail+1)); failed+=("$name")
    else
      printf "  ok    %-34s %s\n" "$name" \
        "$(printf '%s' "$out" | grep -Eo 'RESULT: [^\n]*|[0-9]+ checks, all passed|[0-9]+/[0-9]+ passed|ALL PASS[^\n]*' | tail -1)"
      pass=$((pass+1))
    fi
  done

  echo
  echo "  $pass passed, $fail failed, $skip skipped"
  if [ $fail -gt 0 ]; then printf '  failing: %s\n' "${failed[*]}"; return 1; fi
  return 0
}

# The recursive call the container makes back into this same script.
if [ "${LUDODEX_TEST_INPLACE:-0}" = "1" ]; then
  run_suite "$ROOT" "${ONLY[@]+"${ONLY[@]}"}"
  exit $?
fi

echo "  live gates: LUDODEX_LIVE_TESTS=0  LUDODEX_LIVE_AI=0  (reset, not inherited)"

case "$MODE" in
  local)
    echo "  target: this checkout, host python3 (tests needing fastapi will report an error)"
    echo
    LUDODEX_TEST_INPLACE=1 run_suite "$ROOT" "${ONLY[@]+"${ONLY[@]}"}"
    exit $?
    ;;

  throwaway)
    command -v docker >/dev/null || {
      echo "docker not found. Use --local to run against this checkout instead." >&2
      exit 2; }
    if [ "$BUILD" = "1" ]; then
      echo "  building $IMAGE ..."
      docker build -t "$IMAGE" "$ROOT" || exit 1
    fi
    docker image inspect "$IMAGE" >/dev/null 2>&1 || {
      echo "image $IMAGE not found. Re-run with --build, or --local." >&2
      exit 2; }
    echo "  target: THROWAWAY container from $IMAGE (--rm, no data volume)"
    echo
    # The checkout is mounted read-only at /src and IS what gets tested; the image
    # supplies only the interpreter, the Python deps and the OS tools. Scratch goes to a
    # tmpfs inside the container, so nothing outlives the run.
    #
    # NOTE the deliberate absence of any `-v <volume>:/data`. There is no live data
    # mounted here, on purpose; --live-data is the flag that changes that.
    exec docker run --rm -i "${NETWORK[@]+"${NETWORK[@]}"}" \
      -v "$ROOT":/src:ro \
      --tmpfs /scratch:rw,exec \
      -e LUDODEX_TEST_INPLACE=1 \
      -e LUDODEX_LIVE_TESTS=0 -e LUDODEX_LIVE_AI=0 \
      -e TMPDIR=/scratch \
      ${LUDODEX_LIVE_DIRS+-e LUDODEX_LIVE_DIRS="$LUDODEX_LIVE_DIRS"} \
      --entrypoint bash "$IMAGE" /src/scripts/run_tests.sh "${ONLY[@]+"${ONLY[@]}"}"
    ;;

  live-data)
    # DELIBERATE OPT-IN. Still a throwaway container — but the REAL data volume is
    # mounted, so a test that ignores test_support.isolate() can reach the real library.
    # This exists because sometimes you genuinely need to check the deployed data. There
    # is no `docker exec` mode any more: poking the running production container was the
    # shape that made the 2026-08-02 incident possible, and nothing needs it.
    #
    # Take a backup first (Settings -> Database).
    command -v docker >/dev/null || { echo "docker not found." >&2; exit 2; }
    docker volume inspect "$VOLUME" >/dev/null 2>&1 || {
      echo "volume '$VOLUME' not found (override with --volume NAME)." >&2; exit 2; }
    docker image inspect "$IMAGE" >/dev/null 2>&1 || {
      echo "image $IMAGE not found. Re-run with --build." >&2; exit 2; }
    echo "  target: throwaway container with the REAL '$VOLUME' volume mounted at /data."
    echo "  assert_isolated() still refuses /data, but you are one bad test from the library."
    echo
    exec docker run --rm -i "${NETWORK[@]+"${NETWORK[@]}"}" \
      -v "$ROOT":/src:ro \
      -v "$VOLUME":/data \
      --tmpfs /scratch:rw,exec \
      -e LUDODEX_TEST_INPLACE=1 \
      -e LUDODEX_LIVE_TESTS=0 -e LUDODEX_LIVE_AI=0 \
      -e TMPDIR=/scratch \
      -e LUDODEX_LIVE_DIRS="/data${LUDODEX_LIVE_DIRS:+:$LUDODEX_LIVE_DIRS}" \
      --entrypoint bash "$IMAGE" /src/scripts/run_tests.sh "${ONLY[@]+"${ONLY[@]}"}"
    ;;

esac
