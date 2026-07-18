#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 || "$1" != "--output-dir" || "$2" != /* ]]; then
  echo "usage: run-property-evidence.sh --output-dir ABSOLUTE_NEW_DIRECTORY" >&2
  exit 64
fi

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/..")"
NUMERIC_ROOT="$(realpath "${HASKELL_ROOT}/..")"
OUTPUT_DIRECTORY="$(realpath -m "$2")"
OUTPUT_PARENT="${OUTPUT_DIRECTORY%/*}"

if [[ -e "$OUTPUT_DIRECTORY" || -L "$OUTPUT_DIRECTORY" ]]; then
  echo "property evidence output already exists" >&2
  exit 73
fi
if [[ ! -d "$OUTPUT_PARENT" || -L "$OUTPUT_PARENT" ]]; then
  echo "property evidence output parent must be an existing real directory" >&2
  exit 73
fi

"$HASKELL_ROOT/tools/assert-toolchain.sh" >/dev/null
STACK_CONFIGURED="${S1_4X_STACK_BIN:?S1_4X_STACK_BIN readiness path is required}"
GHC_CONFIGURED="${S1_4X_AUTHORITATIVE_GHC_BIN:?S1_4X_AUTHORITATIVE_GHC_BIN readiness path is required}"
STACK_BIN="$(readlink -f "$STACK_CONFIGURED")"
GHC_BIN="$(readlink -f "$GHC_CONFIGURED")"

if [[ ! -x "$STACK_BIN" || ! -x "$GHC_BIN" ]]; then
  echo "required Haskell toolchain executable is missing" >&2
  exit 69
fi
if [[ "$("$STACK_BIN" --numeric-version)" != "3.11.1" ]]; then
  echo "Stack version mismatch" >&2
  exit 69
fi
if [[ "$("$GHC_BIN" --numeric-version)" != "9.10.3" ]]; then
  echo "GHC baseline version mismatch" >&2
  exit 69
fi

export PATH="${GHC_BIN%/*}:${STACK_BIN%/*}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
STACK_ARGUMENTS=(
  --system-ghc
  --no-install-ghc
  --stack-yaml "${HASKELL_ROOT}/stack.yaml"
)

"$STACK_BIN" "${STACK_ARGUMENTS[@]}" build --test --no-run-tests --no-terminal
DIST_DIRECTORY="$("$STACK_BIN" "${STACK_ARGUMENTS[@]}" path --dist-dir)"
TEST_RUNNER="${HASKELL_ROOT}/${DIST_DIRECTORY}/build/s1-4x-haskell-test/s1-4x-haskell-test"
if [[ ! -x "$TEST_RUNNER" ]]; then
  echo "compiled property test runner is missing" >&2
  exit 70
fi

mkdir -m 700 "$OUTPUT_DIRECTORY"
"$TEST_RUNNER" \
  --s1-4x-property-evidence \
  "$OUTPUT_DIRECTORY" \
  "$HASKELL_ROOT" \
  "${NUMERIC_ROOT}/contract/property-plan.v1.json" \
  "${NUMERIC_ROOT}/contract/fixtures/property/property-seeds.v1.json" \
  "${NUMERIC_ROOT}/contract/function-registry.v1.json" \
  "${NUMERIC_ROOT}/contract/error-registry.v1.json" \
  "$SCRIPT_PATH"
