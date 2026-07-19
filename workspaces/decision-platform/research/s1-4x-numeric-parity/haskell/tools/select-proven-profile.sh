#!/usr/bin/bash
set -euo pipefail

usage() {
  echo "usage: select-proven-profile.sh --materialize|--check" >&2
  exit 64
}

[[ "$#" -eq 1 ]] || usage
case "$1" in
  --materialize) MODE="materialize" ;;
  --check) MODE="check" ;;
  *) usage ;;
esac

: "${S1_4X_HASKELL_BASELINE_CORRECTNESS:?absolute baseline correctness receipt is required}"
: "${S1_4X_HASKELL_OPTIMIZED_CORRECTNESS:?absolute optimized correctness receipt is required}"
: "${S1_4X_HASKELL_QUALIFICATION_ARTIFACT:?absolute qualification artifact is required}"
for evidence_path in \
  "$S1_4X_HASKELL_BASELINE_CORRECTNESS" \
  "$S1_4X_HASKELL_OPTIMIZED_CORRECTNESS" \
  "$S1_4X_HASKELL_QUALIFICATION_ARTIFACT"; do
  [[ "$evidence_path" == /* && -f "$evidence_path" && ! -L "$evidence_path" ]] || {
    echo "profile selector evidence must be an absolute regular non-symlink" >&2
    exit 66
  }
done

if [[ -v STACK_YAML || -v STACK_ROOT || -v STACK_OPTS || -v STACK_CONFIG ]]; then
  echo "ambient Stack configuration is forbidden" >&2
  exit 64
fi

SCRIPT_PATH="$(readlink -f "$0")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/..")"
source "$HASKELL_ROOT/tools/python-runtime.sh"
s1_4x_pin_benchmark_python
"$HASKELL_ROOT/tools/assert-toolchain.sh" >/dev/null

s1_4x_exec_benchmark_python "$HASKELL_ROOT/tools/profile_workflow.py" select-profile \
  --mode "$MODE"
