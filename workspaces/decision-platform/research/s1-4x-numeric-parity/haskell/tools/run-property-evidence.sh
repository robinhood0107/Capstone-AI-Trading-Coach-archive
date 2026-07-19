#!/usr/bin/env bash
set -euo pipefail
export LC_ALL="C.UTF-8"

if [[ "$#" -ne 2 || "$1" != "--output-dir" || "$2" != /* ]]; then
  echo "usage: run-property-evidence.sh --output-dir ABSOLUTE_NEW_DIRECTORY" >&2
  exit 64
fi

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/..")"
NUMERIC_ROOT="$(realpath "${HASKELL_ROOT}/..")"
source "$HASKELL_ROOT/tools/python-runtime.sh"
s1_4x_pin_benchmark_python
OUTPUT_DIRECTORY="$(realpath -m "$2")"
OUTPUT_PARENT="${OUTPUT_DIRECTORY%/*}"

if [[ -e "$OUTPUT_DIRECTORY" || -L "$OUTPUT_DIRECTORY" ]]; then
  echo "property evidence output already exists" >&2
  exit 73
fi
if [[ ! -d "$OUTPUT_PARENT" || -L "$OUTPUT_PARENT" ]]; then
  echo "property evidence output parent must be an existing real directory" >&2
  exit 73
fi

if [[ -n "${STACK_YAML+x}" \
  || -n "${STACK_ROOT+x}" \
  || -n "${STACK_OPTS+x}" \
  || -n "${STACK_CONFIG+x}" ]]; then
  echo "ambient Stack configuration is forbidden" >&2
  exit 64
fi

"$HASKELL_ROOT/tools/assert-toolchain.sh" >/dev/null
STACK_CONFIGURED="${S1_4X_STACK_BIN:?S1_4X_STACK_BIN readiness path is required}"
GHC_CONFIGURED="${S1_4X_AUTHORITATIVE_GHC_BIN:?S1_4X_AUTHORITATIVE_GHC_BIN readiness path is required}"
CACHE_ROOT_CONFIGURED="${S1_4X_CACHE_ROOT:?S1_4X_CACHE_ROOT readiness path is required}"
STACK_BIN="$(readlink -f "$STACK_CONFIGURED")"
GHC_BIN="$(readlink -f "$GHC_CONFIGURED")"
SOURCE_MANIFEST="$HASKELL_ROOT/source-inputs.v1.json"
SELECTED_PROFILE="$HASKELL_ROOT/selected-profile.v1.json"
QUALIFICATION_PLAN="$NUMERIC_ROOT/benchmarks/benchmark-plan.v1.json"

if [[ "$CACHE_ROOT_CONFIGURED" != /* \
  || ! -d "$CACHE_ROOT_CONFIGURED" \
  || -L "$CACHE_ROOT_CONFIGURED" \
  || "$(readlink -f "$CACHE_ROOT_CONFIGURED")" != "$CACHE_ROOT_CONFIGURED" ]]; then
  echo "S1_4X_CACHE_ROOT must be an absolute existing real directory" >&2
  exit 69
fi
CACHE_ROOT="$CACHE_ROOT_CONFIGURED"
STACK_ROOT_PATH="$(
  s1_4x_run_benchmark_python "$HASKELL_ROOT/tools/profile_workflow.py" \
    isolated-stack-root \
    --cache-root "$CACHE_ROOT" \
    --purpose property \
    --output "$OUTPUT_DIRECTORY"
)"
if [[ "$STACK_ROOT_PATH" != "$CACHE_ROOT"/stack-root-property-* \
  || -e "$STACK_ROOT_PATH" \
  || -L "$STACK_ROOT_PATH" ]]; then
  echo "output-bound property Stack root must be new" >&2
  exit 73
fi
mkdir -m 700 -- "$STACK_ROOT_PATH"
STACK_WORK_DIR=".stack-work-s1-4x-${STACK_ROOT_PATH##*/}"

if [[ ! -x "$STACK_BIN" || ! -x "$GHC_BIN" ]]; then
  echo "required Haskell toolchain executable is missing" >&2
  exit 69
fi
if [[ "$("$STACK_BIN" --numeric-version)" != "3.11.1" ]]; then
  echo "Stack version mismatch" >&2
  exit 69
fi
if [[ "$("$GHC_BIN" --numeric-version)" != "9.10.3" ]]; then
  echo "GHC baseline version mismatch" >&2
  exit 69
fi

validate_execution_closure() {
  local phase="$1"
  local source_result
  local profile_result
  local closure_result
  source_result="$(
    s1_4x_run_benchmark_python "$HASKELL_ROOT/tools/haskell_evidence.py" source-inputs \
      --haskell-root "$HASKELL_ROOT" \
      --manifest "$SOURCE_MANIFEST"
  )"
  profile_result="$(
    s1_4x_run_benchmark_python "$HASKELL_ROOT/tools/haskell_evidence.py" selected-profile \
      --haskell-root "$HASKELL_ROOT" \
      --profile "$SELECTED_PROFILE" \
      --qualification-plan "$QUALIFICATION_PLAN"
  )"
  closure_result="$(
    s1_4x_run_benchmark_python "$HASKELL_ROOT/tools/haskell_evidence.py" property-closure \
      --haskell-root "$HASKELL_ROOT"
  )"
  s1_4x_run_benchmark_python - "$phase" "$source_result" "$profile_result" "$closure_result" <<'PY'
