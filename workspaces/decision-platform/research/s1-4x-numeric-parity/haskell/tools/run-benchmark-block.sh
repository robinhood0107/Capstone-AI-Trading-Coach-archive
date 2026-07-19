#!/usr/bin/bash
set -euo pipefail

usage() {
  echo "usage: run-benchmark-block.sh --plan PLAN --block-dir BLOCK_DIR --qualification QUALIFICATION --boundary haskell --selector SELECTOR --family FAMILY --rotation ROTATION --outer-repetition N --run-id RUN_ID --benchmark-subject-commit COMMIT" >&2
  exit 64
}

[[ "$#" -eq 20 ]] || usage
if [[ "$1" != "--plan" \
  || "$3" != "--block-dir" \
  || "$5" != "--qualification" \
  || "$7" != "--boundary" \
  || "$9" != "--selector" \
  || "${11}" != "--family" \
  || "${13}" != "--rotation" \
  || "${15}" != "--outer-repetition" \
  || "${17}" != "--run-id" \
  || "${19}" != "--benchmark-subject-commit" ]]; then
  usage
fi

if [[ -v STACK_YAML \
  || -v STACK_ROOT \
  || -v STACK_OPTS \
  || -v STACK_CONFIG ]]; then
  echo "ambient Stack configuration is forbidden" >&2
  exit 64
fi

# Sealed wrapper 밖에서 interpreter, Git, JVM 동작을 바꾸는 ambient hook은 상속하지 않는다.
while IFS= read -r environment_name; do
  case "$environment_name" in
    BASH_ENV | ENV | PYTHONPATH | PYTHONHOME | PYTHON* | VIRTUAL_ENV | \
      JAVA_TOOL_OPTIONS | \
      JDK_JAVA_OPTIONS | _JAVA_OPTIONS | GIT_* | LD_* | GHCRTS | \
      GHC_ENVIRONMENT | HASKELL_PACKAGE_SANDBOX | CABAL_CONFIG | HPACK_CONFIG)
      unset "$environment_name"
      ;;
  esac
done < <(compgen -e)
export PATH="/usr/bin:/bin"
export LC_ALL="C"
export TZ="UTC"

