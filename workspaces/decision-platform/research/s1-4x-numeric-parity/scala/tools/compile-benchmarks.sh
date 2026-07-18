#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:?set exact Scala CLI 1.15.0 binary path from readiness packet}"
PROFILE=""
OUTPUT=""

usage() {
  printf 'usage: %s --profile A|B|C --output <new-absolute-list-file>\n' "$0" >&2
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

case "$PROFILE" in
  A | B | C) ;;
  *) usage ;;
esac
[[ "$OUTPUT" == /* && ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || usage

"$SCALA_ROOT/tools/assert-toolchain.sh"
"$SCALA_ROOT/tools/assert-compiler-profiles.sh" >/dev/null
jq -e --arg profile "$PROFILE" \
  '.schemaVersion == "s1.4x-scala-compiler-profiles-v1" and
   (.profiles[$profile] != null)' \
  "$SCALA_ROOT/compiler-profiles.v1.json" >/dev/null
mapfile -t profile_options < <(
  jq -er --arg profile "$PROFILE" \
    '.profiles[$profile].scalaCliArguments[]' \
    "$SCALA_ROOT/compiler-profiles.v1.json"
)
mapfile -t benchmark_sources < <(
  python3 "$SCALA_ROOT/tools/source_input_manifest.py" \
    --scala-root "$SCALA_ROOT" \
    --manifest "$SCALA_ROOT/source-inputs.v1.json" \
    --policy "$S1_ROOT/contract/scala-source-policy.v1.json" \
    --role configuration \
    --role main \
    --role benchmark
)
[[ "${#benchmark_sources[@]}" -gt 2 ]] || {
  printf 'benchmark source manifest closure is empty\n' >&2
  exit 1
}

CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
mkdir -p "$CACHE_ROOT/tmp" "$CACHE_ROOT/coursier" "$(dirname -- "$OUTPUT")"
export TMPDIR="$CACHE_ROOT/tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export COURSIER_CACHE="$CACHE_ROOT/coursier"

# Scala CLI + JDK 25 real JVM/JMH compile/list: --jmh --jmh-version 1.37.
"$SCALA_CLI" --power run \
  "${benchmark_sources[@]}" \
  --server=false \
  --jvm system \
  --coursier-validate-checksums \
  "${profile_options[@]}" \
  --jmh --jmh-version 1.37 -- \
  -l >"$OUTPUT"

printf 'SCALA_JMH_LIST_PASS profile=%s outputSha256=%s\n' \
  "$PROFILE" "$(sha256sum "$OUTPUT" | awk '{print $1}')"
