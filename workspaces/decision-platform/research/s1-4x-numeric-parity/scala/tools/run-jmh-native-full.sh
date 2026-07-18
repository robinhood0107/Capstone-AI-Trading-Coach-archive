#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PLAN=""
PROFILE=""
CASE_ID=""
JVM_ALLOWLIST=""
OUTPUT_DIR=""

usage() {
  printf 'usage: %s --plan <absolute-json> --profile A|B|C --case-id <id> --jvm-allowlist <absolute-json> --output-dir <new-absolute-block-directory/scala-jmh/new-case-directory>\n' \
    "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --plan)
      (($# >= 2)) || usage
      PLAN="$2"
      shift 2
      ;;
    --profile)
      (($# >= 2)) || usage
      PROFILE="$2"
      shift 2
      ;;
    --case-id)
      (($# >= 2)) || usage
      CASE_ID="$2"
      shift 2
      ;;
    --jvm-allowlist)
      (($# >= 2)) || usage
      JVM_ALLOWLIST="$2"
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

[[ "$PLAN" == /* && -f "$PLAN" && ! -L "$PLAN" ]] || usage
case "$PROFILE" in
  A | B | C) ;;
  *) usage ;;
esac
[[ "$CASE_ID" =~ ^[a-z0-9][a-z0-9._/-]{0,191}$ ]] || usage
[[ "$JVM_ALLOWLIST" == /* && -f "$JVM_ALLOWLIST" && ! -L "$JVM_ALLOWLIST" ]] ||
  usage
[[ "$OUTPUT_DIR" == /* && ! -e "$OUTPUT_DIR" && ! -L "$OUTPUT_DIR" ]] || usage
[[ "$(basename -- "$(dirname -- "$OUTPUT_DIR")")" == "scala-jmh" ]] || usage

"$SCALA_ROOT/tools/assert-selected-profile.sh" --benchmark-subject
selected_profile="$(
  jq -er '.selectedProfileId' "${S1_4X_SCALA_SELECTED_PROFILE_RESULT}"
)"
[[ "$PROFILE" == "$selected_profile" ]] || {
  printf 'full JMH profile is not the frozen selected profile: expected=%s actual=%s\n' \
    "$selected_profile" "$PROFILE" >&2
  exit 1
}

exec "$SCALA_ROOT/tools/run-jmh-native-smoke.sh" \
  --plan "$PLAN" \
  --profile "$PROFILE" \
  --case-id "$CASE_ID" \
  --mode full \
  --jvm-allowlist "$JVM_ALLOWLIST" \
  --output-dir "$OUTPUT_DIR"
