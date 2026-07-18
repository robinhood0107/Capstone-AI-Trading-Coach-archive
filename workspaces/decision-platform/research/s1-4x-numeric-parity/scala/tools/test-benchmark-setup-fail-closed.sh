#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:-/home/pjjpj/.local/bin/scala-cli}"
PLAN="$S1_ROOT/benchmarks/benchmark-plan.v1.json"
temporary="$(mktemp -d -t s1-4x-scala-setup.XXXXXXXX)"

cleanup() {
  [[ "$temporary" == /tmp/s1-4x-scala-setup.* ]] || {
    printf 'refusing unsafe temporary cleanup: %s\n' "$temporary" >&2
    exit 1
  }
  rm -rf -- "$temporary"
}
trap cleanup EXIT

mkdir -p "$temporary/empty-fixtures"
case_id="$(
  jq -r '.familySelectors[] |
    select(.boundaryId == "scala" and .familyId == "path-transform") |
    .expectedCaseIds[0]' "$PLAN"
)"
command=(
  "$SCALA_CLI" --power run
  "$SCALA_ROOT/project.scala"
  "$SCALA_ROOT/selected-profile.scala"
  "$SCALA_ROOT/src/main/scala"
  "$SCALA_ROOT/benchmarks"
  --server=false
  --jvm system
  --coursier-validate-checksums
  --main-class ai.trading.coach.s14x.benchmark.BenchmarkSetupProbeMain
  --
  path-transform
)

set +e
S1_4X_BENCHMARK_CASE_ID="$case_id" \
S1_4X_BENCHMARK_PLAN="$PLAN" \
S1_4X_FIXTURE_ROOT="$temporary/empty-fixtures" \
  "${command[@]}" >"$temporary/missing-fixture.stdout" 2>"$temporary/missing-fixture.stderr"
missing_fixture_exit=$?
set -e
[[ "$missing_fixture_exit" -eq 70 ]]
! grep -Eq '(^|[[:space:]])(Exception|Error)(:|[[:space:]])|at [A-Za-z0-9_.]+\\(' \
  "$temporary/missing-fixture.stderr"

set +e
S1_4X_BENCHMARK_CASE_ID="$case_id" \
S1_4X_BENCHMARK_PLAN="$temporary/absent-plan.json" \
S1_4X_FIXTURE_ROOT="$temporary/empty-fixtures" \
  "${command[@]}" >"$temporary/missing-plan.stdout" 2>"$temporary/missing-plan.stderr"
missing_plan_exit=$?
set -e
[[ "$missing_plan_exit" -eq 70 ]]
! grep -Eq '(^|[[:space:]])(Exception|Error)(:|[[:space:]])|at [A-Za-z0-9_.]+\\(' \
  "$temporary/missing-plan.stderr"

printf 'SCALA_BENCHMARK_SETUP_FAIL_CLOSED_PASS missingFixture=70 missingPlan=70\n'
