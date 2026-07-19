#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
PLAN=""
OUTPUT=""

usage() {
  printf 'usage: %s --plan <absolute-json> [--output <absolute-json>]\n' "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --plan)
      (($# >= 2)) || usage
      PLAN="$2"
      shift 2
      ;;
    --output)
      (($# >= 2)) || usage
      OUTPUT="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

[[ "$PLAN" == /* && -f "$PLAN" ]] || usage
[[ -z "$OUTPUT" || "$OUTPUT" == /* ]] || usage
[[ -z "$OUTPUT" || ! -e "$OUTPUT" ]] || {
  printf 'JMH integrity output already exists: %s\n' "$OUTPUT" >&2
  exit 1
}

POLICY="$S1_ROOT/contract/scala-source-policy.v1.json"
EXPECTED_PLAN_SHA="$(
  awk 'NR == 1 {print $1}' "$S1_ROOT/benchmarks/benchmark-plan.v1.sha256"
)"
PLAN_SHA="$(sha256sum "$PLAN" | awk '{print $1}')"
POLICY_SHA="$(sha256sum "$POLICY" | awk '{print $1}')"

[[ "$PLAN_SHA" == "$EXPECTED_PLAN_SHA" ]] || {
  printf 'JMH plan SHA mismatch\n' >&2
  exit 1
}
[[ "$(jq -r '.scalaJmhPolicy.sourceAnnotationPolicySha256' "$PLAN")" == "$POLICY_SHA" ]] || {
  printf 'JMH source-policy SHA binding mismatch\n' >&2
  exit 1
}
jq -e '
  .schemaVersion == "s1.4x-benchmark-plan-v1" and
  .planId == "s1.4x-full-same-host-v1" and
  (.cases | length) == 89 and
  ([.cases[].caseId] | unique | length) == 89
' "$PLAN" >/dev/null

mapfile -t public_annotations < <(
  jq -r '.jmhAnnotationPolicy.allowedAnnotationsAndValues | keys[]' "$POLICY" | sort
)
[[ "${public_annotations[*]}" == \
  "org.openjdk.jmh.annotations.Benchmark org.openjdk.jmh.annotations.Setup org.openjdk.jmh.annotations.State" ]] || {
  printf 'public JMH annotation allowlist drifted\n' >&2
  exit 1
}
jq -e '
  (.jmhAnnotationPolicy.forbiddenPlanOverrideAnnotations | index(
    "org.openjdk.jmh.annotations.Param"
  )) != null and
  (.jmhAnnotationPolicy.allowedAnnotationsAndValues |
    has("org.openjdk.jmh.annotations.OperationsPerInvocation") | not)
' "$POLICY" >/dev/null

families=(
  path-transform
  classical-path-risk
  intraday-realized
  serial-sharpe
  probabilistic-scalar
  coverage-batch
)
packages=(
  path_transform
  classical_path_risk
  intraday_realized
  serial_sharpe
  probabilistic_scalar
  coverage_batch
)
classes=(
  PathTransformBenchmark
  ClassicalPathRiskBenchmark
  IntradayRealizedBenchmark
  SerialSharpeBenchmark
  ProbabilisticScalarBenchmark
  CoverageBatchBenchmark
)
expected_counts=(15 45 10 5 2 12)
expected_operations=(1 1 1 1 16384 32)

actual_wrappers="$(
  find "$SCALA_ROOT/benchmarks" -type f -name '*Benchmark.scala' -print | sort
)"
[[ "$(wc -l <<<"$actual_wrappers")" -eq 6 ]] || {
  printf 'JMH wrapper count mismatch\n' >&2
  exit 1
}

for index in "${!families[@]}"; do
  family="${families[$index]}"
  package="${packages[$index]}"
  class="${classes[$index]}"
  wrapper="$SCALA_ROOT/benchmarks/scala/s1_4x/benchmarks/$package/$class.scala"
  [[ -f "$wrapper" ]] || {
    printf 'missing JMH wrapper: %s\n' "$wrapper" >&2
    exit 1
  }

  mapfile -t annotations < <(
    sed -En 's/^[[:space:]]*(@[^[:space:]()]+).*/\1/p' "$wrapper"
  )
  [[ "${annotations[*]}" == "@State @Setup @Benchmark" ]] || {
    printf 'JMH annotation set mismatch: %s\n' "$wrapper" >&2
    exit 1
  }
  grep -Fq '@State(Scope.Benchmark)' "$wrapper"
  grep -Fq '@Setup(Level.Trial)' "$wrapper"
  grep -Eq '^[[:space:]]*@Benchmark[[:space:]]*$' "$wrapper"
  grep -Fq "private lazy val invocation = BenchmarkInvocation.fromEnvironment(\"$family\")" \
    "$wrapper"
  ! grep -Eq '@(Param|OperationsPerInvocation|Fork|Threads|Warmup|Measurement|BenchmarkMode|OutputTimeUnit|CompilerControl|Timeout)\b' \
    "$wrapper"
  ! grep -Eq '\b(var|while|return|throw|null)\b' "$wrapper"

  selector="scala/$family"
  jq -e \
    --arg selector "$selector" \
    --arg family "$family" \
    --argjson count "${expected_counts[$index]}" \
    --argjson operations "${expected_operations[$index]}" '
      ([.familySelectors[] | select(.selectorId == $selector)] | length) == 1 and
      ([.cases[] | select(.familyId == $family)] | length) == $count and
      ([.cases[] | select(.familyId == $family) |
        .logicalOperationsPerInvocation] | all(. == $operations)) and
      ([.familySelectors[] | select(.selectorId == $selector) | .expectedCaseIds][0] ==
        [.cases[] | select(.familyId == $family) | .caseId])
    ' "$PLAN" >/dev/null
done

invocation="$SCALA_ROOT/benchmarks/scala/ai/trading/coach/s14x/benchmark/BenchmarkInvocation.scala"
[[ -f "$invocation" ]] || {
  printf 'missing Scala benchmark invocation\n' >&2
  exit 1
}
grep -Fq "$PLAN_SHA" "$invocation"
grep -Fq 'S1_4X_BENCHMARK_CASE_ID' "$invocation"
grep -Fq 'S1_4X_BENCHMARK_PLAN' "$invocation"
grep -Fq 'S1_4X_FIXTURE_ROOT' "$invocation"
if find "$SCALA_ROOT/benchmarks/scala" -type f -name '*.scala' -exec \
  sed -En '/^[[:space:]]*@(Param|OperationsPerInvocation)([^A-Za-z]|$)/p' {} + |
  grep -q .; then
  printf 'forbidden JMH parameter/operation annotation found\n' >&2
  exit 1
fi

if [[ -n "$OUTPUT" ]]; then
  mkdir -p "$(dirname -- "$OUTPUT")"
  temporary="$OUTPUT.tmp.$$"
  jq -n \
    --arg planSha256 "$PLAN_SHA" \
    --arg policySha256 "$POLICY_SHA" \
    --argjson cases "$(jq '[.cases[] | {
      caseId,
      familyId,
      functionId,
      fixtureId,
      functionArguments,
      logicalOperationsPerInvocation
    }]' "$PLAN")" \
    '{
      schemaVersion: "s1.4x-scala-jmh-plan-integrity-v1",
      planSha256: $planSha256,
      publicSourcePolicySha256: $policySha256,
      precedenceResolution: "public-gate1-policy",
      allowedAnnotations: [
        "org.openjdk.jmh.annotations.Benchmark",
        "org.openjdk.jmh.annotations.Setup(Level.Trial)",
        "org.openjdk.jmh.annotations.State(Scope.Benchmark)"
      ],
      forbiddenAnnotations: [
        "org.openjdk.jmh.annotations.Param",
        "org.openjdk.jmh.annotations.OperationsPerInvocation"
      ],
      caseSelection: "external-single-case-process",
      logicalOperationSource: "frozen-benchmark-plan-metadata",
      cases: $cases,
      status: "PASS"
    }' >"$temporary"
  mv -- "$temporary" "$OUTPUT"
fi

printf 'SCALA_JMH_PLAN_INTEGRITY_PASS planSha256=%s policySha256=%s cases=89\n' \
  "$PLAN_SHA" "$POLICY_SHA"
