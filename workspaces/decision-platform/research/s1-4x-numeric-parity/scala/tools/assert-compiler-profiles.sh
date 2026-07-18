#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
CONFIG="$SCALA_ROOT/compiler-profiles.v1.json"
[[ "$#" -eq 0 ]] || {
  printf 'usage: %s\n' "$0" >&2
  exit 64
}

jq -e '
  .schemaVersion == "s1.4x-scala-compiler-profiles-v1" and
  .profiles == {
    A: {
      profileName: "baseline",
      additionalOptions: [],
      scalaCliArguments: []
    },
    B: {
      profileName: "opt",
      additionalOptions: ["-opt"],
      scalaCliArguments: ["--scalac-option=-opt"]
    },
    C: {
      profileName: "opt-own-source-inline",
      additionalOptions: [
        "-opt",
        "-opt-inline:ai.trading.coach.s14x.**"
      ],
      scalaCliArguments: [
        "--scalac-option=-opt",
        "--scalac-option=-opt-inline:ai.trading.coach.s14x.**"
      ]
    }
  }
' "$CONFIG" >/dev/null || {
  printf 'Scala compiler profile option mapping drifted\n' >&2
  exit 1
}

printf 'SCALA_COMPILER_PROFILES_PASS sha256=%s\n' \
  "$(sha256sum "$CONFIG" | awk '{print $1}')"
