#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
LOCK="$SCALA_ROOT/toolchain-lock.v1.json"
PROVENANCE="$S1_ROOT/contract/toolchain-provenance.v1.json"
TEST_TMP_ROOT="${S1_4X_TEST_TMP_ROOT:-${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}/tmp}"
mkdir -p "$TEST_TMP_ROOT"
TEST_TMP_ROOT="$(realpath -- "$TEST_TMP_ROOT")"
temporary="$(mktemp -d -p "$TEST_TMP_ROOT" s1-4x-scala-toolchain-lock.XXXXXXXX)"

cleanup() {
  [[ "$temporary" == "$TEST_TMP_ROOT"/s1-4x-scala-toolchain-lock.* ]] || {
    printf 'refusing unsafe temporary cleanup: %s\n' "$temporary" >&2
    exit 1
  }
  rm -rf -- "$temporary"
}
trap cleanup EXIT

"$SCALA_ROOT/tools/assert-toolchain.sh" \
  --lock "$LOCK" \
  --merged-provenance "$PROVENANCE" \
  >"$temporary/clean.stdout" 2>"$temporary/clean.stderr"
grep -Fq 'SCALA_TOOLCHAIN_PASS' "$temporary/clean.stdout"

unix_home='/'"home/"
unix_tmp='/'"tmp/"
if grep -Eq "(${unix_home}[^/]+|${unix_tmp}|[A-Za-z]:\\\\\\\\)" "$LOCK"; then
  printf 'public toolchain lock contains a local absolute path\n' >&2
  exit 1
fi

jq -e '
  (keys | sort) == ([
    "jdk",
    "language",
    "mergedToolchainProvenancePath",
    "mergedToolchainProvenanceSha256",
    "scala",
    "scalaCli",
    "scalafix",
    "scalafmt",
    "schemaVersion",
    "sharedDistributionProvenance"
  ] | sort) and
  .schemaVersion == "s1.4x-scala-toolchain-lock-v1" and
  .language == "scala" and
  .jdk.javaHomePathId == "TEMURIN_25_0_3_9_LTS" and
  .scalaCli.pathId == "SCALA_CLI_1_15_0" and
  .scalafix.pathId == "SCALAFIX_0_14_7" and
  .scalafmt.runnerPathId == "SCALA_CLI_1_15_0" and
  .scalafmt.archivePathId ==
    "S1_4X_CACHE_ROOT/coursier/https/github.com/scalameta/scalafmt/releases/download/v3.11.4/scalafmt-x86_64-pc-linux.zip" and
  .scalafmt.executablePathId ==
    "COURSIER_ARCHIVE_CACHE/https/github.com/scalameta/scalafmt/releases/download/v3.11.4/scalafmt-x86_64-pc-linux.zip/scalafmt" and
  .scalafmt.resolvedVersionOutput == "scalafmt 3.11.4" and
  .scalafmt.networkPolicy == "OFFLINE_PINNED_LAUNCHER"
' "$LOCK" >/dev/null

expect_tamper_rejected() {
  local case_id="$1"
  local filter="$2"
  local changed="$temporary/$case_id.json"
  jq "$filter" "$LOCK" >"$changed"
  if "$SCALA_ROOT/tools/assert-toolchain.sh" \
    --lock "$changed" \
    --merged-provenance "$PROVENANCE" \
    >"$temporary/$case_id.stdout" 2>"$temporary/$case_id.stderr"; then
    printf 'toolchain lock tamper unexpectedly passed: %s\n' "$case_id" >&2
    exit 1
  fi
}

zero='"0000000000000000000000000000000000000000000000000000000000000000"'
expect_tamper_rejected merged-provenance-sha \
  ".mergedToolchainProvenanceSha256 = $zero"
expect_tamper_rejected jdk-runtime \
  '.jdk.runtimeVersion = "25"'
expect_tamper_rejected scala-cli-sha \
  ".scalaCli.binarySha256 = $zero"
expect_tamper_rejected scalafmt-config-sha \
  ".scalafmt.configSha256 = $zero"
expect_tamper_rejected scalafmt-archive-sha \
  ".scalafmt.archiveSha256 = $zero"
expect_tamper_rejected scalafmt-executable-sha \
  ".scalafmt.executableSha256 = $zero"
expect_tamper_rejected scalafix-sha \
  ".scalafix.binarySha256 = $zero"
expect_tamper_rejected stack-archive-sha \
  ".sharedDistributionProvenance.stackArchiveSha256 = $zero"
expect_tamper_rejected installed-stack-role \
  '.sharedDistributionProvenance.upstreamStandaloneAssetRole = "installed"'

printf 'SCALA_TOOLCHAIN_LOCK_TEST_PASS tamperCases=9\n'