REPO_ROOT="$(/usr/bin/git -C "$PWD" rev-parse --show-toplevel)"
if [[ "$REPO_ROOT" != /* || "$(/usr/bin/pwd -P)" != "$REPO_ROOT" ]]; then
  echo "benchmark wrapper must run from the repository root" >&2
  exit 64
fi

HASKELL_ROOT="$REPO_ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity/haskell"
HELPER="$HASKELL_ROOT/tools/haskell_benchmark_block.py"
source "$HASKELL_ROOT/tools/python-runtime.sh"
s1_4x_pin_benchmark_python
BENCHMARK_PYTHON="${S1_4X_BENCHMARK_PYTHON_BIN:?S1_4X_BENCHMARK_PYTHON_BIN is required}"
BENCHMARK_PYTHON_SHA256="${S1_4X_BENCHMARK_PYTHON_SHA256:?S1_4X_BENCHMARK_PYTHON_SHA256 is required}"
BENCHMARK_PYTHON_PINNED_FD_PATH="${S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH:?S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH is required}"
GHCUP_BIN="${S1_4X_GHCUP_BIN:?S1_4X_GHCUP_BIN is required}"
GHCUP_SHA256="${S1_4X_GHCUP_SHA256:?S1_4X_GHCUP_SHA256 is required}"
GHCUP_PINNED_FD_PATH="${S1_4X_GHCUP_PINNED_FD_PATH:?S1_4X_GHCUP_PINNED_FD_PATH is required}"
GHCUP_INSTALL_BASE_PREFIX="${GHCUP_INSTALL_BASE_PREFIX:?GHCUP_INSTALL_BASE_PREFIX is required}"
STACK_BIN="${S1_4X_STACK_BIN:?S1_4X_STACK_BIN is required}"
STACK_SHA256="${S1_4X_STACK_SHA256:?S1_4X_STACK_SHA256 is required}"
STACK_PINNED_FD_PATH="${S1_4X_STACK_PINNED_FD_PATH:?S1_4X_STACK_PINNED_FD_PATH is required}"
AUTHORITATIVE_GHC_BIN="${S1_4X_AUTHORITATIVE_GHC_BIN:?S1_4X_AUTHORITATIVE_GHC_BIN is required}"
AUTHORITATIVE_GHC_SHA256="${S1_4X_AUTHORITATIVE_GHC_SHA256:?S1_4X_AUTHORITATIVE_GHC_SHA256 is required}"
AUTHORITATIVE_GHC_PINNED_FD_PATH="${S1_4X_AUTHORITATIVE_GHC_PINNED_FD_PATH:?S1_4X_AUTHORITATIVE_GHC_PINNED_FD_PATH is required}"
LATEST_GHC_BIN="${S1_4X_LATEST_GHC_BIN:?S1_4X_LATEST_GHC_BIN is required}"
LATEST_GHC_SHA256="${S1_4X_LATEST_GHC_SHA256:?S1_4X_LATEST_GHC_SHA256 is required}"
LATEST_GHC_PINNED_FD_PATH="${S1_4X_LATEST_GHC_PINNED_FD_PATH:?S1_4X_LATEST_GHC_PINNED_FD_PATH is required}"
HLINT_BIN="${S1_4X_HLINT_BIN:?S1_4X_HLINT_BIN is required}"
HLINT_SHA256="${S1_4X_HLINT_SHA256:?S1_4X_HLINT_SHA256 is required}"
HLINT_PINNED_FD_PATH="${S1_4X_HLINT_PINNED_FD_PATH:?S1_4X_HLINT_PINNED_FD_PATH is required}"
STYLISH_BIN="${S1_4X_STYLISH_BIN:?S1_4X_STYLISH_BIN is required}"
STYLISH_SHA256="${S1_4X_STYLISH_SHA256:?S1_4X_STYLISH_SHA256 is required}"
STYLISH_PINNED_FD_PATH="${S1_4X_STYLISH_PINNED_FD_PATH:?S1_4X_STYLISH_PINNED_FD_PATH is required}"
BASELINE_CORRECTNESS="${S1_4X_HASKELL_BASELINE_CORRECTNESS:?S1_4X_HASKELL_BASELINE_CORRECTNESS is required}"
BASELINE_CORRECTNESS_SHA256="${S1_4X_HASKELL_BASELINE_CORRECTNESS_SHA256:?S1_4X_HASKELL_BASELINE_CORRECTNESS_SHA256 is required}"
BASELINE_CORRECTNESS_SOURCE_PATH="${S1_4X_HASKELL_BASELINE_CORRECTNESS_SOURCE_PATH:?S1_4X_HASKELL_BASELINE_CORRECTNESS_SOURCE_PATH is required}"
OPTIMIZED_CORRECTNESS="${S1_4X_HASKELL_OPTIMIZED_CORRECTNESS:?S1_4X_HASKELL_OPTIMIZED_CORRECTNESS is required}"
OPTIMIZED_CORRECTNESS_SHA256="${S1_4X_HASKELL_OPTIMIZED_CORRECTNESS_SHA256:?S1_4X_HASKELL_OPTIMIZED_CORRECTNESS_SHA256 is required}"
OPTIMIZED_CORRECTNESS_SOURCE_PATH="${S1_4X_HASKELL_OPTIMIZED_CORRECTNESS_SOURCE_PATH:?S1_4X_HASKELL_OPTIMIZED_CORRECTNESS_SOURCE_PATH is required}"
QUALIFICATION_ARTIFACT="${S1_4X_HASKELL_QUALIFICATION_ARTIFACT:?S1_4X_HASKELL_QUALIFICATION_ARTIFACT is required}"
QUALIFICATION_ARTIFACT_SHA256="${S1_4X_HASKELL_QUALIFICATION_ARTIFACT_SHA256:?S1_4X_HASKELL_QUALIFICATION_ARTIFACT_SHA256 is required}"
QUALIFICATION_ARTIFACT_SOURCE_PATH="${S1_4X_HASKELL_QUALIFICATION_ARTIFACT_SOURCE_PATH:?S1_4X_HASKELL_QUALIFICATION_ARTIFACT_SOURCE_PATH is required}"
CACHE_ROOT="${S1_4X_CACHE_ROOT:?S1_4X_CACHE_ROOT is required}"
LARGE_FIXTURE_ROOT="${S1_4X_LARGE_FIXTURE_ROOT:?S1_4X_LARGE_FIXTURE_ROOT is required}"

verify_source_path_layout() {
  local label="$1"
  local source_path="$2"
  if [[ "$source_path" != /* \
    || "$source_path" == *":"* \
    || "$source_path" == *"|"* \
    || "$source_path" == *$'\n'* \
    || "$source_path" == *"//"* \
    || "$source_path" == *"/./"* \
    || "$source_path" == *"/../"* \
    || "$source_path" == */. \
    || "$source_path" == */.. ]]; then
    echo "$label source path layout is unsafe" >&2
    exit 69
  fi
}

