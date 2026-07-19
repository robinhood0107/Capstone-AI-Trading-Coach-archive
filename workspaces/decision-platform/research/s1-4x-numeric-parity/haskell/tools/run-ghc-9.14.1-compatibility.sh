#!/usr/bin/bash
set -euo pipefail

usage() {
  echo "usage: run-ghc-9.14.1-compatibility.sh --stack-yaml STACK_YAML --full-matrix --output-dir ABSOLUTE_NEW_DIRECTORY" >&2
  exit 64
}

[[ "$#" -eq 5 \
  && "$1" == "--stack-yaml" \
  && "$2" == /* \
  && "$3" == "--full-matrix" \
  && "$4" == "--output-dir" \
  && "$5" == /* ]] || usage

if [[ -v STACK_YAML || -v STACK_ROOT || -v STACK_OPTS || -v STACK_CONFIG ]]; then
  echo "ambient Stack configuration is forbidden" >&2
  exit 64
fi
if [[ -e "$5" || -L "$5" ]]; then
  echo "compatibility evidence output must be a new path" >&2
  exit 73
fi

SCRIPT_PATH="$(readlink -f "$0")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/..")"
source "$HASKELL_ROOT/tools/python-runtime.sh"
s1_4x_pin_benchmark_python
EXPECTED_STACK_YAML="$(realpath "$HASKELL_ROOT/stack-ghc-9.14.1.yaml")"
[[ "$(realpath "$2")" == "$EXPECTED_STACK_YAML" ]] || {
  echo "compatibility stack yaml must be the frozen tracked input" >&2
  exit 64
}
"$HASKELL_ROOT/tools/assert-toolchain.sh" >/dev/null

: "${S1_4X_CACHE_ROOT:?absolute existing cache root is required}"
[[ "$S1_4X_CACHE_ROOT" == /* \
  && -d "$S1_4X_CACHE_ROOT" \
  && ! -L "$S1_4X_CACHE_ROOT" \
  && "$(realpath "$S1_4X_CACHE_ROOT")" == "$S1_4X_CACHE_ROOT" ]] || {
  echo "S1_4X_CACHE_ROOT must be an absolute real directory" >&2
  exit 69
}
: "${S1_4X_GHCUP_BIN:?exact GHCup path is required}"
: "${S1_4X_STACK_BIN:?exact Stack path is required}"

OUTPUT_DIRECTORY="$5"
STACK_ROOT_PATH="$(
  "$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" "$HASKELL_ROOT/tools/profile_workflow.py" \
    isolated-stack-root \
    --cache-root "$S1_4X_CACHE_ROOT" \
    --purpose compatibility \
    --output "$OUTPUT_DIRECTORY"
)"
[[ "$STACK_ROOT_PATH" == "$S1_4X_CACHE_ROOT"/stack-root-compatibility-* ]] || {
  echo "compatibility Stack root derivation drift" >&2
  exit 73
}
[[ ! -e "$STACK_ROOT_PATH" && ! -L "$STACK_ROOT_PATH" ]] || {
  echo "compatibility Stack root must be new" >&2
  exit 73
}
mkdir -m 700 "$OUTPUT_DIRECTORY" "$STACK_ROOT_PATH"
STACK_WORK_DIR=".stack-work/s1-4x/${STACK_ROOT_PATH##*/}"

AUTHORITATIVE_BOOT_DUMP="$OUTPUT_DIRECTORY/authoritative-boot.dump"
COMPATIBILITY_BOOT_DUMP="$OUTPUT_DIRECTORY/compatibility-boot.dump"
"$S1_4X_GHCUP_BIN" --offline run --quick \
  --ghc 9.10.3 --stack 3.11.1 -- \
  ghc-pkg dump >"$AUTHORITATIVE_BOOT_DUMP"
"$S1_4X_GHCUP_BIN" --offline run --quick \
  --ghc 9.14.1 --stack 3.11.1 -- \
  ghc-pkg dump >"$COMPATIBILITY_BOOT_DUMP"

STDOUT_PATH="$OUTPUT_DIRECTORY/dependency.stdout"
STDERR_PATH="$OUTPUT_DIRECTORY/dependency.stderr"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%S.000000Z)"
set +e
"$S1_4X_GHCUP_BIN" --offline run --quick \
  --ghc 9.14.1 --stack 3.11.1 -- \
  "$S1_4X_STACK_BIN" \
  --stack-root "$STACK_ROOT_PATH" \
  --work-dir "$STACK_WORK_DIR" \
  --stack-yaml "$EXPECTED_STACK_YAML" \
  --no-terminal --color never \
  --system-ghc --no-install-ghc --hpack-force \
  build --dry-run --test --bench --no-run-tests --no-run-benchmarks \
  >"$STDOUT_PATH" 2>"$STDERR_PATH"
SOLVE_EXIT_CODE=$?
set -e
ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%S.000000Z)"
PANTRY_DB="$STACK_ROOT_PATH/pantry/pantry.sqlite3"

if [[ "$SOLVE_EXIT_CODE" -eq 0 ]]; then
  exec "$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" "$HASKELL_ROOT/tools/profile_workflow.py" \
    replay-compatibility-success \
    --stack-yaml "$EXPECTED_STACK_YAML" \
    --stack-root "$STACK_ROOT_PATH" \
    --stdout "$STDOUT_PATH" \
    --stderr "$STDERR_PATH" \
    --authoritative-boot-dump "$AUTHORITATIVE_BOOT_DUMP" \
    --compatibility-boot-dump "$COMPATIBILITY_BOOT_DUMP" \
    --pantry-db "$PANTRY_DB" \
    --started-at "$STARTED_AT" \
    --ended-at "$ENDED_AT" \
    --exit-code "$SOLVE_EXIT_CODE" \
    --output-dir "$OUTPUT_DIRECTORY"
fi
if [[ "$SOLVE_EXIT_CODE" -ne 1 ]]; then
  printf 'COMPATIBILITY_SOLVE_UNCLASSIFIED_EXIT:%s\n' "$SOLVE_EXIT_CODE" >&2
  exit 2
fi

exec "$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" "$HASKELL_ROOT/tools/profile_workflow.py" \
  capture-compatibility-failure \
  --stack-yaml "$EXPECTED_STACK_YAML" \
  --stack-root "$STACK_ROOT_PATH" \
  --stdout "$STDOUT_PATH" \
  --stderr "$STDERR_PATH" \
  --authoritative-boot-dump "$AUTHORITATIVE_BOOT_DUMP" \
  --compatibility-boot-dump "$COMPATIBILITY_BOOT_DUMP" \
  --pantry-db "$PANTRY_DB" \
  --started-at "$STARTED_AT" \
  --ended-at "$ENDED_AT" \
  --exit-code "$SOLVE_EXIT_CODE" \
  --output-dir "$OUTPUT_DIRECTORY"
