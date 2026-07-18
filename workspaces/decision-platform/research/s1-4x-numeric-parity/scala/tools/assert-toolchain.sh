#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:-/home/pjjpj/.local/bin/scala-cli}"
SCALAFIX="${S1_4X_SCALAFIX_BIN:-/home/pjjpj/.local/share/s1-4x/scalafix-0.14.7/bin/scalafix}"

fail() {
  printf 'SCALA_TOOLCHAIN_FAIL %s\n' "$1" >&2
  exit 1
}

[[ -x "$SCALA_CLI" ]] || fail "scala-cli executable missing"
[[ -x "$SCALAFIX" ]] || fail "scalafix executable missing"

scala_cli_sha="$(sha256sum "$SCALA_CLI" | awk '{print $1}')"
scalafix_sha="$(sha256sum "$SCALAFIX" | awk '{print $1}')"
[[ "$scala_cli_sha" == "54b93b8401e333095526da5e4853780d5bf37494baa1ba5486e9e643084253d0" ]] ||
  fail "scala-cli SHA-256 mismatch"
[[ "$scalafix_sha" == "9db6db7359e580de8f4b72cd7c104d70023cf32a278db0c30aefb79c939eb0f3" ]] ||
  fail "scalafix SHA-256 mismatch"

scala_version="$("$SCALA_CLI" version 2>&1)"
grep -Fq "Scala CLI version: 1.15.0" <<<"$scala_version" ||
  fail "scala-cli numeric version mismatch"
grep -Fq "Scala version (default): 3.8.4" <<<"$scala_version" ||
  fail "default Scala version mismatch"
"$SCALAFIX" --version 2>&1 | grep -Fq "0.14.7" ||
  fail "scalafix numeric version mismatch"

java_spec="$(
  java -XshowSettings:properties -version 2>&1 |
    awk -F'= ' '/java.specification.version =/ {print $2; exit}'
)"
[[ "$java_spec" == "25" ]] || fail "JDK specification version mismatch"

grep -Fxq '//> using scala 3.8.4' "$SCALA_ROOT/project.scala" ||
  fail "project Scala pin mismatch"
grep -Fxq '//> using option -release:25' "$SCALA_ROOT/project.scala" ||
  fail "project JDK release mismatch"
grep -Fxq 'version = 3.11.4' "$SCALA_ROOT/.scalafmt.conf" ||
  fail "Scalafmt pin mismatch"

printf 'SCALA_TOOLCHAIN_PASS scalaCliSha256=%s scalafixSha256=%s jdk=%s\n' \
  "$scala_cli_sha" "$scalafix_sha" "$java_spec"
