#!/usr/bin/bash
set -euo pipefail

# Outer runner PID closure를 유지한 채 승인된 constrained-host 정책을 재검사한다.
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
readonly DOCKER_BIN="${S1_4X_DOCKER_BIN:?S1_4X_DOCKER_BIN is required}"
readonly DOCKER_SHA256="${S1_4X_DOCKER_SHA256:?S1_4X_DOCKER_SHA256 is required}"
if [[ ! "$UV_BIN" =~ ^/proc/self/fd/[0-9]+$ ]]; then
  echo "UV executable must be inherited through a sealed fd" >&2
  exit 69
fi
if [[ ! "$DOCKER_BIN" =~ ^/proc/self/fd/([3-9]|[1-9][0-9]+)$ ]]; then
  echo "Docker executable must be inherited through a sealed fd" >&2
  exit 69
fi
if [[ ! "$DOCKER_SHA256" =~ ^[0-9a-f]{64}$ ]] \
  || [[ "$(/usr/bin/sha256sum "$DOCKER_BIN" | /usr/bin/awk '{print $1}')" \
    != "$DOCKER_SHA256" ]]; then
  echo "Docker executable SHA-256 mismatch" >&2
  exit 69
fi

ROOT="$("$GIT_BIN" -c core.fsmonitor=false rev-parse --show-toplevel)"
S1_4X="$ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity"
ORACLE="$S1_4X/oracle"

exec "$UV_BIN" run --frozen --no-config --project "$ORACLE" \
  python "$ORACLE/validate_environment.py" \
  --home "$HOME" \
  --docker-bin "$DOCKER_BIN" \
  --docker-sha256 "$DOCKER_SHA256" \
  --cpu-set 0 \
  --min-home-free-bytes 32212254720 \
  --min-available-memory-bytes 4294967296 \
  --max-normalized-load1 0.10 \
  --load-samples 3 \
  --sample-interval-seconds 30 \
  --max-quiet-wait-seconds 600 \
  --max-running-containers 4 \
  --external-process-sample-seconds 30 \
  --max-external-process-cpu-percent 5 \
  "$@"
