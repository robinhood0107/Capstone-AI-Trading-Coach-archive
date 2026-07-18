#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
DOCKER_BIN="${S1_4X_DOCKER_BIN:?set validated absolute Docker CLI path}"
DOCKER_SHA256="${S1_4X_DOCKER_SHA256:?set exact Docker CLI SHA-256}"
CALLER_BASE_IMAGE="${S1_4X_SCALA_BASE_IMAGE_REF:?set caller-frozen base name@sha256:digest}"
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
[[ "$BASE_IMAGE" == "$CALLER_BASE_IMAGE" ]] || {
  printf 'base image differs from caller-frozen digest reference\n' >&2
  exit 69
}
[[ "$BASE_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || usage
[[ "$IMAGE_TAG" =~ ^[a-z0-9][a-z0-9._/-]*:[a-z0-9][a-z0-9._-]*$ ]] || usage
[[ "$OUTPUT" == /* && ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || usage
[[ "$DOCKER_BIN" == /* && -f "$DOCKER_BIN" && -x "$DOCKER_BIN" \
  && ! -L "$DOCKER_BIN" ]] || usage
[[ "$DOCKER_SHA256" =~ ^[0-9a-f]{64}$ ]] || usage

CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
mkdir -p "$CACHE_ROOT/scratch" "$(dirname -- "$OUTPUT")"
[[ "$CACHE_ROOT" == /* && -d "$CACHE_ROOT" && ! -L "$CACHE_ROOT" ]] || usage

exec python3 "$SCALA_ROOT/tools/oci_evidence.py" build \
  --docker "$DOCKER_BIN" \
  --docker-sha256 "$DOCKER_SHA256" \
  --candidate "$CANDIDATE" \
  --base-image "$BASE_IMAGE" \
  --image-tag "$IMAGE_TAG" \
  --containerfile "$SCALA_ROOT/Containerfile" \
  --fixture-root "$S1_ROOT/contract/fixtures" \
  --cache-root "$CACHE_ROOT" \
  --output "$OUTPUT"
