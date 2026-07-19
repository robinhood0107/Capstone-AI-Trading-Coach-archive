#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: run-candidate.sh --request ABSOLUTE_FILE --fixture-root ABSOLUTE_DIRECTORY --output ABSOLUTE_NEW_FILE" >&2
  exit 64
}

[[ "$#" -eq 6 \
  && "$1" == "--request" \
  && "$3" == "--fixture-root" \
  && "$5" == "--output" \
  && "$2" == /* \
  && "$4" == /* \
  && "$6" == /* ]] || usage

REQUEST_PATH="$2"
FIXTURE_ROOT="$4"
OUTPUT_PATH="$6"

if [[ -n "${STACK_YAML+x}" \
  || -n "${STACK_ROOT+x}" \
  || -n "${STACK_OPTS+x}" \
  || -n "${STACK_CONFIG+x}" ]]; then
  echo "ambient Stack configuration is forbidden" >&2
  exit 64
fi

# Interpreter, linker, Git, Cabal, Hpack hook이 sealed candidate child로 유입되지 않게 한다.
while IFS= read -r environment_name; do
  case "$environment_name" in
    BASH_ENV | ENV | PYTHONPATH | PYTHONHOME | PYTHON* | VIRTUAL_ENV | \
      JAVA_TOOL_OPTIONS | JDK_JAVA_OPTIONS | _JAVA_OPTIONS | GIT_* | LD_* | \
      GHCRTS | GHC_ENVIRONMENT | HASKELL_PACKAGE_SANDBOX | CABAL_CONFIG | \
      HPACK_CONFIG)
      unset "$environment_name"
      ;;
  esac
done < <(compgen -e)
export PATH="/usr/bin:/bin"
export LC_ALL="C"
export TZ="UTC"

SCRIPT_PATH="$(/usr/bin/readlink -f -- "${BASH_SOURCE[0]}")"
HASKELL_ROOT="$(/usr/bin/realpath -- "${SCRIPT_PATH%/*}/..")"
NUMERIC_ROOT="$(/usr/bin/realpath -- "$HASKELL_ROOT/..")"
PROFILE_PATH="$HASKELL_ROOT/selected-profile.v1.json"
SOURCE_MANIFEST="$HASKELL_ROOT/source-inputs.v1.json"
QUALIFICATION_PLAN="$NUMERIC_ROOT/benchmarks/benchmark-plan.v1.json"
PROFILE_HELPER="$HASKELL_ROOT/tools/profile_workflow.py"
STACK_YAML_PATH="$HASKELL_ROOT/stack.yaml"

GHCUP_BIN="${S1_4X_GHCUP_BIN:?S1_4X_GHCUP_BIN readiness path is required}"
STACK_BIN="${S1_4X_STACK_BIN:?S1_4X_STACK_BIN readiness path is required}"
CACHE_ROOT="${S1_4X_CACHE_ROOT:?S1_4X_CACHE_ROOT readiness path is required}"

if [[ ! -f "$REQUEST_PATH" \
  || -L "$REQUEST_PATH" \
  || "$(/usr/bin/realpath -e -- "$REQUEST_PATH")" != "$REQUEST_PATH" \
  || ! -d "$FIXTURE_ROOT" \
  || -L "$FIXTURE_ROOT" \
  || "$(/usr/bin/realpath -e -- "$FIXTURE_ROOT")" != "$FIXTURE_ROOT" ]]; then
  echo "candidate request and fixture root must be absolute real inputs" >&2
  exit 64
fi
OUTPUT_PARENT="${OUTPUT_PATH%/*}"
if [[ -e "$OUTPUT_PATH" \
  || -L "$OUTPUT_PATH" \
  || ! -d "$OUTPUT_PARENT" \
  || -L "$OUTPUT_PARENT" \
  || "$(/usr/bin/realpath -e -- "$OUTPUT_PARENT")" != "$OUTPUT_PARENT" ]]; then
  echo "candidate output must be a new file under an absolute real directory" >&2
  exit 73
fi
if [[ "$CACHE_ROOT" != /* \
  || ! -d "$CACHE_ROOT" \
  || -L "$CACHE_ROOT" \
  || "$(/usr/bin/realpath -e -- "$CACHE_ROOT")" != "$CACHE_ROOT" ]]; then
  echo "S1_4X_CACHE_ROOT must be an absolute existing real directory" >&2
  exit 69
fi

"$HASKELL_ROOT/tools/assert-toolchain.sh" >/dev/null
PROFILE_ID="$(
  /usr/bin/python3 "$PROFILE_HELPER" candidate-runtime \
    --profile "$PROFILE_PATH" \
    --source-manifest "$SOURCE_MANIFEST" \
    --qualification-plan "$QUALIFICATION_PLAN"
)"
case "$PROFILE_ID" in
  baseline-o0-fasm)
    PROFILE_GHC_OPTIONS="-O0 -fasm"
    ;;
  optimized-o2-fasm)
    PROFILE_GHC_OPTIONS="-O2 -fasm"
    ;;
  *)
    echo "selected profile cannot issue an integration candidate" >&2
    exit 2
    ;;
esac

STACK_ROOT_PATH="$(
  /usr/bin/python3 "$PROFILE_HELPER" candidate-stack-root \
    --cache-root "$CACHE_ROOT" \
    --output "$OUTPUT_PATH"
)"
if [[ "$STACK_ROOT_PATH" != "$CACHE_ROOT"/stack-root-candidate-* \
  || -e "$STACK_ROOT_PATH" \
  || -L "$STACK_ROOT_PATH" ]]; then
  echo "output-bound candidate Stack root is not new" >&2
  exit 73
fi
/usr/bin/mkdir -m 700 -- "$STACK_ROOT_PATH"
STACK_WORK_DIR=".stack-work/s1-4x/${STACK_ROOT_PATH##*/}"
STACK_STDOUT="$STACK_ROOT_PATH/candidate.stdout"
STACK_STDERR="$STACK_ROOT_PATH/candidate.stderr"

set +e
"$GHCUP_BIN" \
  --offline run --quick \
  --ghc 9.10.3 \
  --stack 3.11.1 \
  -- \
  "$STACK_BIN" \
  --stack-root "$STACK_ROOT_PATH" \
  --work-dir "$STACK_WORK_DIR" \
  --stack-yaml "$STACK_YAML_PATH" \
  --no-terminal \
  --color never \
  --system-ghc \
  --no-install-ghc \
  --hpack-force \
  --silent \
  run \
  --ghc-options "$PROFILE_GHC_OPTIONS" \
  -- \
  --request "$REQUEST_PATH" \
  --fixture-root "$FIXTURE_ROOT" \
  --output "$OUTPUT_PATH" \
  >"$STACK_STDOUT" \
  2>"$STACK_STDERR"
candidate_status="$?"
set -e

if [[ -s "$STACK_STDOUT" ]]; then
  echo "candidate process wrote unexpected stdout" >&2
  exit 70
fi
if [[ -s "$STACK_STDERR" ]]; then
  /usr/bin/cat -- "$STACK_STDERR" >&2
fi
if [[ "$candidate_status" -ne 0 ]]; then
  exit "$candidate_status"
fi
if [[ -s "$STACK_STDERR" \
  || ! -f "$OUTPUT_PATH" \
  || -L "$OUTPUT_PATH" \
  || "$(/usr/bin/realpath -e -- "$OUTPUT_PATH")" != "$OUTPUT_PATH" ]]; then
  echo "candidate success output contract failed" >&2
  exit 70
fi
