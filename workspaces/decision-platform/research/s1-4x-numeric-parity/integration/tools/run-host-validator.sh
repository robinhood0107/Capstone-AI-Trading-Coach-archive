#!/usr/bin/bash
set -euo pipefail

# Outer runner PID closure를 유지한 채 Gate 1 exact host policy를 재검사한다.
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
readonly UV_BIN=/home/pjjpj/.local/bin/uv

ROOT="$("$GIT_BIN" -c core.fsmonitor=false rev-parse --show-toplevel)"
S1_4X="$ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity"
ORACLE="$S1_4X/oracle"

exec "$UV_BIN" run --frozen --no-config --project "$ORACLE" \
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
