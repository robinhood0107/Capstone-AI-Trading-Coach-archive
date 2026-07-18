#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
DOCKER_BIN="${S1_4X_DOCKER_BIN:?set exact Docker CLI binary path}"
CANDIDATE=""
BASE_IMAGE=""
IMAGE_TAG=""
OUTPUT=""

usage() {
  printf 'usage: %s --candidate <absolute-jar> --base-image <name@sha256:digest> --image-tag <local-tag> --output <new-absolute-json>\n' \
    "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --candidate)
      (($# >= 2)) || usage
      CANDIDATE="$2"
      shift 2
      ;;
    --base-image)
      (($# >= 2)) || usage
      BASE_IMAGE="$2"
      shift 2
      ;;
    --image-tag)
      (($# >= 2)) || usage
      IMAGE_TAG="$2"
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

[[ "$CANDIDATE" == /* && -f "$CANDIDATE" && ! -L "$CANDIDATE" ]] || usage
[[ "$BASE_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || usage
[[ "$IMAGE_TAG" =~ ^[a-z0-9][a-z0-9._/-]*:[a-z0-9][a-z0-9._-]*$ ]] || usage
[[ "$OUTPUT" == /* && ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || usage
[[ -x "$DOCKER_BIN" && "$DOCKER_BIN" == /* && ! -L "$DOCKER_BIN" ]] || usage

CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
mkdir -p "$CACHE_ROOT/scratch" "$(dirname -- "$OUTPUT")"
context="$(mktemp -d -p "$CACHE_ROOT/scratch" s1-4x-scala-oci-build.XXXXXXXX)"
cleanup() {
  [[ "$context" == "$CACHE_ROOT"/scratch/s1-4x-scala-oci-build.* ]] || {
    printf 'refusing unsafe OCI context cleanup: %s\n' "$context" >&2
    exit 1
  }
  rm -rf -- "$context"
}
trap cleanup EXIT

mkdir -p "$context/fixture-bundle"
cp -- "$CANDIDATE" "$context/candidate.jar"
cp -a -- "$S1_ROOT/contract/fixtures/." "$context/fixture-bundle/"
cp -- "$SCALA_ROOT/Containerfile" "$context/Containerfile"
candidate_sha="$(sha256sum "$CANDIDATE" | awk '{print $1}')"

"$DOCKER_BIN" build \
  --network=none \
  --pull=false \
  --build-arg "S1_4X_SCALA_BASE_IMAGE=$BASE_IMAGE" \
  --build-arg "S1_4X_CANDIDATE_SHA256=$candidate_sha" \
  --tag "$IMAGE_TAG" \
  --file "$context/Containerfile" \
  "$context"

image_id="$("$DOCKER_BIN" image inspect --format '{{.Id}}' "$IMAGE_TAG")"
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  printf 'built image ID is not immutable\n' >&2
  exit 1
}
python3 - "$OUTPUT" "$BASE_IMAGE" "$IMAGE_TAG" "$image_id" "$candidate_sha" <<'PY'
import json
import sys
from pathlib import Path

output, base_image, image_tag, image_id, candidate_sha = sys.argv[1:]
result = {
    "schemaVersion": "s1.4x-scala-oci-build-result-v1",
    "baseImage": base_image,
    "localTag": image_tag,
    "imageId": image_id,
    "candidateSha256": candidate_sha,
    "buildNetwork": "none",
    "pull": False,
    "aggregateStatus": "PASS",
}
with Path(output).open("x", encoding="utf-8", newline="\n") as stream:
    stream.write(
        json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n"
    )
PY

printf 'SCALA_OCI_BUILD_PASS imageId=%s candidateSha256=%s\n' \
  "$image_id" "$candidate_sha"
