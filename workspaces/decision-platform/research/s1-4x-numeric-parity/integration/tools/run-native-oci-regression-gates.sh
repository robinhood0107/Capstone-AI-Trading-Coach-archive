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
RESULT_ROOT="${1:?usage: run-native-oci-regression-gates.sh ABSOLUTE_RESULT_ROOT [--sealed-continuation-manifest ABSOLUTE_MANIFEST]}"
shift
SEALED_CONTINUATION_MANIFEST=""
if (($# == 2)) && [[ "$1" == "--sealed-continuation-manifest" ]]; then
  SEALED_CONTINUATION_MANIFEST="$2"
  shift 2
elif (($# != 0)); then
  echo "usage: run-native-oci-regression-gates.sh ABSOLUTE_RESULT_ROOT [--sealed-continuation-manifest ABSOLUTE_MANIFEST]" >&2
  exit 64
fi
FINAL_AUDIT_ROOT="${RESULT_ROOT}-final-audit"
UV_BIN="${S1_4X_UV_BIN:?set the verified absolute uv executable path}"
DOCKER_BIN="${S1_4X_DOCKER_BIN:?set the verified absolute Docker executable path}"
DOCKER_SHA256="${S1_4X_DOCKER_SHA256:?set the verified Docker executable SHA-256}"
SCALA_BASE_IMAGE="${S1_4X_SCALA_BASE_IMAGE_REF:?set the frozen Scala base image digest reference}"
HASKELL_BASE_IMAGE="docker.io/library/haskell@sha256:417d4bc30ac7d8d5ff04ec97937f86eb508b0c76bfd1a39b5ec225688531aa9d"
VECTOR_SOURCE_ARCHIVE="${S1_4X_VECTOR_SOURCE_ARCHIVE:?set the verified vector source archive path}"
source "$INTEGRATION/tools/path-identity.sh"

case "$RESULT_ROOT" in
  /*) ;;
  *) echo "result root must be absolute" >&2; exit 64 ;;
esac
if [[ -n "$SEALED_CONTINUATION_MANIFEST" ]]; then
  [[ "$SEALED_CONTINUATION_MANIFEST" == /* \
    && -f "$SEALED_CONTINUATION_MANIFEST" \
    && ! -L "$SEALED_CONTINUATION_MANIFEST" ]] || {
    echo "sealed continuation manifest must be an absolute regular non-symlink" >&2
    exit 64
  }
fi
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
[[ "$VECTOR_SOURCE_ARCHIVE" == /* \
  && -f "$VECTOR_SOURCE_ARCHIVE" \
  && ! -L "$VECTOR_SOURCE_ARCHIVE" ]] || {
  echo "vector source archive identity is unsafe" >&2
  exit 69
}
UV_SHA256="$(sha256sum "$UV_BIN" | awk '{print $1}')"

mkdir -p \
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
source "$HASKELL/tools/python-runtime.sh"
s1_4x_pin_benchmark_python

s1_4x_pin_fresh_directory \
  "$RESULT_ROOT" \
  RESULT_ROOT_OWNER_FD \
  RESULT_PARENT_OWNER_FD \
  RESULT_ROOT_BASENAME
FINAL_AUDIT_BASENAME="${RESULT_ROOT_BASENAME}-final-audit"
s1_4x_assert_fresh_child_absent \
  "$RESULT_PARENT_OWNER_FD" "$FINAL_AUDIT_BASENAME"

run_result_command() {
  s1_4x_guarded_command \
    "$RESULT_ROOT" "$RESULT_ROOT_OWNER_FD" \
    --close-fd "$RESULT_ROOT_OWNER_FD" \
    --close-fd "$RESULT_PARENT_OWNER_FD" \
    -- "$@"
}

run_result_command_to_file() {
  local output_path="${1:?guarded output path is required}"
  shift
  s1_4x_guarded_command \
    "$RESULT_ROOT" "$RESULT_ROOT_OWNER_FD" \
    --close-fd "$RESULT_ROOT_OWNER_FD" \
    --close-fd "$RESULT_PARENT_OWNER_FD" \
    --stdout-path "$output_path" \
    -- "$@"
}

run_result_command_quiet() {
  s1_4x_guarded_command \
    "$RESULT_ROOT" "$RESULT_ROOT_OWNER_FD" \
    --close-fd "$RESULT_ROOT_OWNER_FD" \
    --close-fd "$RESULT_PARENT_OWNER_FD" \
    --discard-stdout \
    -- "$@"
}

# Vector module-safety 단계에서만 archive FD를 의도적으로 상속한다.
run_result_command_keep_vector() {
  s1_4x_guarded_command \
    "$RESULT_ROOT" "$RESULT_ROOT_OWNER_FD" \
    --close-fd "$RESULT_ROOT_OWNER_FD" \
    --close-fd "$RESULT_PARENT_OWNER_FD" \
    -- "$@"
}

if [[ -z "$SEALED_CONTINUATION_MANIFEST" ]]; then
  (
    exec {RESULT_PARENT_OWNER_FD}<&-
    for result_child in scala haskell coverage oci; do
      mkdir -- "/proc/self/fd/$RESULT_ROOT_OWNER_FD/$result_child"
    done
  )
else
  run_result_command "$UV_BIN" run --frozen --no-config --project "$ORACLE" \
    python "$INTEGRATION/continuation_prefix.py" import \
    --repo-root "$ROOT" \
    --manifest "$SEALED_CONTINUATION_MANIFEST" \
    --output-root "$RESULT_ROOT"
  run_result_command "$UV_BIN" run --frozen --project "$ORACLE" \
    python "$ORACLE/validate_contract.py" --check-all \
    --output "$RESULT_ROOT/contract-validation.json"
  run_result_command_quiet jq -e '
    .status == "PASS" and
    .functionCount == 20 and
    .errorCodeCount == 32 and
    .referenceSourceCount > 0 and
    .referenceSourceTreeCount == 4
  ' "$RESULT_ROOT/contract-validation.json"
  run_result_command_quiet jq -e '
    .schemaVersion == "s1.4x-large-fixture-materialization-receipt-v1" and
    .status == "PASS" and
    .materializedRootPathId == "S1_4X_LARGE_FIXTURE_ROOT" and
    (.manifestEntries | length) == 4 and
    (.payloadEntries | length) == 4
  ' "$RESULT_ROOT/large-fixture-receipt.json"
  run_result_command mkdir -- "$RESULT_ROOT/coverage" "$RESULT_ROOT/oci"
fi
s1_4x_assert_pinned_directory "$RESULT_ROOT" "$RESULT_ROOT_OWNER_FD"

# Guard의 pre/post identity 검사는 지속된 route substitution을 차단한다.
# 단일 producer 내부의 swap-and-restore race는 raw closure를 다시 pin/walk하는
# strict final assembler가 accepted evidence에 들어오지 못하게 해야 한다.

if [[ -z "$SEALED_CONTINUATION_MANIFEST" ]]; then
run_result_command "$UV_BIN" lock --project "$ORACLE" --check
run_result_command "$UV_BIN" sync --frozen --all-groups --project "$ORACLE"
run_result_command "$UV_BIN" run --frozen --project "$ORACLE" \
  python "$ORACLE/validate_contract.py" --check-all \
  --output "$RESULT_ROOT/contract-validation.json"
run_result_command_quiet jq -e '
  .status == "PASS" and
  .functionCount == 20 and
  .errorCodeCount == 32 and
  .referenceSourceCount > 0 and
  .referenceSourceTreeCount == 4
' "$RESULT_ROOT/contract-validation.json"
LARGE_FIXTURE_ROOT="$RESULT_ROOT/large-fixtures"
LARGE_FIXTURE_RECEIPT="$RESULT_ROOT/large-fixture-receipt.json"
run_result_command_quiet "$UV_BIN" run --frozen --no-config --project "$ORACLE" \
  python "$INTEGRATION/materialize_large_fixtures.py" materialize \
  --s1-4x-root "$S1_4X" \
  --output-root "$LARGE_FIXTURE_ROOT" \
  --receipt "$LARGE_FIXTURE_RECEIPT"
run_result_command_to_file \
  "$RESULT_ROOT/large-fixture-check-receipt.json" \
  "$UV_BIN" run --frozen --no-config --project "$ORACLE" \
  python "$INTEGRATION/materialize_large_fixtures.py" check \
  --s1-4x-root "$S1_4X" \
  --output-root "$LARGE_FIXTURE_ROOT" \
  --receipt "$LARGE_FIXTURE_RECEIPT"
export S1_4X_LARGE_FIXTURE_ROOT="$LARGE_FIXTURE_ROOT"

run_result_command "$SCALA/tools/assert-toolchain.sh"
run_result_command "$SCALA/tools/test-source-input-manifest.sh"
run_result_command "$SCALA/tools/test-source-and-jmh-policy.sh"
run_result_command "$SCALA/tools/run-scalafmt-idempotence.sh" \
  --output-dir "$RESULT_ROOT/scala/scalafmt"
run_result_command "$SCALA/tools/run-scalafix.sh" \
  --policy "$POLICY" \
  --fixture-matrix "$SCALA/tools/fixtures/source-policy-negative.v1.json" \
  --output-dir "$RESULT_ROOT/scala/scalafix"
run_result_command "$SCALA/tools/check-source-policy.sh" \
  --policy "$POLICY" \
  --semantic-receipt \
    "$RESULT_ROOT/scala/scalafix/scala-semantic-policy-receipt.v1.json" \
  --all-production-and-benchmark-inputs \
  --output "$RESULT_ROOT/scala/scala-source-policy-result.v1.json"
run_result_command "$SCALA/tools/audit-scala-dependency-edges.sh" \
  --source-policy-result "$RESULT_ROOT/scala/scala-source-policy-result.v1.json" \
  --output "$RESULT_ROOT/scala/scala-dependency-edge-result.v1.json"
for profile in A B C; do
  run_result_command "$SCALA/tools/run-hard-compiler-profile.sh" \
    --profile "$profile" \
    --output-dir "$RESULT_ROOT/scala/hard-compiler-$profile"
done
run_result_command "$SCALA/tools/run-jmh-native-smoke.sh" \
  --plan "$PLAN" \
  --profile A \
  --case-id path-transform/log_returns/n100000/b1 \
  --mode smoke \
  --output-dir "$RESULT_ROOT/scala/jmh-smoke"
export S1_4X_SCALA_JVM_ALLOWLIST_RESULT=\
"$RESULT_ROOT/scala/jmh-smoke/scala-jvm-argument-allowlist.v1.json"
run_result_command "$SCALA/tools/run-correctness-profile.sh" baseline
run_result_command "$SCALA/tools/run-correctness-profile.sh" opt
run_result_command "$SCALA/tools/run-correctness-profile.sh" opt-own-source-inline
run_result_command "$SCALA/tools/run-profile-qualification.sh" \
  --plan "$PLAN" \
  --profiles A,B,C \
  --enforce-order-plan \
  --output-dir "$RESULT_ROOT/scala/qualification"
export S1_4X_SCALA_QUALIFICATION_RESULT=\
"$RESULT_ROOT/scala/qualification/scala-profile-qualification.v1.json"
export S1_4X_SCALA_CORRECTNESS_ROOT="$RESULT_ROOT/scala/profiles"
export S1_4X_SCALA_SELECTED_PROFILE_RESULT=\
"$RESULT_ROOT/scala/scala-selected-profile-result.v1.json"
run_result_command "$SCALA/tools/select-proven-profile.sh" \
  --plan "$PLAN" \
  --qualification "$S1_4X_SCALA_QUALIFICATION_RESULT" \
  --correctness-root "$S1_4X_SCALA_CORRECTNESS_ROOT" \
  --output "$S1_4X_SCALA_SELECTED_PROFILE_RESULT"
run_result_command "$SCALA/tools/select-proven-profile.sh" \
  --check \
  --plan "$PLAN" \
  --qualification "$S1_4X_SCALA_QUALIFICATION_RESULT" \
  --correctness-root "$S1_4X_SCALA_CORRECTNESS_ROOT" \
  --output "$S1_4X_SCALA_SELECTED_PROFILE_RESULT"

run_result_command "$HASKELL/tools/assert-toolchain.sh"
run_result_command "$HASKELL/tools/check-format.sh" \
  --output-dir "$RESULT_ROOT/haskell/format"
run_result_command "$HASKELL/tools/check-hlint.sh" \
  --output-dir "$RESULT_ROOT/haskell/hlint"
run_result_command "$UV_BIN" run --frozen --project "$ORACLE" \
  python "$HASKELL/tools/tests/test_workflow_input_closure.py"
run_result_command mkdir -p "$RESULT_ROOT/haskell/profiles"
run_result_command "$HASKELL/tools/run-correctness-profile.sh" \
  baseline-o0-fasm \
  --output-dir "$RESULT_ROOT/haskell/profiles/baseline-o0-fasm"
BASELINE_RECEIPT=\
"$RESULT_ROOT/haskell/profiles/baseline-o0-fasm/correctness-receipt.v1.json"
BASELINE_STACK_WORK_DIR="$(
  run_result_command jq -er '.stackWorkDir' "$BASELINE_RECEIPT"
)"
[[ "$BASELINE_STACK_WORK_DIR" == .stack-work-* \
  && "$BASELINE_STACK_WORK_DIR" != */* ]] || {
  echo "baseline Stack work directory is not a safe one-component path" >&2
  exit 69
}
BASELINE_INTERFACE_ROOT="$(
  run_result_command realpath -e "$HASKELL/$BASELINE_STACK_WORK_DIR"
)"
run_result_command mkdir -p "$RESULT_ROOT/haskell/module-safety"
if ! exec {VECTOR_SOURCE_ARCHIVE_OWNER_FD}<"$VECTOR_SOURCE_ARCHIVE"; then
  echo "vector source archive could not be pinned" >&2
  exit 69
fi
VECTOR_SOURCE_ARCHIVE_PINNED="/proc/self/fd/$VECTOR_SOURCE_ARCHIVE_OWNER_FD"
VECTOR_ROUTE_IDENTITY="$(
  run_result_command_keep_vector \
    stat -Lc '%d:%i:%f' -- "$VECTOR_SOURCE_ARCHIVE"
)"
VECTOR_PINNED_IDENTITY="$(
  run_result_command_keep_vector \
    stat -Lc '%d:%i:%f' -- "$VECTOR_SOURCE_ARCHIVE_PINNED"
)"
[[ "$VECTOR_ROUTE_IDENTITY" == "$VECTOR_PINNED_IDENTITY" ]] || {
  exec {VECTOR_SOURCE_ARCHIVE_OWNER_FD}<&-
  echo "vector source archive route changed while pinning" >&2
  exit 69
}
VECTOR_SHA256_LINE="$(
  run_result_command_keep_vector sha256sum "$VECTOR_SOURCE_ARCHIVE_PINNED"
)"
[[ "${VECTOR_SHA256_LINE%% *}" \
  == "28f203c786cbf8ac6dc3fea3378ec36f34173d505fb4a1dd60fc8418ad91c423" ]] || {
  exec {VECTOR_SOURCE_ARCHIVE_OWNER_FD}<&-
  echo "vector source archive SHA-256 mismatch" >&2
  exit 69
}
run_result_command_keep_vector s1_4x_run_benchmark_python \
  "$HASKELL/tools/haskell_evidence.py" module-safety \
  --haskell-root "$HASKELL" \
  --numeric-root "$S1_4X" \
  --manifest "$HASKELL/source-inputs.v1.json" \
  --interface-root "$BASELINE_INTERFACE_ROOT" \
  --ghc-bin "$S1_4X_AUTHORITATIVE_GHC_BIN" \
  --vector-archive "$VECTOR_SOURCE_ARCHIVE_PINNED" \
  --output \
    "$RESULT_ROOT/haskell/module-safety/haskell-module-safety-result.v1.json" \
  --write
