#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
POLICY=""
OUTPUT="${S1_4X_SCALA_SOURCE_POLICY_OUTPUT:-}"
SEMANTIC_RECEIPT=""
ALL_INPUTS=false

usage() {
  printf 'usage: %s --policy <absolute-json> --semantic-receipt <absolute-json> --all-production-and-benchmark-inputs [--output <absolute-json>]\n' \
    "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --policy)
      (($# >= 2)) || usage
      POLICY="$2"
      shift 2
      ;;
    --all-production-and-benchmark-inputs)
      ALL_INPUTS=true
      shift
      ;;
    --semantic-receipt)
      (($# >= 2)) || usage
      SEMANTIC_RECEIPT="$2"
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

[[ "$POLICY" == /* && -f "$POLICY" ]] || usage
[[ "$SEMANTIC_RECEIPT" == /* && -f "$SEMANTIC_RECEIPT" ]] || usage
[[ "$ALL_INPUTS" == true ]] || usage
if [[ -z "$OUTPUT" ]]; then
  OUTPUT="${S1_4X_RESULT_DIR:?set S1_4X_RESULT_DIR or pass --output}"
  OUTPUT="$OUTPUT/scala-source-policy-result.v1.json"
fi
[[ "$OUTPUT" == /* && ! -e "$OUTPUT" ]] || {
  printf 'source-policy output must be a new absolute path\n' >&2
  exit 64
}

python3 "$SCALA_ROOT/tools/check_source_policy.py" \
  --scala-root "$SCALA_ROOT" \
  --policy "$POLICY" \
  --manifest "$SCALA_ROOT/source-inputs.v1.json" \
  --semantic-receipt "$SEMANTIC_RECEIPT" \
  --require-git-source-equality \
  --output "$OUTPUT"

printf 'SCALA_SOURCE_POLICY_PASS mode=semanticdb supplemental=token-receiver-audit output=%s\n' \
  "$OUTPUT"
