#!/usr/bin/env bash
set -euo pipefail

# Outer runner PID closure를 유지한 채 Gate 1 exact host policy를 재검사한다.
ROOT="$(git rev-parse --show-toplevel)"
S1_4X="$ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity"
ORACLE="$S1_4X/oracle"
UV_BIN="${S1_4X_UV_BIN:-$(command -v uv)}"

exec "$UV_BIN" run --frozen --project "$ORACLE" \
  python "$ORACLE/validate_environment.py" \
  --home "$HOME" \
  --cpu-set 0 \
  --min-home-free-bytes 32212254720 \
  --min-available-memory-bytes 8589934592 \
  --max-normalized-load1 0.10 \
  --load-samples 3 \
  --sample-interval-seconds 30 \
  --max-quiet-wait-seconds 600 \
  --max-running-containers 0 \
  --external-process-sample-seconds 30 \
  --max-external-process-cpu-percent 5 \
  "$@"
