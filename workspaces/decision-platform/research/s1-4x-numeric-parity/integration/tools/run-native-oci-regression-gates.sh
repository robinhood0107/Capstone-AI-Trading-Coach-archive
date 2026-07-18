#!/usr/bin/env bash
set -euo pipefail

# Heavy compiler, qualification, OCI, regression gate는 증거 오염을 막기 위해 직렬 실행한다.
ROOT="$(/usr/bin/git -c core.fsmonitor=false rev-parse --show-toplevel)"
S1_4X="$ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity"
ORACLE="$S1_4X/oracle"
SCALA="$S1_4X/scala"
HASKELL="$S1_4X/haskell"
INTEGRATION="$S1_4X/integration"
PLAN="$S1_4X/benchmarks/benchmark-plan.v1.json"
POLICY="$S1_4X/contract/scala-source-policy.v1.json"
CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
RESULT_ROOT="${1:?usage: run-native-oci-regression-gates.sh ABSOLUTE_RESULT_ROOT}"
UV_BIN="${S1_4X_UV_BIN:?set the verified absolute uv executable path}"
DOCKER_BIN="${S1_4X_DOCKER_BIN:?set the verified absolute Docker executable path}"
DOCKER_SHA256="${S1_4X_DOCKER_SHA256:?set the verified Docker executable SHA-256}"
SCALA_BASE_IMAGE="${S1_4X_SCALA_BASE_IMAGE_REF:?set the frozen Scala base image digest reference}"
HASKELL_BASE_IMAGE="docker.io/library/haskell@sha256:417d4bc30ac7d8d5ff04ec97937f86eb508b0c76bfd1a39b5ec225688531aa9d"

