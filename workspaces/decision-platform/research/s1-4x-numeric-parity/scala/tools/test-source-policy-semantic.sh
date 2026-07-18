#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
temporary="$(mktemp -d -t s1-4x-scala-semantic.XXXXXXXX)"

cleanup() {
  [[ "$temporary" == /tmp/s1-4x-scala-semantic.* ]] || {
    printf 'refusing unsafe temporary cleanup: %s\n' "$temporary" >&2
    exit 1
  }
  rm -rf -- "$temporary"
}
trap cleanup EXIT

"$SCALA_ROOT/tools/run-scalafix.sh" \
  --policy "$S1_ROOT/contract/scala-source-policy.v1.json" \
  --fixture-matrix "$SCALA_ROOT/tools/fixtures/source-policy-negative.v1.json" \
  --output-dir "$temporary/evidence"

receipt="$temporary/evidence/scala-semantic-policy-receipt.v1.json"
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
