#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
RUNNER_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:-/home/pjjpj/.local/bin/scala-cli}"
PROFILE="A"
OUTPUT_DIR=""
ORIGINAL_ARGUMENTS=("$@")

usage() {
  printf 'usage: %s --output-dir <absolute-path> [--profile A|B|C]\n' "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --output-dir)
      (($# >= 2)) || usage
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --profile)
      (($# >= 2)) || usage
      PROFILE="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

[[ "$OUTPUT_DIR" == /* ]] || usage
case "$PROFILE" in
  A | B | C) ;;
  *) usage ;;
esac

for name in \
  scala-property-report.v1.json \
  scala-registry-report.v1.json \
  scala-property-execution-evidence.v1.json; do
  [[ ! -e "$OUTPUT_DIR/$name" ]] || {
    printf 'property evidence output already exists: %s\n' "$OUTPUT_DIR/$name" >&2
    exit 1
  }
done

CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
mkdir -p "$CACHE_ROOT/tmp" "$CACHE_ROOT/coursier" "$OUTPUT_DIR"
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

command_prefix=(
  "$SCALA_CLI" --power run
  "$SCALA_ROOT/project.scala"
  "$SCALA_ROOT/selected-profile.scala"
  "$SCALA_ROOT/src"
  --test
  --server=false
  --jvm system
  --coursier-validate-checksums
  --main-class ai.trading.coach.s14x.shell.PropertyEvidenceMain
  "${profile_options[@]}"
)
runner_arguments=(
  --output-dir "$OUTPUT_DIR"
  --s1-root "$S1_ROOT"
  --profile "$PROFILE"
  --runner-path "$RUNNER_PATH"
)
command_argv_sha="$(
  python3 - "$RUNNER_PATH" "${ORIGINAL_ARGUMENTS[@]}" <<'PY'
import hashlib
import json
import sys

payload = json.dumps(
    sys.argv[1:],
    allow_nan=False,
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
).encode("utf-8")
print(hashlib.sha256(payload).hexdigest())
PY
)"

"${command_prefix[@]}" -- \
  "${runner_arguments[@]}" \
  --command-argv-sha256 "$command_argv_sha"

printf 'SCALA_PROPERTY_EVIDENCE_PASS profile=%s outputDir=%s commandArgvSha256=%s\n' \
  "$PROFILE" "$OUTPUT_DIR" "$command_argv_sha"
