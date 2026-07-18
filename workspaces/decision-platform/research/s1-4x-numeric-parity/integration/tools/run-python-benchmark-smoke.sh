#!/usr/bin/env bash
set -euo pipefail

# 네 boundary는 pinned dependency와 JAX compile 상태가 섞이지 않도록 process를 분리한다.
ROOT="$(git rev-parse --show-toplevel)"
S1_4X="$ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity"
PRODUCTION="$ROOT/workspaces/decision-platform/python-services"
RESEARCH="$ROOT/workspaces/decision-platform/research/s1-4r-jax-risk"
PLAN="$S1_4X/benchmarks/benchmark-plan.v1.json"
OUTPUT_DIR="${1:?usage: run-python-benchmark-smoke.sh ABSOLUTE_OUTPUT_DIR}"
UV_BIN="${S1_4X_UV_BIN:?set the verified absolute uv executable path}"

case "$OUTPUT_DIR" in
  /*) ;;
  *) echo "output directory must be absolute" >&2; exit 64 ;;
esac
test ! -e "$OUTPUT_DIR" || {
  echo "output directory already exists: $OUTPUT_DIR" >&2
  exit 73
}
mkdir -p "$OUTPUT_DIR"

export UV_PYTHON=3.12.13
export JAX_PLATFORMS=cpu
export JAX_ENABLE_X64=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export JAX_NUM_THREADS=1
export XLA_FLAGS="--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1"

"$UV_BIN" run --frozen --project "$PRODUCTION" \
  python "$S1_4X/integration/python_benchmark_smoke.py" \
  --repo-root "$ROOT" \
  --plan "$PLAN" \
  --boundary python-numpy-s1-4 \
  --output "$OUTPUT_DIR/python-numpy-s1-4.json"

for boundary in \
  python-numpy-s1-4r \
  python-jax-eager-s1-4r \
  python-jax-jit-s1-4r
do
  "$UV_BIN" run --frozen --project "$RESEARCH" \
    python "$S1_4X/integration/python_benchmark_smoke.py" \
    --repo-root "$ROOT" \
    --plan "$PLAN" \
    --boundary "$boundary" \
    --output "$OUTPUT_DIR/$boundary.json"
done
