#!/usr/bin/env bash
set -euo pipefail

[[ "${1:-}" == "run" ]] || {
  printf 'usage: %s run --request <abs> --fixture-root <abs> --output <abs>\n' "$0" >&2
  exit 64
}
shift

JAR="${S1_4X_SCALA_CANDIDATE_JAR:?set S1_4X_SCALA_CANDIDATE_JAR to a built assembly}"
[[ "$JAR" == /* && -f "$JAR" ]] || {
  printf 'candidate assembly is not an absolute regular file\n' >&2
  exit 70
}

if [[ -n "${S1_4X_SCALA_CANDIDATE_SHA256:-}" ]]; then
  actual_sha="$(sha256sum "$JAR" | awk '{print $1}')"
  [[ "$actual_sha" == "$S1_4X_SCALA_CANDIDATE_SHA256" ]] || {
    printf 'candidate assembly SHA-256 mismatch\n' >&2
    exit 70
  }
fi

exec java -cp "$JAR" ai.trading.coach.s14x.shell.Main "$@"
