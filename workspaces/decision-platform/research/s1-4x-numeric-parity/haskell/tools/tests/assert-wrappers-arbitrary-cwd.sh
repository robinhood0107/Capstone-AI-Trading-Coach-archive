#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/../..")"

: "${S1_4X_GHCUP_BIN:?S1_4X_GHCUP_BIN readiness path is required}"
: "${S1_4X_AUTHORITATIVE_GHC_BIN:?S1_4X_AUTHORITATIVE_GHC_BIN readiness path is required}"
: "${S1_4X_LATEST_GHC_BIN:?S1_4X_LATEST_GHC_BIN readiness path is required}"
: "${S1_4X_STACK_BIN:?S1_4X_STACK_BIN readiness path is required}"
: "${S1_4X_HLINT_BIN:?S1_4X_HLINT_BIN readiness path is required}"
: "${S1_4X_STYLISH_BIN:?S1_4X_STYLISH_BIN readiness path is required}"
EVIDENCE_ROOT="${S1_4X_EVIDENCE_ROOT:?S1_4X_EVIDENCE_ROOT cache path is required}"

if [[ "$#" -ne 2 || "$1" != "--output-root" || "$2" != /* || -e "$2" ]]; then
  echo "usage: assert-wrappers-arbitrary-cwd.sh --output-root ABSOLUTE_NEW_CACHE_DIRECTORY" >&2
  exit 64
fi
OUTPUT_ROOT="$2"
[[ "$EVIDENCE_ROOT" == /* && -d "$EVIDENCE_ROOT" && ! -L "$EVIDENCE_ROOT" ]] || {
  echo "S1_4X_EVIDENCE_ROOT must be an existing absolute non-symlink cache directory" >&2
  exit 64
}
case "$OUTPUT_ROOT" in
  "$EVIDENCE_ROOT"/*) ;;
  *)
    echo "wrapper evidence output must stay below S1_4X_EVIDENCE_ROOT" >&2
    exit 64
    ;;
esac

mkdir -m 700 "$OUTPUT_ROOT"
temporary_cwd="$(mktemp -d)"
trap 'rm -rf -- "$temporary_cwd"' EXIT

(
  cd "$temporary_cwd"
  "$HASKELL_ROOT/tools/assert-toolchain.sh" \
    >"$OUTPUT_ROOT/toolchain.stdout" \
    2>"$OUTPUT_ROOT/toolchain.stderr"
  "$HASKELL_ROOT/tools/check-format.sh" \
    --output-dir "$OUTPUT_ROOT/format" \
    >"$OUTPUT_ROOT/format.stdout" \
    2>"$OUTPUT_ROOT/format.stderr"
  "$HASKELL_ROOT/tools/check-hlint.sh" \
    --output-dir "$OUTPUT_ROOT/hlint" \
    >"$OUTPUT_ROOT/hlint.stdout" \
    2>"$OUTPUT_ROOT/hlint.stderr"
)

grep -F 'HASKELL_TOOLCHAIN_PASS' "$OUTPUT_ROOT/toolchain.stdout" >/dev/null
grep -F 'HASKELL_FORMAT_PASS' "$OUTPUT_ROOT/format.stdout" >/dev/null
grep -F 'HASKELL_HLINT_PASS' "$OUTPUT_ROOT/hlint.stdout" >/dev/null
[[ ! -s "$OUTPUT_ROOT/toolchain.stderr" ]] || {
  echo "toolchain wrapper wrote unexpected stderr" >&2
  exit 2
}
[[ ! -s "$OUTPUT_ROOT/format.stderr" ]] || {
  echo "format wrapper wrote unexpected stderr" >&2
  exit 2
}
[[ ! -s "$OUTPUT_ROOT/hlint.stderr" ]] || {
  echo "HLint wrapper wrote unexpected stderr" >&2
  exit 2
}

echo "HASKELL_ARBITRARY_CWD_WRAPPERS_PASS"
