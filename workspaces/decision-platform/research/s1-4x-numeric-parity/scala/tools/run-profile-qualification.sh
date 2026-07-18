#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
UV_BIN="${S1_4X_UV_BIN:?set exact uv 0.11.26 binary path}"
JVM_ALLOWLIST="${S1_4X_SCALA_JVM_ALLOWLIST_RESULT:?set accepted smoke-produced JVM allowlist result path}"
PLAN=""
PROFILES=""
OUTPUT_DIR=""
ENFORCE_ORDER=false

usage() {
  printf 'usage: %s --plan <absolute-json> --profiles A,B,C --enforce-order-plan --output-dir <new-absolute-directory>\n' \
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
    --profiles)
      (($# >= 2)) || usage
      PROFILES="$2"
      shift 2
      ;;
    --enforce-order-plan)
      ENFORCE_ORDER=true
      shift
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
[[ "$JVM_ALLOWLIST" == /* && -f "$JVM_ALLOWLIST" && ! -L "$JVM_ALLOWLIST" ]] ||
  usage
[[ "$PROFILES" == "A,B,C" && "$ENFORCE_ORDER" == true ]] || usage
[[ "$OUTPUT_DIR" == /* && ! -e "$OUTPUT_DIR" && ! -L "$OUTPUT_DIR" ]] || usage
RESULT_DIR="${RESULT_DIR:?set RESULT_DIR to the absolute correctness result directory}"
CORRECTNESS_ROOT="$RESULT_DIR/scala/profiles"
[[ "$CORRECTNESS_ROOT" == /* && -d "$CORRECTNESS_ROOT" ]] || usage

"$SCALA_ROOT/tools/assert-toolchain.sh"

# Frozen plan의 profileOrderBlocks와 hostValidityBeforeEachProfileBlock=true를 Python
# orchestrator가 다시 검증하고 한 번에 JMH process 하나만 실행한다.
python3 "$SCALA_ROOT/tools/run_profile_qualification.py" \
  --plan "$PLAN" \
  --scala-root "$SCALA_ROOT" \
  --correctness-root "$CORRECTNESS_ROOT" \
  --jmh-runner "$SCALA_ROOT/tools/run-jmh-native-smoke.sh" \
  --host-validator "$S1_ROOT/oracle/validate_environment.py" \
  --uv "$(readlink -f -- "$UV_BIN")" \
  --jvm-allowlist "$JVM_ALLOWLIST" \
  --output-dir "$OUTPUT_DIR"
