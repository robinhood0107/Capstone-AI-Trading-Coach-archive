#!/usr/bin/bash
set -euo pipefail

usage() {
  echo "usage: run-profile-qualification.sh --plan PLAN --profiles baseline-o0-fasm,optimized-o2-fasm --enforce-order-plan --output-dir ABSOLUTE_NEW_DIRECTORY" >&2
  exit 64
}

[[ "$#" -eq 7 \
  && "$1" == "--plan" \
  && "$2" == /* \
  && "$3" == "--profiles" \
  && "$4" == "baseline-o0-fasm,optimized-o2-fasm" \
  && "$5" == "--enforce-order-plan" \
  && "$6" == "--output-dir" \
  && "$7" == /* ]] || usage

if [[ -v STACK_YAML || -v STACK_ROOT || -v STACK_OPTS || -v STACK_CONFIG ]]; then
  echo "ambient Stack configuration is forbidden" >&2
  exit 64
fi
if [[ -e "$7" || -L "$7" ]]; then
  echo "qualification evidence output must be a new path" >&2
  exit 73
fi

BENCHMARK_PYTHON="${S1_4X_BENCHMARK_PYTHON_BIN:?S1_4X_BENCHMARK_PYTHON_BIN is required}"
BENCHMARK_PYTHON_SHA256="${S1_4X_BENCHMARK_PYTHON_SHA256:?S1_4X_BENCHMARK_PYTHON_SHA256 is required}"
BENCHMARK_PYTHON_PINNED_FD_PATH="${S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH:?S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH is required}"
LARGE_FIXTURE_ROOT="${S1_4X_LARGE_FIXTURE_ROOT:?S1_4X_LARGE_FIXTURE_ROOT is required}"

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

SCRIPT_PATH="$(readlink -f "$0")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/..")"
"$HASKELL_ROOT/tools/assert-toolchain.sh" >/dev/null

if [[ "$BENCHMARK_PYTHON" != /* \
  || "$BENCHMARK_PYTHON" == *":"* \
  || "$BENCHMARK_PYTHON" == *"|"* \
  || "$BENCHMARK_PYTHON" == *$'\n'* \
  || "$BENCHMARK_PYTHON" == *"//"* \
  || "$BENCHMARK_PYTHON" == *"/./"* \
  || "$BENCHMARK_PYTHON" == *"/../"* \
  || "$BENCHMARK_PYTHON" == */. \
  || "$BENCHMARK_PYTHON" == */.. ]]; then
  echo "benchmark Python source path layout is unsafe" >&2
  exit 69
fi
if [[ ! "$BENCHMARK_PYTHON_PINNED_FD_PATH" =~ ^/proc/self/fd/([3-9]|[1-9][0-9]+)$ \
  || ! -f "$BENCHMARK_PYTHON_PINNED_FD_PATH" \
  || ! -x "$BENCHMARK_PYTHON_PINNED_FD_PATH" \
  || ! "$BENCHMARK_PYTHON_SHA256" =~ ^[0-9a-f]{64}$ \
  || "$(/usr/bin/sha256sum "$BENCHMARK_PYTHON_PINNED_FD_PATH" | /usr/bin/awk '{print $1}')" \
    != "$BENCHMARK_PYTHON_SHA256" ]]; then
  echo "benchmark Python pinned FD identity is unsafe" >&2
  exit 69
fi
if [[ "$LARGE_FIXTURE_ROOT" != /* \
  || ! -d "$LARGE_FIXTURE_ROOT" \
  || -L "$LARGE_FIXTURE_ROOT" \
  || ! -d "$LARGE_FIXTURE_ROOT/large" \
  || -L "$LARGE_FIXTURE_ROOT/large" \
  || "$(/usr/bin/realpath -e -- "$LARGE_FIXTURE_ROOT")" \
    != "$LARGE_FIXTURE_ROOT" ]]; then
  echo "shared large fixture root identity is unsafe" >&2
  exit 69
fi

exec "$BENCHMARK_PYTHON_PINNED_FD_PATH" "$HASKELL_ROOT/tools/profile_workflow.py" qualification \
  --plan "$2" \
  --profiles "$4" \
  --enforce-order-plan \
  --output-dir "$7"