case "$RESULT_ROOT" in
  /*) ;;
  *) echo "result root must be absolute" >&2; exit 64 ;;
esac
[[ "$CACHE_ROOT" == /* && ! -L "$CACHE_ROOT" ]] || {
  echo "cache root must be an absolute non-symlink path" >&2
  exit 64
}
for executable in "$UV_BIN" "$DOCKER_BIN"; do
  [[ "$executable" == /* && -f "$executable" && -x "$executable" \
    && ! -L "$executable" ]] || {
    echo "required executable identity is unsafe: $executable" >&2
    exit 69
  }
done
[[ "$DOCKER_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "Docker executable SHA-256 is invalid" >&2
  exit 69
}
[[ "$(sha256sum "$DOCKER_BIN" | awk '{print $1}')" == "$DOCKER_SHA256" ]] || {
  echo "Docker executable SHA-256 mismatch" >&2
  exit 69
}
[[ "$SCALA_BASE_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "Scala base image must be digest pinned" >&2
  exit 69
}
test ! -e "$RESULT_ROOT" && test ! -L "$RESULT_ROOT" || {
  echo "result root already exists: $RESULT_ROOT" >&2
  exit 73
}

mkdir -p \
  "$RESULT_ROOT/scala" \
  "$RESULT_ROOT/haskell" \
  "$RESULT_ROOT/coverage" \
  "$RESULT_ROOT/oci" \
  "$CACHE_ROOT/tmp" \
  "$CACHE_ROOT/uv" \
  "$CACHE_ROOT/coursier"

export TMPDIR="$CACHE_ROOT/tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export UV_CACHE_DIR="$CACHE_ROOT/uv"
export COURSIER_CACHE="$CACHE_ROOT/coursier"
export UV_PYTHON=3.12.13
export JAX_PLATFORMS=cpu
export JAX_ENABLE_X64=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export JAX_NUM_THREADS=1
export XLA_FLAGS="--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1"
export RESULT_DIR="$RESULT_ROOT"
export S1_4X_BENCHMARK_SUBJECT_COMMIT
S1_4X_BENCHMARK_SUBJECT_COMMIT="$(
  /usr/bin/git -c core.fsmonitor=false rev-parse --verify HEAD
)"

"$UV_BIN" lock --project "$ORACLE" --check
"$UV_BIN" sync --frozen --all-groups --project "$ORACLE"
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$ORACLE/validate_contract.py" --check-all \
  --output "$RESULT_ROOT/contract-validation.json"
jq -e '
  .status == "PASS" and
  .functionCount == 20 and
  .errorCodeCount == 32 and
  .referenceSourceCount > 0 and
  .referenceSourceTreeCount == 4
' "$RESULT_ROOT/contract-validation.json" >/dev/null
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$ORACLE/generate_large_fixtures.py" --check

"$SCALA/tools/assert-toolchain.sh"
"$SCALA/tools/test-source-input-manifest.sh"
"$SCALA/tools/test-source-and-jmh-policy.sh"
"$SCALA/tools/run-scalafmt-idempotence.sh" \
  --output-dir "$RESULT_ROOT/scala/scalafmt"
"$SCALA/tools/run-scalafix.sh" \
  --policy "$POLICY" \
  --fixture-matrix "$SCALA/tools/fixtures/source-policy-negative.v1.json" \
  --output-dir "$RESULT_ROOT/scala/scalafix"
"$SCALA/tools/check-source-policy.sh" \
  --policy "$POLICY" \
  --semantic-receipt \
    "$RESULT_ROOT/scala/scalafix/scala-semantic-policy-receipt.v1.json" \
  --all-production-and-benchmark-inputs \
  --output "$RESULT_ROOT/scala/scala-source-policy-result.v1.json"
"$SCALA/tools/audit-scala-dependency-edges.sh" \
  --source-policy-result "$RESULT_ROOT/scala/scala-source-policy-result.v1.json" \
  --output "$RESULT_ROOT/scala/scala-dependency-edge-result.v1.json"
for profile in A B C; do
  "$SCALA/tools/run-hard-compiler-profile.sh" \
    --profile "$profile" \
    --output-dir "$RESULT_ROOT/scala/hard-compiler-$profile"
done
"$SCALA/tools/run-jmh-native-smoke.sh" \
  --plan "$PLAN" \
  --profile A \
  --case-id path-transform/log_returns/n100000/b1 \
  --mode smoke \
  --output-dir "$RESULT_ROOT/scala/jmh-smoke"
export S1_4X_SCALA_JVM_ALLOWLIST_RESULT=\
"$RESULT_ROOT/scala/jmh-smoke/scala-jvm-argument-allowlist.v1.json"
"$SCALA/tools/run-correctness-profile.sh" baseline
"$SCALA/tools/run-correctness-profile.sh" opt
"$SCALA/tools/run-correctness-profile.sh" opt-own-source-inline
"$SCALA/tools/run-profile-qualification.sh" \
  --plan "$PLAN" \
  --profiles A,B,C \
  --enforce-order-plan \
  --output-dir "$RESULT_ROOT/scala/qualification"
export S1_4X_SCALA_QUALIFICATION_RESULT=\
"$RESULT_ROOT/scala/qualification/scala-profile-qualification.v1.json"
export S1_4X_SCALA_CORRECTNESS_ROOT="$RESULT_ROOT/scala/profiles"
export S1_4X_SCALA_SELECTED_PROFILE_RESULT=\
"$RESULT_ROOT/scala/scala-selected-profile-result.v1.json"
"$SCALA/tools/select-proven-profile.sh" \
  --plan "$PLAN" \
  --qualification "$S1_4X_SCALA_QUALIFICATION_RESULT" \
  --correctness-root "$S1_4X_SCALA_CORRECTNESS_ROOT" \
  --output "$S1_4X_SCALA_SELECTED_PROFILE_RESULT"
"$SCALA/tools/select-proven-profile.sh" \
  --check \
  --plan "$PLAN" \
  --qualification "$S1_4X_SCALA_QUALIFICATION_RESULT" \
  --correctness-root "$S1_4X_SCALA_CORRECTNESS_ROOT" \
  --output "$S1_4X_SCALA_SELECTED_PROFILE_RESULT"

"$HASKELL/tools/assert-toolchain.sh"
"$HASKELL/tools/check-format.sh" \
  --output-dir "$RESULT_ROOT/haskell/format"
"$HASKELL/tools/check-hlint.sh" \
  --output-dir "$RESULT_ROOT/haskell/hlint"
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$HASKELL/tools/tests/test_workflow_input_closure.py"
"$HASKELL/tools/run-correctness-profile.sh" \
  baseline-o0-fasm \
  --output-dir "$RESULT_ROOT/haskell/profiles/baseline-o0-fasm"
"$HASKELL/tools/run-correctness-profile.sh" \
  optimized-o2-fasm \
  --output-dir "$RESULT_ROOT/haskell/profiles/optimized-o2-fasm"
"$HASKELL/tools/run-profile-qualification.sh" \
  --plan "$PLAN" \
  --profiles baseline-o0-fasm,optimized-o2-fasm \
  --enforce-order-plan \
  --output-dir "$RESULT_ROOT/haskell/qualification"
export S1_4X_HASKELL_BASELINE_CORRECTNESS=\
"$RESULT_ROOT/haskell/profiles/baseline-o0-fasm/correctness-receipt.v1.json"
export S1_4X_HASKELL_OPTIMIZED_CORRECTNESS=\
"$RESULT_ROOT/haskell/profiles/optimized-o2-fasm/correctness-receipt.v1.json"
export S1_4X_HASKELL_QUALIFICATION_ARTIFACT=\
"$RESULT_ROOT/haskell/qualification/qualification-artifact.v1.json"
"$HASKELL/tools/select-proven-profile.sh" --check
"$HASKELL/tools/run-ghc-9.14.1-compatibility.sh" \
  --stack-yaml "$HASKELL/stack-ghc-9.14.1.yaml" \
  --full-matrix \
  --output-dir "$RESULT_ROOT/haskell/ghc-9.14.1"
"$HASKELL/tools/validate-ghc-9.14.1-compatibility.sh" \
  "$RESULT_ROOT/haskell/ghc-9.14.1/ghc-9.14.1-compatibility.v1.json"
jq -e '
  .nonScoring == true and
  .performanceInput == false and
  (.result == "PASS" or .result == "FAIL_FROZEN_DEPENDENCY")
' \
  "$RESULT_ROOT/haskell/ghc-9.14.1/ghc-9.14.1-compatibility.v1.json" \
  >/dev/null

"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$INTEGRATION/coverage_execution.py" \
  --candidate scala \
  --runner "$SCALA/tools/run-property-evidence.sh" \
  --output-directory "$RESULT_ROOT/coverage/scala" \
  --receipt "$RESULT_ROOT/coverage/scala-coverage-receipt.json" \
  --property-plan "$S1_4X/contract/property-plan.v1.json" \
  --function-registry "$S1_4X/contract/function-registry.v1.json" \
  --error-registry "$S1_4X/contract/error-registry.v1.json"
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$INTEGRATION/coverage_execution.py" \
  --candidate haskell \
  --runner "$HASKELL/tools/run-property-evidence.sh" \
  --output-directory "$RESULT_ROOT/coverage/haskell" \
  --receipt "$RESULT_ROOT/coverage/haskell-coverage-receipt.json" \
  --property-plan "$S1_4X/contract/property-plan.v1.json" \
  --function-registry "$S1_4X/contract/function-registry.v1.json" \
  --error-registry "$S1_4X/contract/error-registry.v1.json"
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$INTEGRATION/coverage_gate.py" \
  --property-plan "$S1_4X/contract/property-plan.v1.json" \
  --function-registry "$S1_4X/contract/function-registry.v1.json" \
  --error-registry "$S1_4X/contract/error-registry.v1.json" \
  --scala-property-report \
    "$RESULT_ROOT/coverage/scala/scala-property-report.v1.json" \
  --scala-registry-report \
    "$RESULT_ROOT/coverage/scala/scala-registry-report.v1.json" \
  --scala-execution-report \
    "$RESULT_ROOT/coverage/scala/scala-property-execution-evidence.v1.json" \
  --haskell-property-report \
    "$RESULT_ROOT/coverage/haskell/haskell-property-report.v1.json" \
  --haskell-registry-report \
    "$RESULT_ROOT/coverage/haskell/haskell-registry-report.v1.json" \
  --haskell-execution-report \
    "$RESULT_ROOT/coverage/haskell/haskell-property-execution-evidence.v1.json" \
  --output "$RESULT_ROOT/coverage/integration-coverage.json"

SCALA_PROFILE="$(
  jq -er '.selectedProfileId' "$S1_4X_SCALA_SELECTED_PROFILE_RESULT"
)"
export S1_4X_SCALA_CANDIDATE_JAR=\
"$RESULT_ROOT/scala/profiles/$SCALA_PROFILE/candidate.jar"
export S1_4X_SCALA_CANDIDATE_SHA256
S1_4X_SCALA_CANDIDATE_SHA256="$(
  sha256sum "$S1_4X_SCALA_CANDIDATE_JAR" | awk '{print $1}'
)"
"$INTEGRATION/tools/run-integration-correctness.sh" \
  "$RESULT_ROOT/cross-language"
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$ORACLE/compare_results.py" \
  --expected "$S1_4X/contract/fixtures/expected/canonical-results.v1.json" \
  --actual "$RESULT_ROOT/cross-language/canonical/scala-results.json" \
  --actual "$RESULT_ROOT/cross-language/canonical/haskell-results.json" \
  --output "$RESULT_ROOT/cross-language/selected-comparison.json"
jq -e '.status == "PASS" and .mismatchCount == 0' \
  "$RESULT_ROOT/cross-language/selected-comparison.json" >/dev/null

"$DOCKER_BIN" image inspect "$SCALA_BASE_IMAGE" >/dev/null
"$DOCKER_BIN" image inspect "$HASKELL_BASE_IMAGE" >/dev/null
SCALA_OCI="$RESULT_ROOT/oci/scala"
mkdir -p "$SCALA_OCI"
SCALA_BUILD_RESULT="$SCALA_OCI/scala-oci-build-result.v1.json"
"$SCALA/tools/build-oci-image.sh" \
  --candidate "$S1_4X_SCALA_CANDIDATE_JAR" \
  --base-image "$SCALA_BASE_IMAGE" \
  --image-tag "s1-4x-scala:${S1_4X_BENCHMARK_SUBJECT_COMMIT:0:12}" \
  --output "$SCALA_BUILD_RESULT"
"$SCALA/tools/run-oci-correctness.sh" \
  --build-result "$SCALA_BUILD_RESULT" \
  --output-dir "$SCALA_OCI/runtime"
jq -e '.runtimeNetwork == "none" and .mismatchCount == 0' \
  "$SCALA_OCI/runtime/scala-oci-correctness-result.v1.json" >/dev/null

HASKELL_OCI="$RESULT_ROOT/oci/haskell"
"$HASKELL/tools/run-oci-correctness.sh" --output-dir "$HASKELL_OCI"
jq -e '.runtimeNetwork == "none" and .mismatchCount == 0' \
  "$HASKELL_OCI/oci-correctness-receipt.v1.json" >/dev/null
"$UV_BIN" run --frozen --project "$ORACLE" \
  python "$ORACLE/compare_results.py" \
  --expected "$S1_4X/contract/fixtures/expected/canonical-results.v1.json" \
  --actual "$SCALA_OCI/runtime/canonical-results.json" \
  --actual "$HASKELL_OCI/runtime/canonical.actual.json" \
  --output "$RESULT_ROOT/oci/cross-language-comparison.json"
jq -e '.status == "PASS" and .mismatchCount == 0' \
  "$RESULT_ROOT/oci/cross-language-comparison.json" >/dev/null

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
  # S1.4R branch allowlist 한 건은 S1.4X의 더 좁은 replacement gate로 대체한다.
  "$UV_BIN" run --frozen --project "$ORACLE" pytest -q \
    "$INTEGRATION/tests/test_s1_4r_regression_boundary.py"
  "$UV_BIN" run --frozen pytest -q \
    --deselect="tests/test_production_isolation.py::test_branch_diff_is_confined_to_the_research_project_and_two_workflows"
)

printf 'S1_4X_NATIVE_OCI_REGRESSION_PASS subject=%s resultRoot=%s\n' \
  "$S1_4X_BENCHMARK_SUBJECT_COMMIT" "$RESULT_ROOT"
