#!/usr/bin/env bash
set -euo pipefail

# 이 wrapper는 frozen oracle과 두 candidate를 같은 absolute fixture root에 묶는다.
ROOT="$(git rev-parse --show-toplevel)"
S1_4X="$ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity"
ORACLE="$S1_4X/oracle"
INTEGRATION="$S1_4X/integration"
CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
OUTPUT_DIRECTORY="${1:?usage: run-integration-correctness.sh ABSOLUTE_OUTPUT_DIRECTORY}"

case "$OUTPUT_DIRECTORY" in
  /*) ;;
  *) echo "output directory must be absolute" >&2; exit 64 ;;
esac
test ! -e "$OUTPUT_DIRECTORY" || {
  echo "output directory already exists: $OUTPUT_DIRECTORY" >&2
  exit 73
}
mkdir -p "$OUTPUT_DIRECTORY"

UV_BIN="${S1_4X_UV_BIN:?set the verified absolute uv executable path}"
SCALA_RUNNER="$S1_4X/scala/tools/run-candidate.sh"
HASKELL_RUNNER="$INTEGRATION/tools/run-haskell-candidate.sh"
for required in \
  "$UV_BIN" \
  "$SCALA_RUNNER" \
  "$HASKELL_RUNNER"; do
  test -x "$required" || {
    echo "required integration executable is unavailable: $required" >&2
    exit 69
  }
done
for required_source in \
  "$ORACLE/capture_reference_results.py" \
  "$ORACLE/compare_results.py"; do
  test -f "$required_source" || {
    echo "required integration source is unavailable: $required_source" >&2
    exit 69
  }
done

mkdir -p "$CACHE_ROOT/tmp" "$CACHE_ROOT/uv" "$CACHE_ROOT/oracle-capture"
export TMPDIR="$CACHE_ROOT/tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export UV_CACHE_DIR="$CACHE_ROOT/uv"
export UV_PYTHON=3.12.13

"$UV_BIN" lock --project "$ORACLE" --check
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$INTEGRATION/run_full_correctness.py" \
  --request "$S1_4X/contract/fixtures/small/canonical-inputs.v1.json" \
  --fixture-root "$S1_4X/contract/fixtures" \
  --expected "$S1_4X/contract/fixtures/expected/canonical-results.v1.json" \
  --output-directory "$OUTPUT_DIRECTORY/canonical" \
  --scala-runner "$SCALA_RUNNER" \
  --haskell-runner "$HASKELL_RUNNER" \
  --capture-script "$ORACLE/capture_reference_results.py" \
  --comparator "$ORACLE/compare_results.py" \
  --production-project "$ROOT/workspaces/decision-platform/python-services" \
  --research-project "$ROOT/workspaces/decision-platform/research/s1-4r-jax-risk" \
  --uv-executable "$UV_BIN" \
  --scratch-root "$CACHE_ROOT/oracle-capture/canonical"

"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$INTEGRATION/run_full_correctness.py" \
  --request "$S1_4X/contract/fixtures/invalid/semantic-errors.v1.json" \
  --fixture-root "$S1_4X/contract/fixtures" \
  --expected "$S1_4X/contract/fixtures/invalid/semantic-errors.expected.v1.json" \
  --output-directory "$OUTPUT_DIRECTORY/semantic" \
  --scala-runner "$SCALA_RUNNER" \
  --haskell-runner "$HASKELL_RUNNER" \
  --capture-script "$ORACLE/capture_reference_results.py" \
  --comparator "$ORACLE/compare_results.py" \
  --production-project "$ROOT/workspaces/decision-platform/python-services" \
  --research-project "$ROOT/workspaces/decision-platform/research/s1-4r-jax-risk" \
  --uv-executable "$UV_BIN" \
  --scratch-root "$CACHE_ROOT/oracle-capture/semantic"

"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$INTEGRATION/replay_transport_contract.py" \
  --candidate "$SCALA_RUNNER" \
  --fixture-root "$S1_4X/contract/fixtures" \
  --invalid-root "$S1_4X/contract/fixtures/invalid" \
  --output-directory "$OUTPUT_DIRECTORY/scala-transport" \
  --report "$OUTPUT_DIRECTORY/scala-transport-report.json"
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$INTEGRATION/replay_transport_contract.py" \
  --candidate "$HASKELL_RUNNER" \
  --fixture-root "$S1_4X/contract/fixtures" \
  --invalid-root "$S1_4X/contract/fixtures/invalid" \
  --output-directory "$OUTPUT_DIRECTORY/haskell-transport" \
  --report "$OUTPUT_DIRECTORY/haskell-transport-report.json"

"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$INTEGRATION/replay_binary_contract.py" \
  --candidate "$SCALA_RUNNER" \
  --invalid-root "$S1_4X/contract/fixtures/invalid" \
  --output-directory "$OUTPUT_DIRECTORY/scala-binary" \
  --report "$OUTPUT_DIRECTORY/scala-binary-report.json"
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$INTEGRATION/replay_binary_contract.py" \
  --candidate "$HASKELL_RUNNER" \
  --invalid-root "$S1_4X/contract/fixtures/invalid" \
  --output-directory "$OUTPUT_DIRECTORY/haskell-binary" \
  --report "$OUTPUT_DIRECTORY/haskell-binary-report.json"
