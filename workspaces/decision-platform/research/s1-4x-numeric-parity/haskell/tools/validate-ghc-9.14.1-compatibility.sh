#!/usr/bin/bash
set -euo pipefail

if [[ "$#" -ne 1 || "$1" != /* ]]; then
  echo "usage: validate-ghc-9.14.1-compatibility.sh /ABSOLUTE/PATH/ghc-9.14.1-compatibility.v1.json" >&2
  exit 64
fi
RESULT_PATH="$1"
EVIDENCE_PATH="${RESULT_PATH%/*}/compatibility-failure.v1.json"
[[ -f "$RESULT_PATH" && ! -L "$RESULT_PATH" \
  && -f "$EVIDENCE_PATH" && ! -L "$EVIDENCE_PATH" ]] || {
  echo "typed compatibility result and companion evidence are required" >&2
  exit 66
}

SCRIPT_PATH="$(readlink -f "$0")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/..")"
"$HASKELL_ROOT/tools/assert-toolchain.sh" >/dev/null

exec /usr/bin/python3 "$HASKELL_ROOT/tools/profile_workflow.py" \
  validate-compatibility \
  --result "$RESULT_PATH"
