#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:-/home/pjjpj/.local/bin/scala-cli}"
SCALAFIX="${S1_4X_SCALAFIX_BIN:-/home/pjjpj/.local/share/s1-4x/scalafix-0.14.7/bin/scalafix}"
POLICY=""
FIXTURE_MATRIX=""
OUTPUT_DIR=""

usage() {
  printf 'usage: %s --policy <absolute-json> --fixture-matrix <absolute-json> --output-dir <new-absolute-directory>\n' \
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
    --fixture-matrix)
      (($# >= 2)) || usage
      FIXTURE_MATRIX="$2"
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

[[ "$POLICY" == /* && -f "$POLICY" && ! -L "$POLICY" ]] || usage
[[ "$FIXTURE_MATRIX" == /* && -f "$FIXTURE_MATRIX" && ! -L "$FIXTURE_MATRIX" ]] || usage
[[ "$OUTPUT_DIR" == /* && ! -e "$OUTPUT_DIR" && ! -L "$OUTPUT_DIR" ]] || usage

"$SCALA_ROOT/tools/assert-toolchain.sh"

CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
mkdir -p "$CACHE_ROOT/tmp" "$CACHE_ROOT/coursier"
export TMPDIR="$CACHE_ROOT/tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export COURSIER_CACHE="$CACHE_ROOT/coursier"

python3 "$SCALA_ROOT/tools/run_scalafix.py" \
  --scala-root "$SCALA_ROOT" \
  --policy "$POLICY" \
  --fixture-matrix "$FIXTURE_MATRIX" \
  --output-dir "$OUTPUT_DIR" \
  --scala-cli "$(readlink -f -- "$SCALA_CLI")" \
  --scalafix "$(readlink -f -- "$SCALAFIX")"
