#!/usr/bin/env bash
# Run the offline test suite.
#
# Most of these tests need fastapi and the provider drivers, which only exist in the
# image — so the natural place to run them is inside the running container. That is
# exactly what made 2026-08-02 expensive: a test that used `os.environ.setdefault` for
# LUDODEX_DATA inherited the container's /data and erased the live media index.
#
# So this runner does not trust the tests. It gives each one its own scratch
# LUDODEX_DATA, and test_support.assert_isolated() refuses to run against a live dir
# regardless. Live tests (which mutate the running instance and the real backing store
# on purpose) are skipped unless LUDODEX_LIVE_TESTS=1.
#
#   in-container:  docker exec -i ludodex bash /app/run_tests.sh
#   on a checkout: ./run_tests.sh          (tests needing fastapi will report an error)
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${LUDODEX_LIVE_TESTS:=0}"
export LUDODEX_LIVE_TESTS

pass=0; fail=0; skip=0
failed=()

for t in "$DIR"/test_*.py; do
  name="$(basename "$t" .py)"
  [ "$name" = "test_support" ] && continue
  scratch="$(mktemp -d "${TMPDIR:-/tmp}/ludodex-suite-XXXXXX")"
  out="$(LUDODEX_DATA="$scratch" python3 "$t" 2>&1)"
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
      "$(printf '%s' "$out" | grep -Eo '[0-9]+/[0-9]+ passed|ALL PASS[^\n]*|RESULT: [^\n]*' | tail -1)"
    pass=$((pass+1))
  fi
done

echo
echo "  $pass passed, $fail failed, $skip skipped"
[ $fail -gt 0 ] && { printf '  failing: %s\n' "${failed[*]}"; exit 1; }
exit 0
