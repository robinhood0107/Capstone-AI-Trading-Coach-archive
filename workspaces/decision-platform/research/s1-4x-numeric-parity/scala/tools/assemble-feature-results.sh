#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
CAPABILITY="${S1_4X_SCALA_CAPABILITY_RESULT:?set capability result path}"
SELECTED="${S1_4X_SCALA_SELECTED_PROFILE_RESULT:?set selected-profile result path}"
COMPILER="${S1_4X_SCALA_HARD_COMPILER_RESULT:?set hard compiler result path}"
SOURCE_POLICY="${S1_4X_SCALA_SOURCE_POLICY_RESULT:?set source-policy result path}"
DEPENDENCY="${S1_4X_SCALA_DEPENDENCY_AUDIT_RESULT:?set dependency audit result path}"
CORRECTNESS_ROOT="${S1_4X_SCALA_CORRECTNESS_ROOT:?set A/B/C correctness root}"
OUTPUT=""

usage() {
  printf 'usage: %s --output <new-absolute-json>\n' "$0" >&2
  exit 64
}

[[ "${1:-}" == "--output" && "$#" -eq 2 ]] || usage
OUTPUT="$2"
[[ "$OUTPUT" == /* && ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || usage

python3 "$SCALA_ROOT/tools/assemble_feature_results.py" \
  --planned "$S1_ROOT/contract/feature-decisions.v1.json" \
  --capability "$CAPABILITY" \
  --selected "$SELECTED" \
  --compiler "$COMPILER" \
  --source-policy "$SOURCE_POLICY" \
  --dependency-audit "$DEPENDENCY" \
  --lint-exceptions "$SCALA_ROOT/lint-exceptions.v1.json" \
  --correctness-root "$CORRECTNESS_ROOT" \
  --output "$OUTPUT"
