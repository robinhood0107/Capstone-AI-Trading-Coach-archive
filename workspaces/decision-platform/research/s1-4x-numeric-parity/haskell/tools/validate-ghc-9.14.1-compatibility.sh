#!/usr/bin/bash
set -euo pipefail

if [[ "$#" -ne 1 || "$1" != /* ]]; then
  echo "usage: validate-ghc-9.14.1-compatibility.sh /ABSOLUTE/PATH/ghc-9.14.1-compatibility.v1.json" >&2
  exit 64
fi
RESULT_PATH="$1"
OUTPUT_DIRECTORY="${RESULT_PATH%/*}"
FAILURE_EVIDENCE="$OUTPUT_DIRECTORY/compatibility-failure.v1.json"
PASS_EVIDENCE="$OUTPUT_DIRECTORY/compatibility-pass.v1.json"
CANDIDATE_FAILURE_EVIDENCE="$OUTPUT_DIRECTORY/compatibility-candidate-failure.v1.json"
EVIDENCE_COUNT=0
for evidence in \
  "$FAILURE_EVIDENCE" \
  "$PASS_EVIDENCE" \
  "$CANDIDATE_FAILURE_EVIDENCE"; do
  if [[ -f "$evidence" && ! -L "$evidence" ]]; then
    EVIDENCE_COUNT=$((EVIDENCE_COUNT + 1))
  fi
done
[[ -f "$RESULT_PATH" && ! -L "$RESULT_PATH" \
  && "$EVIDENCE_COUNT" -eq 1 ]] || {
  echo "typed compatibility result and companion evidence are required" >&2
  exit 66
}

SCRIPT_PATH="$(readlink -f "$0")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/..")"
"$HASKELL_ROOT/tools/assert-toolchain.sh" >/dev/null

exec /usr/bin/python3 "$HASKELL_ROOT/tools/profile_workflow.py" \
  validate-compatibility \
  --result "$RESULT_PATH"
