#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
PROFILE_NAME="${1:-}"
case "$PROFILE_NAME" in
  baseline | A) PROFILE="A" ;;
  opt | B) PROFILE="B" ;;
  opt-own-source-inline | C) PROFILE="C" ;;
  *)
    printf 'usage: %s baseline|opt|opt-own-source-inline\n' "$0" >&2
    exit 64
    ;;
esac

RESULT_DIR="${RESULT_DIR:?set RESULT_DIR to an absolute correctness result directory}"
[[ "$RESULT_DIR" == /* ]] || {
  printf 'RESULT_DIR must be absolute\n' >&2
  exit 64
}
PROFILE_DIR="$RESULT_DIR/scala/profiles/$PROFILE"
[[ ! -e "$PROFILE_DIR" ]] || {
  printf 'profile output already exists: %s\n' "$PROFILE_DIR" >&2
  exit 1
}
mkdir -p "$PROFILE_DIR"

"$SCALA_ROOT/tools/build-candidate.sh" \
  --profile "$PROFILE" \
  --output "$PROFILE_DIR/candidate.jar"
export S1_4X_SCALA_CANDIDATE_JAR="$PROFILE_DIR/candidate.jar"
export S1_4X_SCALA_CANDIDATE_SHA256
S1_4X_SCALA_CANDIDATE_SHA256="$(sha256sum "$S1_4X_SCALA_CANDIDATE_JAR" | awk '{print $1}')"

"$SCALA_ROOT/tools/run-candidate.sh" run \
  --request "$S1_ROOT/contract/fixtures/small/canonical-inputs.v1.json" \
  --fixture-root "$S1_ROOT/contract/fixtures" \
  --output "$PROFILE_DIR/canonical-results.json"
"$SCALA_ROOT/tools/run-candidate.sh" run \
  --request "$S1_ROOT/contract/fixtures/invalid/semantic-errors.v1.json" \
  --fixture-root "$S1_ROOT/contract/fixtures" \
  --output "$PROFILE_DIR/semantic-errors.json"

"$SCALA_ROOT/tools/run-property-evidence.sh" \
  --profile "$PROFILE" \
  --output-dir "$PROFILE_DIR/property"

ORACLE="$S1_ROOT/oracle"
CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
export UV_CACHE_DIR="$CACHE_ROOT/uv"
export UV_PROJECT_ENVIRONMENT="$CACHE_ROOT/oracle-venv"
uv run --project "$ORACLE" --frozen python "$ORACLE/compare_results.py" \
  --expected "$S1_ROOT/contract/fixtures/expected/canonical-results.v1.json" \
  --actual "$PROFILE_DIR/canonical-results.json" \
  >"$PROFILE_DIR/canonical-comparison.json"
uv run --project "$ORACLE" --frozen python "$ORACLE/compare_results.py" \
  --expected "$S1_ROOT/contract/fixtures/invalid/semantic-errors.expected.v1.json" \
  --actual "$PROFILE_DIR/semantic-errors.json" \
  >"$PROFILE_DIR/semantic-comparison.json"

jq -e '.status == "PASS" and .mismatchCount == 0' \
  "$PROFILE_DIR/canonical-comparison.json" >/dev/null
jq -e '.status == "PASS" and .mismatchCount == 0' \
  "$PROFILE_DIR/semantic-comparison.json" >/dev/null

printf 'SCALA_CORRECTNESS_PROFILE_PASS profile=%s candidateSha256=%s\n' \
  "$PROFILE" "$S1_4X_SCALA_CANDIDATE_SHA256"
