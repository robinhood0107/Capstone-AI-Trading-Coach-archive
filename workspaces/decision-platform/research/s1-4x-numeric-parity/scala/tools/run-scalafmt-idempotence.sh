#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:-/home/pjjpj/.local/bin/scala-cli}"
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

"$SCALA_ROOT/tools/assert-toolchain.sh"

CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
mkdir -p "$CACHE_ROOT/tmp" "$CACHE_ROOT/coursier"
export TMPDIR="$CACHE_ROOT/tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export COURSIER_CACHE="$CACHE_ROOT/coursier"

python3 "$SCALA_ROOT/tools/run_scalafmt.py" \
  --scala-root "$SCALA_ROOT" \
  --policy "$S1_ROOT/contract/scala-source-policy.v1.json" \
  --manifest "$SCALA_ROOT/source-inputs.v1.json" \
  --scala-cli "$(readlink -f -- "$SCALA_CLI")" \
  --toolchain-lock "$SCALA_ROOT/toolchain-lock.v1.json" \
  --output-dir "$OUTPUT_DIR"
