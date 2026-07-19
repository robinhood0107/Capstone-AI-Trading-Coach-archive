#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
PLAN="${S1_4X_BENCHMARK_PLAN:-$S1_ROOT/benchmarks/benchmark-plan.v1.json}"
QUALIFICATION="${S1_4X_SCALA_QUALIFICATION_RESULT:-}"
CORRECTNESS_ROOT="${S1_4X_SCALA_CORRECTNESS_ROOT:-}"
OUTPUT="${S1_4X_SCALA_SELECTED_PROFILE_RESULT:-}"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:?set exact Scala CLI 1.15.0 binary path}"
JVM_ALLOWLIST="${S1_4X_SCALA_JVM_ALLOWLIST_RESULT:?set accepted smoke-produced JVM allowlist result path}"
CHECK=false

usage() {
  printf 'usage: %s [--check] [--plan <absolute-json>] --qualification <absolute-json> --correctness-root <absolute-directory> --output <absolute-json>\n' \
    "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --check)
      CHECK=true
      shift
      ;;
    --plan)
      (($# >= 2)) || usage
      PLAN="$2"
      shift 2
      ;;
    --qualification)
      (($# >= 2)) || usage
      QUALIFICATION="$2"
      shift 2
      ;;
    --correctness-root)
      (($# >= 2)) || usage
      CORRECTNESS_ROOT="$2"
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

[[ "$PLAN" == /* && -f "$PLAN" && ! -L "$PLAN" ]] || usage
[[ "$QUALIFICATION" == /* && -f "$QUALIFICATION" && ! -L "$QUALIFICATION" ]] || usage
[[ "$CORRECTNESS_ROOT" == /* && -d "$CORRECTNESS_ROOT" && ! -L "$CORRECTNESS_ROOT" ]] || usage
[[ "$SCALA_CLI" == /* && -x "$SCALA_CLI" && ! -L "$SCALA_CLI" ]] || usage
[[ "$JVM_ALLOWLIST" == /* && -f "$JVM_ALLOWLIST" && ! -L "$JVM_ALLOWLIST" ]] ||
  usage
[[ "$OUTPUT" == /* && ! -L "$OUTPUT" ]] || usage
if [[ "$CHECK" == true ]]; then
  [[ -f "$OUTPUT" ]] || usage
else
  [[ ! -e "$OUTPUT" ]] || usage
fi

"$SCALA_ROOT/tools/test-source-input-manifest.sh" >/dev/null

command=(
  python3 -E -s -S "$SCALA_ROOT/tools/t3_evidence.py" select-profile
  --plan "$PLAN"
  --qualification "$QUALIFICATION"
  --qualification-artifact-root "$(dirname -- "$QUALIFICATION")"
  --correctness-root "$CORRECTNESS_ROOT"
  --compiler-profiles "$SCALA_ROOT/compiler-profiles.v1.json"
  --selected-profile-source "$SCALA_ROOT/selected-profile.scala"
  --source-manifest "$SCALA_ROOT/source-inputs.v1.json"
  --scala-root "$SCALA_ROOT"
  --scala-cli-bin "$SCALA_CLI"
  --toolchain-lock "$SCALA_ROOT/toolchain-lock.v1.json"
  --merged-provenance "$S1_ROOT/contract/toolchain-provenance.v1.json"
  --capability-smoke-plan "$S1_ROOT/contract/capability-smoke-plan.v1.json"
  --jvm-allowlist "$JVM_ALLOWLIST"
)

if [[ "$CHECK" == false ]]; then
  "${command[@]}" --output "$OUTPUT"
else
  CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
  mkdir -p "$CACHE_ROOT/scratch"
  temporary="$(mktemp -d -p "$CACHE_ROOT/scratch" s1-4x-scala-selector.XXXXXXXX)"
  cleanup() {
    [[ "$temporary" == "$CACHE_ROOT"/scratch/s1-4x-scala-selector.* ]] || {
      printf 'refusing unsafe selector cleanup: %s\n' "$temporary" >&2
      exit 1
    }
    rm -rf -- "$temporary"
  }
  trap cleanup EXIT
  "${command[@]}" --output "$temporary/recomputed.json" >/dev/null
  cmp --silent "$temporary/recomputed.json" "$OUTPUT" || {
    printf 'selected profile result differs from deterministic recomputation\n' >&2
    exit 1
  }
fi

printf 'SCALA_PROFILE_SELECTOR_PASS check=%s resultSha256=%s\n' \
  "$CHECK" "$(sha256sum "$OUTPUT" | awk '{print $1}')"
