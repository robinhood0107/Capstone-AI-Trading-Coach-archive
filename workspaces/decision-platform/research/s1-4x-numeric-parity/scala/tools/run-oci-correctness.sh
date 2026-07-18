#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
DOCKER_BIN="${S1_4X_DOCKER_BIN:?set validated absolute Docker CLI path}"
DOCKER_SHA256="${S1_4X_DOCKER_SHA256:?set exact Docker CLI SHA-256}"
UV_BIN="${S1_4X_UV_BIN:?set exact uv 0.11.26 binary path}"
BUILD_RESULT=""
OUTPUT_DIR=""

usage() {
  printf 'usage: %s --build-result <absolute-build-receipt> --output-dir <new-absolute-directory>\n' \
    "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --build-result)
      (($# >= 2)) || usage
      BUILD_RESULT="$2"
      shift 2
      ;;
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

[[ "$BUILD_RESULT" == /* && -f "$BUILD_RESULT" && ! -L "$BUILD_RESULT" ]] || usage
[[ "$OUTPUT_DIR" == /* && ! -e "$OUTPUT_DIR" && ! -L "$OUTPUT_DIR" ]] || usage
[[ "$DOCKER_BIN" == /* && -f "$DOCKER_BIN" && -x "$DOCKER_BIN" \
  && ! -L "$DOCKER_BIN" ]] || usage
[[ "$DOCKER_SHA256" =~ ^[0-9a-f]{64}$ ]] || usage
[[ "$UV_BIN" == /* && -f "$UV_BIN" && -x "$UV_BIN" && ! -L "$UV_BIN" ]] || usage
mkdir -p "$OUTPUT_DIR"

BINDING_BEFORE="$OUTPUT_DIR/oci-runtime-binding-before.v1.json"
BINDING_AFTER="$OUTPUT_DIR/oci-runtime-binding-after.v1.json"
python3 "$SCALA_ROOT/tools/oci_evidence.py" runtime-binding \
  --docker "$DOCKER_BIN" \
  --docker-sha256 "$DOCKER_SHA256" \
  --build-receipt "$BUILD_RESULT" \
  >"$BINDING_BEFORE"

IMAGE_ID="$(
  jq -er '.imageId | select(test("^sha256:[0-9a-f]{64}$"))' \
    "$BINDING_BEFORE"
)"

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
    "$IMAGE_ID" \
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

# 실행 뒤에도 동일 CLI/context/daemon/image/label closure인지 다시 확인한다.
python3 "$SCALA_ROOT/tools/oci_evidence.py" runtime-binding \
  --docker "$DOCKER_BIN" \
  --docker-sha256 "$DOCKER_SHA256" \
  --build-receipt "$BUILD_RESULT" \
  >"$BINDING_AFTER"
cmp --silent "$BINDING_BEFORE" "$BINDING_AFTER" || {
  printf 'Docker runtime identity changed during OCI correctness run\n' >&2
  exit 70
}

python3 - \
  "$OUTPUT_DIR/scala-oci-correctness-result.v1.json" \
  "$OUTPUT_DIR/canonical-results.json" \
  "$OUTPUT_DIR/semantic-errors.json" \
  "$OUTPUT_DIR/canonical-comparison.json" \
  "$OUTPUT_DIR/semantic-comparison.json" \
  "$BINDING_BEFORE" \
  "$BUILD_RESULT" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

(
    output,
    canonical,
    semantic,
    canonical_comparison,
    semantic_comparison,
    binding_path,
    build_receipt,
) = map(Path, sys.argv[1:8])


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


binding = json.loads(binding_path.read_text(encoding="utf-8"))
required = {
    "schemaVersion",
    "imageId",
    "buildReceiptSha256",
    "candidateSha256",
    "baseImageReference",
    "baseImageId",
    "dockerIdentity",
    "status",
}
if (
    set(binding) != required
    or binding["schemaVersion"] != "s1.4x-scala-oci-runtime-binding-v1"
    or binding["status"] != "PASS"
    or re.fullmatch(r"sha256:[0-9a-f]{64}", binding["imageId"]) is None
    or binding["buildReceiptSha256"] != digest(build_receipt)
):
    raise SystemExit("invalid OCI runtime binding")

result = {
    "schemaVersion": "s1.4x-scala-oci-correctness-result-v2",
    "imageId": binding["imageId"],
    "buildReceiptSha256": binding["buildReceiptSha256"],
    "candidateSha256": binding["candidateSha256"],
    "baseImageReference": binding["baseImageReference"],
    "baseImageId": binding["baseImageId"],
    "dockerIdentity": binding["dockerIdentity"],
    "dockerIdentitySha256": hashlib.sha256(
        json.dumps(
            binding["dockerIdentity"],
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest(),
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
    "runtimeBindingSha256": digest(binding_path),
    "mismatchCount": 0,
    "aggregateStatus": "PASS",
}
with output.open("x", encoding="utf-8", newline="\n") as stream:
    stream.write(
        json.dumps(
            result,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
PY

printf 'SCALA_OCI_CORRECTNESS_PASS imageId=%s network=none mismatchCount=0\n' \
  "$IMAGE_ID"