verify_pinned_object() {
  local label="$1"
  local pinned_fd_path="$2"
  local expected_sha256="$3"
  local kind="$4"
  if [[ ! "$pinned_fd_path" =~ ^/proc/self/fd/([3-9]|[1-9][0-9]+)$ \
    || ! -f "$pinned_fd_path" \
    || ! "$expected_sha256" =~ ^[0-9a-f]{64}$ \
    || ("$kind" == "executable" && ! -x "$pinned_fd_path") ]]; then
    echo "$label pinned FD identity is unsafe" >&2
    exit 69
  fi
  if [[ "$(/usr/bin/sha256sum "$pinned_fd_path" | /usr/bin/awk '{print $1}')" \
    != "$expected_sha256" ]]; then
    echo "$label pinned FD SHA-256 mismatch" >&2
    exit 69
  fi
}

verify_ghcup_install_base_prefix() {
  local prefix="$1"
  if [[ "$prefix" != /* \
    || ! -d "$prefix" \
    || -L "$prefix" \
    || "$(/usr/bin/realpath -e -- "$prefix")" != "$prefix" \
    || "$AUTHORITATIVE_GHC_BIN" \
      != "$prefix/.ghcup/ghc/9.10.3/bin/ghc-9.10.3" \
    || "$STACK_BIN" != "$prefix/.ghcup/stack/3.11.1/stack" ]]; then
    echo "GHCup install base prefix identity is unsafe" >&2
    exit 69
  fi
}

for tool_record in \
  "benchmark Python|$BENCHMARK_PYTHON|$BENCHMARK_PYTHON_PINNED_FD_PATH|$BENCHMARK_PYTHON_SHA256" \
  "GHCup|$GHCUP_BIN|$GHCUP_PINNED_FD_PATH|$GHCUP_SHA256" \
  "Stack|$STACK_BIN|$STACK_PINNED_FD_PATH|$STACK_SHA256" \
  "authoritative GHC|$AUTHORITATIVE_GHC_BIN|$AUTHORITATIVE_GHC_PINNED_FD_PATH|$AUTHORITATIVE_GHC_SHA256" \
  "compatibility GHC|$LATEST_GHC_BIN|$LATEST_GHC_PINNED_FD_PATH|$LATEST_GHC_SHA256" \
  "HLint|$HLINT_BIN|$HLINT_PINNED_FD_PATH|$HLINT_SHA256" \
  "stylish-haskell|$STYLISH_BIN|$STYLISH_PINNED_FD_PATH|$STYLISH_SHA256"; do
  IFS="|" read -r label source_path pinned_fd_path expected_sha256 <<<"$tool_record"
  verify_source_path_layout "$label" "$source_path"
  verify_pinned_object "$label" "$pinned_fd_path" "$expected_sha256" "executable"
done
for evidence_record in \
  "baseline correctness|$BASELINE_CORRECTNESS_SOURCE_PATH|$BASELINE_CORRECTNESS|$BASELINE_CORRECTNESS_SHA256" \
  "optimized correctness|$OPTIMIZED_CORRECTNESS_SOURCE_PATH|$OPTIMIZED_CORRECTNESS|$OPTIMIZED_CORRECTNESS_SHA256" \
  "qualification artifact|$QUALIFICATION_ARTIFACT_SOURCE_PATH|$QUALIFICATION_ARTIFACT|$QUALIFICATION_ARTIFACT_SHA256"; do
  IFS="|" read -r label source_path pinned_fd_path expected_sha256 <<<"$evidence_record"
  verify_source_path_layout "$label" "$source_path"
  verify_pinned_object "$label" "$pinned_fd_path" "$expected_sha256" "json"
done
verify_ghcup_install_base_prefix "$GHCUP_INSTALL_BASE_PREFIX"

if [[ ! -f "$HELPER" \
  || -L "$HELPER" \
  || "$CACHE_ROOT" != /* \
  || ! -d "$CACHE_ROOT" \
  || -L "$CACHE_ROOT" \
  || "$(/usr/bin/realpath -e -- "$CACHE_ROOT")" != "$CACHE_ROOT" \
  || "$LARGE_FIXTURE_ROOT" != /* \
  || ! -d "$LARGE_FIXTURE_ROOT" \
  || -L "$LARGE_FIXTURE_ROOT" \
  || ! -d "$LARGE_FIXTURE_ROOT/large" \
  || -L "$LARGE_FIXTURE_ROOT/large" \
  || "$(/usr/bin/realpath -e -- "$LARGE_FIXTURE_ROOT")" \
    != "$LARGE_FIXTURE_ROOT" ]]; then
  echo "benchmark helper/cache/large fixture root identity is unsafe" >&2
  exit 69
fi

exec /usr/bin/env -i \
  PATH="/usr/bin:/bin" \
  LC_ALL="C" \
  TZ="UTC" \
  HOME="/nonexistent" \
  GHCUP_INSTALL_BASE_PREFIX="$GHCUP_INSTALL_BASE_PREFIX" \
  S1_4X_BENCHMARK_PYTHON_BIN="$BENCHMARK_PYTHON" \
  S1_4X_BENCHMARK_PYTHON_SHA256="$BENCHMARK_PYTHON_SHA256" \
  S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH="$BENCHMARK_PYTHON_PINNED_FD_PATH" \
  S1_4X_GHCUP_BIN="$GHCUP_BIN" \
  S1_4X_GHCUP_SHA256="$GHCUP_SHA256" \
  S1_4X_GHCUP_PINNED_FD_PATH="$GHCUP_PINNED_FD_PATH" \
  S1_4X_STACK_BIN="$STACK_BIN" \
  S1_4X_STACK_SHA256="$STACK_SHA256" \
  S1_4X_STACK_PINNED_FD_PATH="$STACK_PINNED_FD_PATH" \
  S1_4X_AUTHORITATIVE_GHC_BIN="$AUTHORITATIVE_GHC_BIN" \
  S1_4X_AUTHORITATIVE_GHC_SHA256="$AUTHORITATIVE_GHC_SHA256" \
  S1_4X_AUTHORITATIVE_GHC_PINNED_FD_PATH="$AUTHORITATIVE_GHC_PINNED_FD_PATH" \
  S1_4X_LATEST_GHC_BIN="$LATEST_GHC_BIN" \
  S1_4X_LATEST_GHC_SHA256="$LATEST_GHC_SHA256" \
  S1_4X_LATEST_GHC_PINNED_FD_PATH="$LATEST_GHC_PINNED_FD_PATH" \
  S1_4X_HLINT_BIN="$HLINT_BIN" \
  S1_4X_HLINT_SHA256="$HLINT_SHA256" \
  S1_4X_HLINT_PINNED_FD_PATH="$HLINT_PINNED_FD_PATH" \
  S1_4X_STYLISH_BIN="$STYLISH_BIN" \
  S1_4X_STYLISH_SHA256="$STYLISH_SHA256" \
  S1_4X_STYLISH_PINNED_FD_PATH="$STYLISH_PINNED_FD_PATH" \
  S1_4X_HASKELL_BASELINE_CORRECTNESS="$BASELINE_CORRECTNESS" \
  S1_4X_HASKELL_BASELINE_CORRECTNESS_SHA256="$BASELINE_CORRECTNESS_SHA256" \
  S1_4X_HASKELL_BASELINE_CORRECTNESS_SOURCE_PATH="$BASELINE_CORRECTNESS_SOURCE_PATH" \
  S1_4X_HASKELL_OPTIMIZED_CORRECTNESS="$OPTIMIZED_CORRECTNESS" \
  S1_4X_HASKELL_OPTIMIZED_CORRECTNESS_SHA256="$OPTIMIZED_CORRECTNESS_SHA256" \
  S1_4X_HASKELL_OPTIMIZED_CORRECTNESS_SOURCE_PATH="$OPTIMIZED_CORRECTNESS_SOURCE_PATH" \
  S1_4X_HASKELL_QUALIFICATION_ARTIFACT="$QUALIFICATION_ARTIFACT" \
  S1_4X_HASKELL_QUALIFICATION_ARTIFACT_SHA256="$QUALIFICATION_ARTIFACT_SHA256" \
  S1_4X_HASKELL_QUALIFICATION_ARTIFACT_SOURCE_PATH="$QUALIFICATION_ARTIFACT_SOURCE_PATH" \
  S1_4X_CACHE_ROOT="$CACHE_ROOT" \
  S1_4X_LARGE_FIXTURE_ROOT="$LARGE_FIXTURE_ROOT" \
  s1_4x_run_benchmark_python "$HELPER" \
  --repo-root "$REPO_ROOT" \
  --plan "$2" \
  --block-dir "$4" \
  --qualification "$6" \
  --boundary "$8" \
  --selector "${10}" \
  --family "${12}" \
  --rotation "${14}" \
  --outer-repetition "${16}" \
  --run-id "${18}" \
  --benchmark-subject-commit "${20}"
