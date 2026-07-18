#!/usr/bin/env bash
set -euo pipefail

# Frozen runner가 전달한 shell-free argv를 research uv env의 한 Python process에 위임한다.
ROOT="$(git rev-parse --show-toplevel)"
S1_4X="$ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity"
PRODUCTION="$ROOT/workspaces/decision-platform/python-services"
RESEARCH="$ROOT/workspaces/decision-platform/research/s1-4r-jax-risk"
UV_BIN="${S1_4X_UV_BIN:-$(command -v uv)}"

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

export UV_PYTHON=3.12.13
export JAX_PLATFORMS=cpu
export JAX_ENABLE_X64=1

exec "$UV_BIN" run --frozen --project "$PROJECT" \
  python "$S1_4X/integration/python_benchmark_block.py" \
  --repo-root "$ROOT" \
  "$@"
