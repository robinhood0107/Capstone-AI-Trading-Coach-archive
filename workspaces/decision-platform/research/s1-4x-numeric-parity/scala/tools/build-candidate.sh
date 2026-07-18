#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:-/home/pjjpj/.local/bin/scala-cli}"
PROFILE="A"
OUTPUT=""

usage() {
  printf 'usage: %s --profile A|B|C --output <absolute-jar-path>\n' "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --profile)
      (($# >= 2)) || usage
      PROFILE="$2"
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

[[ "$OUTPUT" == /* ]] || usage
[[ ! -e "$OUTPUT" ]] || {
  printf 'candidate output already exists: %s\n' "$OUTPUT" >&2
  exit 1
}
case "$PROFILE" in
  A | B | C) ;;
  *) usage ;;
esac

CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
mkdir -p "$CACHE_ROOT/tmp" "$CACHE_ROOT/coursier" "$(dirname -- "$OUTPUT")"
export TMPDIR="$CACHE_ROOT/tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export COURSIER_CACHE="$CACHE_ROOT/coursier"

profile_options=()
case "$PROFILE" in
  B)
    profile_options+=(--scalac-option=-opt)
    ;;
  C)
    profile_options+=(--scalac-option=-opt)
    profile_options+=('--scalac-option=-opt-inline:ai.trading.coach.s14x.**')
    ;;
esac

"$SCALA_CLI" --power package \
  "$SCALA_ROOT/project.scala" \
  "$SCALA_ROOT/selected-profile.scala" \
  "$SCALA_ROOT/src/main/scala" \
  --assembly \
  --server=false \
  --jvm system \
  --coursier-validate-checksums \
  --main-class ai.trading.coach.s14x.shell.Main \
  "${profile_options[@]}" \
  --output "$OUTPUT"

sha256sum "$OUTPUT"
