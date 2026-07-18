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
BENCHMARK_PYTHON="${S1_4X_BENCHMARK_PYTHON_BIN:?S1_4X_BENCHMARK_PYTHON_BIN is required}"
BENCHMARK_PYTHON_SHA256="${S1_4X_BENCHMARK_PYTHON_SHA256:?S1_4X_BENCHMARK_PYTHON_SHA256 is required}"
GHCUP_BIN="${S1_4X_GHCUP_BIN:?S1_4X_GHCUP_BIN is required}"
STACK_BIN="${S1_4X_STACK_BIN:?S1_4X_STACK_BIN is required}"
AUTHORITATIVE_GHC_BIN="${S1_4X_AUTHORITATIVE_GHC_BIN:?S1_4X_AUTHORITATIVE_GHC_BIN is required}"
LATEST_GHC_BIN="${S1_4X_LATEST_GHC_BIN:?S1_4X_LATEST_GHC_BIN is required}"
HLINT_BIN="${S1_4X_HLINT_BIN:?S1_4X_HLINT_BIN is required}"
STYLISH_BIN="${S1_4X_STYLISH_BIN:?S1_4X_STYLISH_BIN is required}"
RUNTIME_HOME="${HOME:?HOME is required}"

export S1_4X_GHCUP_SHA256="9ed5da5449b48043a0d17e767c05d2ef585e25a639bb934329496c6d2fad9cf8"
export S1_4X_STACK_SHA256="923dbd137756652c67b376e2447c655b87fcc373f4d104b5073bca913471ecbe"
GHCUP_SHA256="$S1_4X_GHCUP_SHA256"
STACK_SHA256="$S1_4X_STACK_SHA256"
AUTHORITATIVE_GHC_SHA256="d0c0dd79a1bcc5dce3c9e73613c1be51f61b78d5ef7c0970ffe9f142a90a5e2c"
LATEST_GHC_SHA256="ecfd54b4161699f574d2b163bdc817c54df08a08a310323e43b41ab5fc413ef1"
HLINT_SHA256="3ff3fb4b571876d668ddf4ad0245769c19a640283fabb0c2629038aa34197f62"
STYLISH_SHA256="385dc27bc2d0fb654e76ecadfb57bc0b7e1c58afe74f19923e20b696e6fe0d7b"

verify_executable() {
  local label="$1"
  local executable="$2"
  local expected_sha256="$3"
  if [[ "$executable" != /* \
    || ! -f "$executable" \
    || ! -x "$executable" \
    || -L "$executable" \
    || "$(/usr/bin/realpath -e -- "$executable")" != "$executable" ]]; then
    echo "$label identity is unsafe" >&2
    exit 69
  fi
  if [[ "$(/usr/bin/sha256sum "$executable" | /usr/bin/awk '{print $1}')" \
    != "$expected_sha256" ]]; then
    echo "$label SHA-256 mismatch" >&2
    exit 69
  fi
}

verify_executable "benchmark Python" "$BENCHMARK_PYTHON" "$BENCHMARK_PYTHON_SHA256"
verify_executable "GHCup" "$GHCUP_BIN" "$GHCUP_SHA256"
verify_executable "Stack" "$STACK_BIN" "$STACK_SHA256"
verify_executable "authoritative GHC" "$AUTHORITATIVE_GHC_BIN" "$AUTHORITATIVE_GHC_SHA256"
verify_executable "compatibility GHC" "$LATEST_GHC_BIN" "$LATEST_GHC_SHA256"
verify_executable "HLint" "$HLINT_BIN" "$HLINT_SHA256"
verify_executable "stylish-haskell" "$STYLISH_BIN" "$STYLISH_SHA256"

if [[ ! -f "$HELPER" \
  || -L "$HELPER" \
  || "$RUNTIME_HOME" != /* \
  || ! -d "$RUNTIME_HOME" \
  || -L "$RUNTIME_HOME" \
  || "$(/usr/bin/realpath -e -- "$RUNTIME_HOME")" != "$RUNTIME_HOME" ]]; then
  echo "benchmark helper/runtime home identity is unsafe" >&2
  exit 69
fi

exec /usr/bin/env -i \
  PATH="/usr/bin:/bin" \
  LC_ALL="C" \
  TZ="UTC" \
  HOME="$RUNTIME_HOME" \
  S1_4X_BENCHMARK_PYTHON_BIN="$BENCHMARK_PYTHON" \
  S1_4X_BENCHMARK_PYTHON_SHA256="$BENCHMARK_PYTHON_SHA256" \
  S1_4X_GHCUP_BIN="$GHCUP_BIN" \
  S1_4X_GHCUP_SHA256="$GHCUP_SHA256" \
  S1_4X_STACK_BIN="$STACK_BIN" \
  S1_4X_STACK_SHA256="$STACK_SHA256" \
  S1_4X_AUTHORITATIVE_GHC_BIN="$AUTHORITATIVE_GHC_BIN" \
  S1_4X_LATEST_GHC_BIN="$LATEST_GHC_BIN" \
  S1_4X_HLINT_BIN="$HLINT_BIN" \
  S1_4X_STYLISH_BIN="$STYLISH_BIN" \
  "$BENCHMARK_PYTHON" "$HELPER" \
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
