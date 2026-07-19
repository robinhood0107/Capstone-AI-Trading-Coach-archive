#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
mkdir -p "$CACHE_ROOT/tmp"
export TMPDIR="$CACHE_ROOT/tmp"

python3 "$SCALA_ROOT/tools/test_scalafmt_runner_contract.py"
