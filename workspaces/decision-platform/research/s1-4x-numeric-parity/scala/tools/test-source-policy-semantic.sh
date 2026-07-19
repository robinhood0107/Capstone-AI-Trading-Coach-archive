#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
evidence_root="${S1_4X_SEMANTIC_EVIDENCE_DIR:-}"

if [[ -n "$evidence_root" ]]; then
  [[ "$evidence_root" == /* && ! -e "$evidence_root" ]] || {
    printf 'persistent semantic evidence directory must be a new absolute path\n' >&2
    exit 64
  }
else
  TEST_TMP_ROOT="${S1_4X_TEST_TMP_ROOT:-${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}/tmp}"
  mkdir -p "$TEST_TMP_ROOT"
  TEST_TMP_ROOT="$(realpath -- "$TEST_TMP_ROOT")"
  temporary="$(mktemp -d -p "$TEST_TMP_ROOT" s1-4x-scala-semantic.XXXXXXXX)"
  evidence_root="$temporary/evidence"
  cleanup() {
    [[ "$temporary" == "$TEST_TMP_ROOT"/s1-4x-scala-semantic.* ]] || {
      printf 'refusing unsafe temporary cleanup: %s\n' "$temporary" >&2
      exit 1
    }
    rm -rf -- "$temporary"
  }
  trap cleanup EXIT
fi

"$SCALA_ROOT/tools/run-scalafix.sh" \
  --policy "$S1_ROOT/contract/scala-source-policy.v1.json" \
  --fixture-matrix "$SCALA_ROOT/tools/fixtures/source-policy-negative.v1.json" \
  --output-dir "$evidence_root"

receipt="$evidence_root/scala-semantic-policy-receipt.v1.json"
jq -e \
  --slurpfile matrix "$SCALA_ROOT/tools/fixtures/source-policy-negative.v1.json" '
    .schemaVersion == "s1.4x-scala-semantic-policy-receipt-v1" and
    .checkerMode == "semanticdb" and
    .semanticSmokeStatus == "PASS" and
    .semanticdb.fileCount > 0 and
    (.semanticdb.rootSha256 | test("^[0-9a-f]{64}$")) and
    (.scalafix.binarySha256 | test("^[0-9a-f]{64}$")) and
    (.scalafix.commandArgvSha256 | test("^[0-9a-f]{64}$")) and
    (.rule.sourceSha256 | test("^[0-9a-f]{64}$")) and
    (.rule.classpathSha256 | test("^[0-9a-f]{64}$")) and
    ([.negativeMatrix[].fixtureId] == [$matrix[0].fixtures[].fixtureId]) and
    ([.negativeMatrix[].status] | all(. == "PASS")) and
    .status == "PASS"
  ' "$receipt" >/dev/null

printf 'SCALA_SEMANTIC_SOURCE_POLICY_PASS receipt=%s\n' "$receipt"
