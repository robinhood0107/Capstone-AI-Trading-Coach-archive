#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/../..")"

temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT

set +e
"$HASKELL_ROOT/tools/assert-toolchain.sh" \
  >"$temporary/default.stdout" \
  2>"$temporary/default.stderr"
default_exit=$?
set -e
[[ "$default_exit" -eq 78 ]] || {
  printf 'provisional accepted-mode exit drift: %s\n' "$default_exit" >&2
  exit 2
}
grep -Fx \
  'HASKELL_TOOLCHAIN_PENDING: GHC 9.14 compatibility solve is not acceptance-eligible' \
  "$temporary/default.stderr" >/dev/null
[[ ! -s "$temporary/default.stdout" ]] || {
  echo "provisional accepted mode wrote unexpected stdout" >&2
  exit 2
}

"$HASKELL_ROOT/tools/assert-toolchain.sh" --allow-pending-compatibility \
  >"$temporary/scaffold.stdout" \
  2>"$temporary/scaffold.stderr"
grep -F 'compatibilityStatus=PENDING_SOLVE acceptedMode=false' \
  "$temporary/scaffold.stdout" >/dev/null
[[ ! -s "$temporary/scaffold.stderr" ]] || {
  echo "provisional scaffold assertion wrote unexpected stderr" >&2
  exit 2
}

echo "HASKELL_PROVISIONAL_TOOLCHAIN_TEST_PASS"
