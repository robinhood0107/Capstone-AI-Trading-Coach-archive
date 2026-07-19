#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

tracked_files=()
while IFS= read -r path; do
  tracked_files+=("$SCALA_ROOT/$path")
done < <(
  git -C "$SCALA_ROOT" ls-files --cached --others --exclude-standard -- \
    '*.sh' '*.py' '*.json' '*.scala' 'Containerfile' |
    sort
)

((${#tracked_files[@]} > 0)) || {
  printf 'portable path audit found no Scala inputs\n' >&2
  exit 1
}

unix_home='/'"home/"
unix_tmp='/'"tmp"
host_path_pattern="(${unix_home}[A-Za-z0-9._-]+|${unix_tmp}(/|[\"'])|[A-Za-z]:\\\\\\\\|command[[:space:]]+-v)"
host_path_pattern="(?<![A-Za-z0-9_$}])$host_path_pattern"
if rg -n -P \
  "$host_path_pattern" \
  "${tracked_files[@]}"; then
  printf 'tracked Scala artifact contains a host-specific path or PATH lookup\n' >&2
  exit 1
fi

required_env_contracts=(
  'S1_4X_SCALA_CLI_BIN:?'
  'S1_4X_SCALAFIX_BIN:?'
  'S1_4X_SCALAFMT_ARCHIVE:?'
  'S1_4X_SCALAFMT_BIN:?'
)
for contract in "${required_env_contracts[@]}"; do
  rg -Fq "$contract" "$SCALA_ROOT/tools" || {
    printf 'missing fail-closed readiness environment contract: %s\n' "$contract" >&2
    exit 1
  }
done

printf 'SCALA_PORTABLE_RUNNER_PATHS_PASS trackedFiles=%s\n' \
  "${#tracked_files[@]}"
