#!/usr/bin/bash
set -euo pipefail

# Frozen runner가 전달한 shell-free argv를 research uv env의 한 Python process에 위임한다.
unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME JAVA_TOOL_OPTIONS _JAVA_OPTIONS
unset JDK_JAVA_OPTIONS GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0
export PATH=/usr/bin:/bin
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_OPTIONAL_LOCKS=0
export GIT_TERMINAL_PROMPT=0
readonly GIT_BIN=/usr/bin/git
readonly UV_BIN="${S1_4X_UV_BIN:?S1_4X_UV_BIN is required}"
readonly PYTHON_BIN="${S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH:?S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH is required}"
readonly PYTHON_SHA256="${S1_4X_BENCHMARK_PYTHON_SHA256:?S1_4X_BENCHMARK_PYTHON_SHA256 is required}"
if [[ ! "$UV_BIN" =~ ^/proc/self/fd/[0-9]+$ ]]; then
  echo "UV executable must be inherited through a sealed fd" >&2
  exit 69
fi
if [[ ! "$PYTHON_BIN" =~ ^/proc/self/fd/[0-9]+$ \
  || ! "$PYTHON_SHA256" =~ ^[0-9a-f]{64}$ \
  || "$(/usr/bin/sha256sum "$PYTHON_BIN" | /usr/bin/awk '{print $1}')" \
    != "$PYTHON_SHA256" ]]; then
  echo "Python executable must be inherited through the declared sealed fd" >&2
  exit 69
fi

ROOT="$("$GIT_BIN" -c core.fsmonitor=false rev-parse --show-toplevel)"
S1_4X="$ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity"
PRODUCTION="$ROOT/workspaces/decision-platform/python-services"
RESEARCH="$ROOT/workspaces/decision-platform/research/s1-4r-jax-risk"

BOUNDARY=""
EXPECT_BOUNDARY=false
for argument in "$@"; do
  if test "$EXPECT_BOUNDARY" = true; then
    BOUNDARY="$argument"
    EXPECT_BOUNDARY=false
  elif test "$argument" = "--boundary"; then
    EXPECT_BOUNDARY=true
  fi
done
case "$BOUNDARY" in
  python-numpy-s1-4) PROJECT="$PRODUCTION" ;;
  python-numpy-s1-4r|python-jax-eager-s1-4r|python-jax-jit-s1-4r)
    PROJECT="$RESEARCH"
    ;;
  *) echo "unsupported Python benchmark boundary: $BOUNDARY" >&2; exit 64 ;;
esac

export UV_PYTHON="$PYTHON_BIN"
export JAX_PLATFORMS=cpu
export JAX_ENABLE_X64=1

exec "$UV_BIN" run --frozen --no-config --python "$PYTHON_BIN" \
  --project "$PROJECT" \
  python "$S1_4X/integration/python_benchmark_block.py" \
  --repo-root "$ROOT" \
  "$@"