import json
import sys

phase = sys.argv[1]
source = json.loads(sys.argv[2])
profile = json.loads(sys.argv[3])
closure = json.loads(sys.argv[4])
if any(value.get("status") != "PASS" for value in (source, profile, closure)):
    raise SystemExit(f"{phase} property closure validation did not pass")
print(source["manifestSha256"])
print(profile["profileId"])
print(" ".join(profile["ghcOptions"]))
print(profile["optionsSha256"])
print(profile["profileSha256"])
print(profile["sourceTreeSha256"])
print(closure["propertyClosureSha256"])
PY
}

mapfile -t PRE_BUILD_FIELDS < <(validate_execution_closure "pre-build")
[[ "${#PRE_BUILD_FIELDS[@]}" -eq 7 ]] || {
  echo "pre-build property closure field count drift" >&2
  exit 2
}
EXPECTED_SOURCE_MANIFEST_SHA256="${PRE_BUILD_FIELDS[0]}"
PROFILE_ID="${PRE_BUILD_FIELDS[1]}"
PROFILE_GHC_OPTIONS="${PRE_BUILD_FIELDS[2]}"
PROFILE_OPTIONS_SHA256="${PRE_BUILD_FIELDS[3]}"
SELECTED_PROFILE_SHA256="${PRE_BUILD_FIELDS[4]}"
EXPECTED_SOURCE_TREE_SHA256="${PRE_BUILD_FIELDS[5]}"
EXPECTED_PROPERTY_CLOSURE_SHA256="${PRE_BUILD_FIELDS[6]}"
case "$PROFILE_ID:$PROFILE_GHC_OPTIONS" in
  "baseline-o0-fasm:-O0 -fasm" | "optimized-o2-fasm:-O2 -fasm") ;;
  *)
    echo "selected profile options cannot issue property evidence" >&2
    exit 2
    ;;
esac

export PATH="${GHC_BIN%/*}:${STACK_BIN%/*}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
STACK_ARGUMENTS=(
  --stack-root "$STACK_ROOT_PATH"
  --work-dir "$STACK_WORK_DIR"
  --system-ghc
  --no-install-ghc
  --stack-yaml "${HASKELL_ROOT}/stack.yaml"
  --hpack-force
)

BUILD_ARGV_SHA256="$(
  s1_4x_run_benchmark_python - \
    "$STACK_BIN" \
    "${STACK_ARGUMENTS[@]}" \
    build \
    --test \
    --no-run-tests \
    --no-terminal \
    --ghc-options \
    "$PROFILE_GHC_OPTIONS" <<'PY'
import hashlib
import json
import sys

payload = json.dumps(
    sys.argv[1:],
    ensure_ascii=False,
    separators=(",", ":"),
).encode()
print(hashlib.sha256(payload).hexdigest())
PY
)"
"$STACK_BIN" "${STACK_ARGUMENTS[@]}" build \
  --test \
  --no-run-tests \
  --no-terminal \
  --ghc-options "$PROFILE_GHC_OPTIONS"
mapfile -t POST_BUILD_FIELDS < <(validate_execution_closure "post-build")
if [[ "${#POST_BUILD_FIELDS[@]}" -ne 7 \
  || "${POST_BUILD_FIELDS[*]}" != "${PRE_BUILD_FIELDS[*]}" ]]; then
  echo "post-build property closure drift" >&2
  exit 2
fi
DIST_DIRECTORY="$("$STACK_BIN" "${STACK_ARGUMENTS[@]}" path --dist-dir)"
TEST_RUNNER="${HASKELL_ROOT}/${DIST_DIRECTORY}/build/s1-4x-haskell-test/s1-4x-haskell-test"
if [[ ! -x "$TEST_RUNNER" ]]; then
  echo "compiled property test runner is missing" >&2
  exit 70
fi

mkdir -m 700 "$OUTPUT_DIRECTORY"
"$TEST_RUNNER" \
  --s1-4x-property-evidence \
  "$OUTPUT_DIRECTORY" \
  "$HASKELL_ROOT" \
  "${NUMERIC_ROOT}/contract/property-plan.v1.json" \
  "${NUMERIC_ROOT}/contract/fixtures/property/property-seeds.v1.json" \
  "${NUMERIC_ROOT}/contract/function-registry.v1.json" \
  "${NUMERIC_ROOT}/contract/error-registry.v1.json" \
  "$SCRIPT_PATH" \
  "$SELECTED_PROFILE" \
  "$SOURCE_MANIFEST" \
  "$PROFILE_ID" \
  "$PROFILE_GHC_OPTIONS" \
  "$PROFILE_OPTIONS_SHA256" \
  "$BUILD_ARGV_SHA256" \
  "$SELECTED_PROFILE_SHA256" \
  "$EXPECTED_SOURCE_MANIFEST_SHA256" \
  "$EXPECTED_SOURCE_TREE_SHA256" \
  "$EXPECTED_PROPERTY_CLOSURE_SHA256"
mapfile -t POST_RUN_FIELDS < <(validate_execution_closure "post-run")
if [[ "${#POST_RUN_FIELDS[@]}" -ne 7 \
  || "${POST_RUN_FIELDS[*]}" != "${PRE_BUILD_FIELDS[*]}" ]]; then
  echo "post-run property closure drift" >&2
  exit 2
fi
