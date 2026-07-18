#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/../..")"

# accepted failure test도 caller가 전달한 readiness packet path만 상속한다.
: "${S1_4X_GHCUP_BIN:?S1_4X_GHCUP_BIN readiness path is required}"
: "${S1_4X_AUTHORITATIVE_GHC_BIN:?S1_4X_AUTHORITATIVE_GHC_BIN readiness path is required}"
: "${S1_4X_LATEST_GHC_BIN:?S1_4X_LATEST_GHC_BIN readiness path is required}"
: "${S1_4X_STACK_BIN:?S1_4X_STACK_BIN readiness path is required}"
: "${S1_4X_HLINT_BIN:?S1_4X_HLINT_BIN readiness path is required}"
: "${S1_4X_STYLISH_BIN:?S1_4X_STYLISH_BIN readiness path is required}"

temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT

"$HASKELL_ROOT/tools/assert-toolchain.sh" \
  >"$temporary/default.stdout" \
  2>"$temporary/default.stderr"
grep -F \
  'compatibilityStatus=FAIL_FROZEN_DEPENDENCY acceptedMode=true' \
  "$temporary/default.stdout" >/dev/null
[[ ! -s "$temporary/default.stderr" ]] || {
  echo "accepted frozen-dependency assertion wrote unexpected stderr" >&2
  exit 2
}

set +e
"$HASKELL_ROOT/tools/assert-toolchain.sh" --allow-pending-compatibility \
  >"$temporary/legacy.stdout" \
  2>"$temporary/legacy.stderr"
legacy_exit=$?
set -e
[[ "$legacy_exit" -eq 64 ]] || {
  printf 'stale pending bypass exit drift: %s\n' "$legacy_exit" >&2
  exit 2
}
grep -F 'usage: assert-toolchain.sh' "$temporary/legacy.stderr" >/dev/null
[[ ! -s "$temporary/legacy.stdout" ]] || {
  echo "stale pending bypass wrote unexpected stdout" >&2
  exit 2
}

echo "HASKELL_ACCEPTED_COMPATIBILITY_TOOLCHAIN_TEST_PASS"
