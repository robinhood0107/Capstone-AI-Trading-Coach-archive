#!/usr/bin/env bash
set -euo pipefail

# Heavy gate는 직렬 실행하여 toolchain/OCI가 서로 CPU·memory 증거를 오염시키지 않게 한다.
ROOT="$(git rev-parse --show-toplevel)"
S1_4X="$ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity"
ORACLE="$S1_4X/oracle"
CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
RESULT_ROOT="${1:?usage: run-native-oci-regression-gates.sh ABSOLUTE_RESULT_ROOT}"

case "$RESULT_ROOT" in
  /*) ;;
  *) echo "result root must be absolute" >&2; exit 64 ;;
esac
test ! -e "$RESULT_ROOT" || {
  echo "result root already exists: $RESULT_ROOT" >&2
  exit 73
}
mkdir -p "$RESULT_ROOT" "$CACHE_ROOT/tmp" "$CACHE_ROOT/uv"

export TMPDIR="$CACHE_ROOT/tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export UV_CACHE_DIR="$CACHE_ROOT/uv"
export UV_PYTHON=3.12.13
export JAX_PLATFORMS=cpu
export JAX_ENABLE_X64=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

UV_BIN="${S1_4X_UV_BIN:-$(command -v uv)}"
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$ORACLE/validate_contract.py" --check-all
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$ORACLE/generate_large_fixtures.py" --check

"$S1_4X/scala/tools/assert-toolchain.sh"
"$S1_4X/scala/tools/run-correctness-profile.sh" baseline
"$S1_4X/scala/tools/run-correctness-profile.sh" opt
"$S1_4X/scala/tools/run-correctness-profile.sh" opt-own-source-inline
"$S1_4X/scala/tools/select-proven-profile.sh" --check

"$S1_4X/haskell/tools/assert-toolchain.sh"
"$S1_4X/haskell/tools/run-correctness-profile.sh" baseline-o0-fasm
"$S1_4X/haskell/tools/run-correctness-profile.sh" optimized-o2-fasm
"$S1_4X/haskell/tools/select-proven-profile.sh" --check
"$S1_4X/haskell/tools/run-ghc-9.14.1-compatibility.sh" \
  --stack-yaml "$S1_4X/haskell/stack-ghc-9.14.1.yaml" \
  --full-matrix
"$S1_4X/haskell/tools/validate-ghc-9.14.1-compatibility.sh" \
  "$S1_4X/reports/ghc-9.14.1-compatibility.v1.json"

"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$S1_4X/integration/coverage_execution.py" \
  --candidate scala \
  --runner "$S1_4X/scala/tools/run-property-evidence.sh" \
  --output-directory "$RESULT_ROOT/scala-coverage" \
  --receipt "$RESULT_ROOT/scala-coverage-receipt.json" \
  --property-plan "$S1_4X/contract/property-plan.v1.json" \
  --function-registry "$S1_4X/contract/function-registry.v1.json" \
  --error-registry "$S1_4X/contract/error-registry.v1.json"
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$S1_4X/integration/coverage_execution.py" \
  --candidate haskell \
  --runner "$S1_4X/haskell/tools/run-property-evidence.sh" \
  --output-directory "$RESULT_ROOT/haskell-coverage" \
  --receipt "$RESULT_ROOT/haskell-coverage-receipt.json" \
  --property-plan "$S1_4X/contract/property-plan.v1.json" \
  --function-registry "$S1_4X/contract/function-registry.v1.json" \
  --error-registry "$S1_4X/contract/error-registry.v1.json"
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$S1_4X/integration/coverage_gate.py" \
  --property-plan "$S1_4X/contract/property-plan.v1.json" \
  --function-registry "$S1_4X/contract/function-registry.v1.json" \
  --error-registry "$S1_4X/contract/error-registry.v1.json" \
  --scala-property-report \
    "$RESULT_ROOT/scala-coverage/scala-property-report.v1.json" \
  --scala-registry-report \
    "$RESULT_ROOT/scala-coverage/scala-registry-report.v1.json" \
  --scala-execution-report \
    "$RESULT_ROOT/scala-coverage/scala-property-execution-evidence.v1.json" \
  --haskell-property-report \
    "$RESULT_ROOT/haskell-coverage/haskell-property-report.v1.json" \
  --haskell-registry-report \
    "$RESULT_ROOT/haskell-coverage/haskell-registry-report.v1.json" \
  --haskell-execution-report \
    "$RESULT_ROOT/haskell-coverage/haskell-property-execution-evidence.v1.json" \
  --output "$RESULT_ROOT/integration-coverage.json"

"$S1_4X/integration/tools/run-integration-correctness.sh" \
  "$RESULT_ROOT/native"

SCALA_IMAGE="s1-4x-scala-correctness:$(git rev-parse --short=12 HEAD)"
HASKELL_IMAGE="s1-4x-haskell-correctness:$(git rev-parse --short=12 HEAD)"
"$S1_4X/scala/tools/run-oci-correctness.sh" \
  --image "$SCALA_IMAGE" \
  --request "$S1_4X/contract/fixtures/small/canonical-inputs.v1.json" \
  --fixture-root "$S1_4X/contract/fixtures" \
  --output "$RESULT_ROOT/scala-oci-results.json"
"$S1_4X/haskell/tools/run-oci-correctness.sh" \
  --image "$HASKELL_IMAGE" \
  --request "$S1_4X/contract/fixtures/small/canonical-inputs.v1.json" \
  --fixture-root "$S1_4X/contract/fixtures" \
  --output "$RESULT_ROOT/haskell-oci-results.json"

"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$ORACLE/compare_results.py" \
  --expected "$S1_4X/contract/fixtures/expected/canonical-results.v1.json" \
  --request "$S1_4X/contract/fixtures/small/canonical-inputs.v1.json" \
  --actual "$RESULT_ROOT/scala-oci-results.json" \
  --actual "$RESULT_ROOT/haskell-oci-results.json" \
  --output "$RESULT_ROOT/oci-comparison.json"

(
  cd "$ROOT/workspaces/decision-platform/python-services"
  "$UV_BIN" lock --check
  "$UV_BIN" sync --frozen
  "$UV_BIN" run --frozen ruff check .
  "$UV_BIN" run --frozen mypy app
  "$UV_BIN" run --frozen pytest -q
)
(
  cd "$ROOT/workspaces/decision-platform/research/s1-4r-jax-risk"
  "$UV_BIN" lock --check
  "$UV_BIN" sync --frozen --all-groups
  "$UV_BIN" run --frozen ruff check .
  "$UV_BIN" run --frozen mypy src benchmarks
  "$UV_BIN" run --frozen pytest -q
)