exec {VECTOR_SOURCE_ARCHIVE_OWNER_FD}<&-
unset VECTOR_SOURCE_ARCHIVE_PINNED
run_result_command "$HASKELL/tools/run-correctness-profile.sh" \
  optimized-o2-fasm \
  --output-dir "$RESULT_ROOT/haskell/profiles/optimized-o2-fasm"
run_result_command "$HASKELL/tools/run-profile-qualification.sh" \
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
run_result_command "$HASKELL/tools/select-proven-profile.sh" --check
else
  # Import는 sealed ancestor closure만 복원한다. 현재 실패한 profile receipt를
  # selector가 다시 판정하되 compiler/static/qualification은 재실행하지 않는다.
  # Qualification stdout에는 생성 당시 absolute workspace가 결속되므로 selector는
  # manifest에 봉인된 원본 root에서 검증하고, 복사본은 final evidence closure에 남긴다.
  export S1_4X_LARGE_FIXTURE_ROOT="$RESULT_ROOT/large-fixtures"
  SCALA_QUALIFICATION_SOURCE_ROOT="$(
    /usr/bin/jq -er '
      [.sourceTrees[]
        | select(.sourceId == "scala-qualification")
        | .sourceRoot] as $qualification
      | [.sourceTrees[]
          | select(.sourceId == "scala-jmh-smoke")
          | .sourceRoot] as $jmh
      | if (($qualification | length) == 1
          and ($jmh | length) == 1
          and $qualification[0] == $jmh[0])
        then $qualification[0]
        else error("scala source root mismatch")
        end
    ' "$SEALED_CONTINUATION_MANIFEST"
  )"
  [[ "$SCALA_QUALIFICATION_SOURCE_ROOT" == /* \
    && -d "$SCALA_QUALIFICATION_SOURCE_ROOT" \
    && ! -L "$SCALA_QUALIFICATION_SOURCE_ROOT" \
    && "$(/usr/bin/readlink -f -- "$SCALA_QUALIFICATION_SOURCE_ROOT")" \
      == "$SCALA_QUALIFICATION_SOURCE_ROOT" ]] || {
    echo "sealed Scala qualification source root is unsafe" >&2
    exit 66
  }
  export S1_4X_SCALA_JVM_ALLOWLIST_RESULT=\
"$SCALA_QUALIFICATION_SOURCE_ROOT/jmh-smoke/scala-jvm-argument-allowlist.v1.json"
  export S1_4X_SCALA_QUALIFICATION_RESULT=\
"$SCALA_QUALIFICATION_SOURCE_ROOT/qualification/scala-profile-qualification.v1.json"
  export S1_4X_SCALA_CORRECTNESS_ROOT="$RESULT_ROOT/scala/profiles"
  export S1_4X_SCALA_SELECTED_PROFILE_RESULT=\
"$RESULT_ROOT/scala/scala-selected-profile-result.v1.json"
  run_result_command "$SCALA/tools/select-proven-profile.sh" \
    --plan "$PLAN" \
    --qualification "$S1_4X_SCALA_QUALIFICATION_RESULT" \
    --correctness-root "$S1_4X_SCALA_CORRECTNESS_ROOT" \
    --output "$S1_4X_SCALA_SELECTED_PROFILE_RESULT"
  run_result_command "$SCALA/tools/select-proven-profile.sh" \
    --check \
    --plan "$PLAN" \
    --qualification "$S1_4X_SCALA_QUALIFICATION_RESULT" \
    --correctness-root "$S1_4X_SCALA_CORRECTNESS_ROOT" \
    --output "$S1_4X_SCALA_SELECTED_PROFILE_RESULT"

  export S1_4X_HASKELL_BASELINE_CORRECTNESS=\
"$RESULT_ROOT/haskell/profiles/baseline-o0-fasm/correctness-receipt.v1.json"
  export S1_4X_HASKELL_OPTIMIZED_CORRECTNESS=\
"$RESULT_ROOT/haskell/profiles/optimized-o2-fasm/correctness-receipt.v1.json"
  export S1_4X_HASKELL_QUALIFICATION_ARTIFACT=\
"$RESULT_ROOT/haskell/qualification/qualification-artifact.v1.json"
  run_result_command "$HASKELL/tools/select-proven-profile.sh" --check
fi
run_result_command "$HASKELL/tools/run-ghc-9.14.1-compatibility.sh" \
  --stack-yaml "$HASKELL/stack-ghc-9.14.1.yaml" \
  --full-matrix \
  --output-dir "$RESULT_ROOT/haskell/ghc-9.14.1"
run_result_command "$HASKELL/tools/validate-ghc-9.14.1-compatibility.sh" \
  "$RESULT_ROOT/haskell/ghc-9.14.1/ghc-9.14.1-compatibility.v1.json"
run_result_command_quiet jq -e '
  .nonScoring == true and
  .performanceInput == false and
  (.result == "PASS" or .result == "FAIL_FROZEN_DEPENDENCY")
' \
  "$RESULT_ROOT/haskell/ghc-9.14.1/ghc-9.14.1-compatibility.v1.json"

SCALA_PROFILE="$(
  run_result_command \
    jq -er '.selectedProfileId | select(. == "A" or . == "B" or . == "C")' \
    "$S1_4X_SCALA_SELECTED_PROFILE_RESULT"
)"
run_result_command "$UV_BIN" run --frozen --project "$ORACLE" \
  python "$INTEGRATION/coverage_execution.py" \
  --candidate scala \
  --profile "$SCALA_PROFILE" \
  --runner "$SCALA/tools/run-property-evidence.sh" \
  --output-directory "$RESULT_ROOT/coverage/scala" \
  --receipt "$RESULT_ROOT/coverage/scala-coverage-receipt.json" \
  --property-plan "$S1_4X/contract/property-plan.v1.json" \
  --function-registry "$S1_4X/contract/function-registry.v1.json" \
  --error-registry "$S1_4X/contract/error-registry.v1.json"
run_result_command "$UV_BIN" run --frozen --project "$ORACLE" \
  python "$INTEGRATION/coverage_execution.py" \
  --candidate haskell \
  --runner "$HASKELL/tools/run-property-evidence.sh" \
  --output-directory "$RESULT_ROOT/coverage/haskell" \
  --receipt "$RESULT_ROOT/coverage/haskell-coverage-receipt.json" \
  --property-plan "$S1_4X/contract/property-plan.v1.json" \
  --function-registry "$S1_4X/contract/function-registry.v1.json" \
  --error-registry "$S1_4X/contract/error-registry.v1.json"
run_result_command "$UV_BIN" run --frozen --project "$ORACLE" \
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

export S1_4X_SCALA_CANDIDATE_JAR=\
"$RESULT_ROOT/scala/profiles/$SCALA_PROFILE/candidate.jar"
export S1_4X_SCALA_CANDIDATE_SHA256
S1_4X_SCALA_CANDIDATE_SHA256="$(
  scala_sha256_line="$(
    run_result_command sha256sum "$S1_4X_SCALA_CANDIDATE_JAR"
  )"
  printf '%s\n' "${scala_sha256_line%% *}"
)"
run_result_command "$INTEGRATION/tools/run-integration-correctness.sh" \
  "$RESULT_ROOT/cross-language"
run_result_command "$UV_BIN" run --frozen --project "$ORACLE" \
  python "$ORACLE/compare_results.py" \
  --expected "$S1_4X/contract/fixtures/expected/canonical-results.v1.json" \
  --actual "$RESULT_ROOT/cross-language/canonical/scala-results.json" \
  --actual "$RESULT_ROOT/cross-language/canonical/haskell-results.json" \
  --output "$RESULT_ROOT/cross-language/selected-comparison.json"
run_result_command_quiet \
  jq -e '.status == "PASS" and .mismatchCount == 0' \
  "$RESULT_ROOT/cross-language/selected-comparison.json"

run_result_command_quiet "$DOCKER_BIN" image inspect "$SCALA_BASE_IMAGE"
run_result_command_quiet "$DOCKER_BIN" image inspect "$HASKELL_BASE_IMAGE"
SCALA_OCI="$RESULT_ROOT/oci/scala"
run_result_command mkdir -p "$SCALA_OCI"
SCALA_BUILD_RESULT="$SCALA_OCI/scala-oci-build-result.v1.json"
run_result_command "$SCALA/tools/build-oci-image.sh" \
  --candidate "$S1_4X_SCALA_CANDIDATE_JAR" \
  --base-image "$SCALA_BASE_IMAGE" \
  --image-tag "s1-4x-scala:${S1_4X_BENCHMARK_SUBJECT_COMMIT:0:12}" \
  --output "$SCALA_BUILD_RESULT"
run_result_command "$SCALA/tools/run-oci-correctness.sh" \
  --build-result "$SCALA_BUILD_RESULT" \
  --output-dir "$SCALA_OCI/runtime"
run_result_command_quiet \
  jq -e '.runtimeNetwork == "none" and .mismatchCount == 0' \
  "$SCALA_OCI/runtime/scala-oci-correctness-result.v1.json"

HASKELL_OCI="$RESULT_ROOT/oci/haskell"
run_result_command \
  "$HASKELL/tools/run-oci-correctness.sh" --output-dir "$HASKELL_OCI"
run_result_command_quiet \
  jq -e '.runtimeNetwork == "none" and .mismatchCount == 0' \
  "$HASKELL_OCI/oci-correctness-receipt.v1.json"
run_result_command "$UV_BIN" run --frozen --project "$ORACLE" \
  python "$ORACLE/compare_results.py" \
  --expected "$S1_4X/contract/fixtures/expected/canonical-results.v1.json" \
  --actual "$SCALA_OCI/runtime/canonical-results.json" \
  --actual "$HASKELL_OCI/runtime/canonical.actual.json" \
  --output "$RESULT_ROOT/oci/cross-language-comparison.json"
run_result_command_quiet \
  jq -e '.status == "PASS" and .mismatchCount == 0' \
  "$RESULT_ROOT/oci/cross-language-comparison.json"

run_result_command "$UV_BIN" run --frozen --no-config --project "$ORACLE" \
  python "$INTEGRATION/regression_gate.py" \
  --repo-root "$ROOT" \
  --output-root "$RESULT_ROOT/regression" \
  --uv-bin "$UV_BIN" \
  --uv-sha256 "$UV_SHA256" \
  --benchmark-subject-commit "$S1_4X_BENCHMARK_SUBJECT_COMMIT"

run_result_command "$UV_BIN" run --frozen --no-config --project "$ORACLE" \
  python "$INTEGRATION/candidate_rubric_audit.py" \
  --repository-root "$ROOT" \
  --benchmark-subject-commit "$S1_4X_BENCHMARK_SUBJECT_COMMIT" \
  --correctness-root "$RESULT_ROOT" \
  --output-root "$RESULT_ROOT/rubric-audit"

# 이 시점 이후 correctness root는 immutable raw closure다.
s1_4x_assert_pinned_directory "$RESULT_ROOT" "$RESULT_ROOT_OWNER_FD"
run_result_command "$UV_BIN" run --frozen --no-config --project "$ORACLE" \
  python "$INTEGRATION/seal_correctness_run.py" \
  --correctness-root "$RESULT_ROOT" \
  --benchmark-subject-commit "$S1_4X_BENCHMARK_SUBJECT_COMMIT"
run_result_command test -f "$RESULT_ROOT/correctness-run-manifest.v1.json"
s1_4x_assert_pinned_directory "$RESULT_ROOT" "$RESULT_ROOT_OWNER_FD"
s1_4x_assert_fresh_child_absent \
  "$RESULT_PARENT_OWNER_FD" "$FINAL_AUDIT_BASENAME"
run_result_command "$UV_BIN" run --frozen --no-config --project "$ORACLE" \
  python "$INTEGRATION/assemble_final_candidate_evidence.py" \
  --repository-root "$ROOT" \
  --benchmark-subject-commit "$S1_4X_BENCHMARK_SUBJECT_COMMIT" \
  --correctness-root "$RESULT_ROOT" \
  --production-regression-receipt \
    regression/production-compound-receipt.v1.json \
  --research-regression-receipt \
    regression/research-compound-receipt.v1.json \
  --candidate-rubric-audit rubric-audit \
  --output-root "$FINAL_AUDIT_ROOT"
s1_4x_pin_existing_directory "$FINAL_AUDIT_ROOT" FINAL_AUDIT_ROOT_OWNER_FD
run_final_audit_command() {
  local command_status
  s1_4x_assert_pinned_directory "$RESULT_ROOT" "$RESULT_ROOT_OWNER_FD" || return 73
  if s1_4x_guarded_command \
    "$FINAL_AUDIT_ROOT" "$FINAL_AUDIT_ROOT_OWNER_FD" \
    --close-fd "$FINAL_AUDIT_ROOT_OWNER_FD" \
    --close-fd "$RESULT_ROOT_OWNER_FD" \
    --close-fd "$RESULT_PARENT_OWNER_FD" \
    -- "$@"; then
    command_status=0
  else
    command_status=$?
  fi
  s1_4x_assert_pinned_directory "$RESULT_ROOT" "$RESULT_ROOT_OWNER_FD" || return 73
  return "$command_status"
}
run_final_audit_command "$UV_BIN" run --frozen --no-config --project "$ORACLE" \
  python "$INTEGRATION/final_candidate_audit.py" generate \
  --repository-root "$ROOT" \
  --benchmark-subject-commit "$S1_4X_BENCHMARK_SUBJECT_COMMIT" \
  --evidence-root "$FINAL_AUDIT_ROOT/evidence" \
  --output "$FINAL_AUDIT_ROOT/final-candidate-audit.json"
run_final_audit_command "$UV_BIN" run --frozen --no-config --project "$ORACLE" \
  python "$INTEGRATION/final_candidate_audit.py" validate \
  --repository-root "$ROOT" \
  --benchmark-subject-commit "$S1_4X_BENCHMARK_SUBJECT_COMMIT" \
  --ledger "$FINAL_AUDIT_ROOT/final-candidate-audit.json"
s1_4x_assert_pinned_directory "$RESULT_ROOT" "$RESULT_ROOT_OWNER_FD"
s1_4x_assert_pinned_directory \
  "$FINAL_AUDIT_ROOT" "$FINAL_AUDIT_ROOT_OWNER_FD"
exec {FINAL_AUDIT_ROOT_OWNER_FD}<&-
exec {RESULT_ROOT_OWNER_FD}<&-
exec {RESULT_PARENT_OWNER_FD}<&-

printf 'S1_4X_NATIVE_OCI_REGRESSION_PASS subject=%s resultRoot=%s auditRoot=%s\n' \
  "$S1_4X_BENCHMARK_SUBJECT_COMMIT" "$RESULT_ROOT" "$FINAL_AUDIT_ROOT"
