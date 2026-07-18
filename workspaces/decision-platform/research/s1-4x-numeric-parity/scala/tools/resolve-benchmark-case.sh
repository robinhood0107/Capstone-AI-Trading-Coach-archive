#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
PLAN=""
BOUNDARY=""
SELECTOR=""
FAMILY=""
CASE_ID=""

usage() {
  printf 'usage: %s --plan <absolute-json> --boundary scala --selector <id> --family <id> --case-id <id>\n' \
    "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --plan)
      (($# >= 2)) || usage
      PLAN="$2"
      shift 2
      ;;
    --boundary)
      (($# >= 2)) || usage
      BOUNDARY="$2"
      shift 2
      ;;
    --selector)
      (($# >= 2)) || usage
      SELECTOR="$2"
      shift 2
      ;;
    --family)
      (($# >= 2)) || usage
      FAMILY="$2"
      shift 2
      ;;
    --case-id)
      (($# >= 2)) || usage
      CASE_ID="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

[[ "$PLAN" == /* && -f "$PLAN" ]] || usage
[[ "$BOUNDARY" == "scala" && "$SELECTOR" == "scala/$FAMILY" ]] || usage
[[ "$CASE_ID" =~ ^[a-z0-9][a-z0-9._/-]{0,191}$ ]] || usage

EXPECTED_PLAN_SHA="$(
  awk 'NR == 1 {print $1}' "$S1_ROOT/benchmarks/benchmark-plan.v1.sha256"
)"
[[ "$(sha256sum "$PLAN" | awk '{print $1}')" == "$EXPECTED_PLAN_SHA" ]] || {
  printf 'benchmark plan SHA mismatch\n' >&2
  exit 1
}

jq -e \
  --arg boundary "$BOUNDARY" \
  --arg selector "$SELECTOR" \
  --arg family "$FAMILY" \
  --arg caseId "$CASE_ID" '
    ([.familySelectors[] |
      select(.boundaryId == $boundary and .selectorId == $selector and .familyId == $family)] |
      length) == 1 and
    ([.familySelectors[] |
      select(.boundaryId == $boundary and .selectorId == $selector and .familyId == $family) |
      .expectedCaseIds[] |
      select(. == $caseId)] |
      length) == 1 and
    ([.cases[] |
      select(.caseId == $caseId and .familyId == $family)] |
      length) == 1
  ' "$PLAN" >/dev/null || {
  printf 'benchmark case is outside frozen selector: %s\n' "$CASE_ID" >&2
  exit 1
}

jq -c \
  --arg caseId "$CASE_ID" '
    .cases[] |
    select(.caseId == $caseId) |
    {
      caseId,
      familyId,
      functionId,
      fixtureId,
      functionArguments,
      logicalOperationsPerInvocation,
      vectorLength,
      batchSize
    }
  ' "$PLAN"
