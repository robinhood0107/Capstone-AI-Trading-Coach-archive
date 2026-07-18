#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
SOURCE_POLICY_RESULT=""
OUTPUT=""

usage() {
  printf 'usage: %s --source-policy-result <absolute-json> --output <new-absolute-json>\n' "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --source-policy-result)
      (($# >= 2)) || usage
      SOURCE_POLICY_RESULT="$2"
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

[[ "$SOURCE_POLICY_RESULT" == /* && -f "$SOURCE_POLICY_RESULT" && ! -L "$SOURCE_POLICY_RESULT" ]] || usage
[[ "$OUTPUT" == /* && ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || usage

python3 "$SCALA_ROOT/tools/audit_scala_dependencies.py" \
  --scala-root "$SCALA_ROOT" \
  --policy "$S1_ROOT/contract/scala-source-policy.v1.json" \
  --manifest "$SCALA_ROOT/source-inputs.v1.json" \
  --source-policy-result "$SOURCE_POLICY_RESULT" \
  --output "$OUTPUT"

jq -e '
  .schemaVersion == "s1.4x-scala-dependency-native-edge-result-v1" and
  .candidateAuthoredEdgeCount == 0 and
  .candidateAddedNativeDependencyCount == 0 and
  .candidateCoreDirectNativeBindingImportCount == 0 and
  .candidateCoreDirectNativeBindingCallCount == 0 and
  .timedKernelExplicitCandidateNativeInteropCallCount == 0 and
  .aggregateStatus == "PASS"
' "$OUTPUT" >/dev/null

printf 'SCALA_DEPENDENCY_NATIVE_EDGE_PASS result=%s resultSha256=%s candidateAuthoredEdgeCount=0\n' \
  'scala-dependency-native-edge-result.v1.json' \
  "$(sha256sum "$OUTPUT" | awk '{print $1}')"
