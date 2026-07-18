#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
DOCKER_BIN="${S1_4X_DOCKER_BIN:?set exact Docker CLI binary path}"
UV_BIN="${S1_4X_UV_BIN:?set exact uv 0.11.26 binary path}"
IMAGE_REF="${S1_4X_SCALA_IMAGE_REF:?set immutable local Scala OCI image ID}"
OUTPUT_DIR=""

usage() {
  printf 'usage: %s --output-dir <new-absolute-directory>\n' "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --output-dir)
      (($# >= 2)) || usage
      OUTPUT_DIR="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

[[ "$OUTPUT_DIR" == /* && ! -e "$OUTPUT_DIR" && ! -L "$OUTPUT_DIR" ]] || usage
[[ "$IMAGE_REF" =~ ^sha256:[0-9a-f]{64}$ ]] || usage
[[ -x "$DOCKER_BIN" && "$DOCKER_BIN" == /* && ! -L "$DOCKER_BIN" ]] || usage
mkdir -p "$OUTPUT_DIR"

run_candidate() {
  local request="$1"
  local output="$2"
  "$DOCKER_BIN" run --rm \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --pids-limit 128 \
    --memory 1g \
    --cpus 1 \
    --user "$(id -u):$(id -g)" \
    --volume "$OUTPUT_DIR:/evidence:rw" \
    "$IMAGE_REF" \
    run \
    --request "$request" \
    --fixture-root /opt/s1-4x/fixtures \
    --output "$output"
}

run_candidate \
  /opt/s1-4x/fixtures/small/canonical-inputs.v1.json \
  /evidence/canonical-results.json
run_candidate \
  /opt/s1-4x/fixtures/invalid/semantic-errors.v1.json \
  /evidence/semantic-errors.json

ORACLE="$S1_ROOT/oracle"
"$UV_BIN" run --project "$ORACLE" --frozen python \
  "$ORACLE/compare_results.py" \
  --expected "$S1_ROOT/contract/fixtures/expected/canonical-results.v1.json" \
  --actual "$OUTPUT_DIR/canonical-results.json" \
  >"$OUTPUT_DIR/canonical-comparison.json"
"$UV_BIN" run --project "$ORACLE" --frozen python \
  "$ORACLE/compare_results.py" \
  --expected "$S1_ROOT/contract/fixtures/invalid/semantic-errors.expected.v1.json" \
  --actual "$OUTPUT_DIR/semantic-errors.json" \
  >"$OUTPUT_DIR/semantic-comparison.json"

jq -e '.status == "PASS" and .mismatchCount == 0' \
  "$OUTPUT_DIR/canonical-comparison.json" >/dev/null
jq -e '.status == "PASS" and .mismatchCount == 0' \
  "$OUTPUT_DIR/semantic-comparison.json" >/dev/null

python3 - \
  "$OUTPUT_DIR/scala-oci-correctness-result.v1.json" \
  "$OUTPUT_DIR/canonical-results.json" \
  "$OUTPUT_DIR/semantic-errors.json" \
  "$OUTPUT_DIR/canonical-comparison.json" \
  "$OUTPUT_DIR/semantic-comparison.json" \
  "$IMAGE_REF" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

output, canonical, semantic, canonical_comparison, semantic_comparison = map(
    Path, sys.argv[1:6]
)
image_id = sys.argv[6]

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

result = {
    "schemaVersion": "s1.4x-scala-oci-correctness-result-v1",
    "imageId": image_id,
    "runtimeNetwork": "none",
    "readOnlyRoot": True,
    "capabilitiesDropped": "ALL",
    "sourceTreeMounted": False,
    "userHomeMounted": False,
    "credentialMounted": False,
    "canonicalResultSha256": digest(canonical),
    "semanticResultSha256": digest(semantic),
    "canonicalComparisonSha256": digest(canonical_comparison),
    "semanticComparisonSha256": digest(semantic_comparison),
    "mismatchCount": 0,
    "aggregateStatus": "PASS",
}
with output.open("x", encoding="utf-8", newline="\n") as stream:
    stream.write(
        json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n"
    )
PY

printf 'SCALA_OCI_CORRECTNESS_PASS imageId=%s network=none mismatchCount=0\n' \
  "$IMAGE_REF"
