#!/usr/bin/env bash
set -euo pipefail

# Generic replay harness에 Scala runner의 필수 run subcommand만 보충한다.
SCRIPT_PATH="$(/usr/bin/readlink -f -- "${BASH_SOURCE[0]}")"
INTEGRATION_ROOT="$(/usr/bin/realpath -- "${SCRIPT_PATH%/*}/..")"
NUMERIC_ROOT="$(/usr/bin/realpath -- "$INTEGRATION_ROOT/..")"
QUALIFIED_RUNNER="$NUMERIC_ROOT/scala/tools/run-candidate.sh"

if [[ ! -x "$QUALIFIED_RUNNER" || -L "$QUALIFIED_RUNNER" ]]; then
  echo "qualified Scala candidate runner is unavailable" >&2
  exit 69
fi

exec "$QUALIFIED_RUNNER" run "$@"
