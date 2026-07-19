#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
LOCK="$SCALA_ROOT/toolchain-lock.v1.json"
PROVENANCE="$S1_ROOT/contract/toolchain-provenance.v1.json"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:?set exact Scala CLI 1.15.0 binary path from readiness packet}"
SCALAFIX="${S1_4X_SCALAFIX_BIN:?set exact Scalafix 0.14.7 binary path from readiness packet}"
SCALAFMT_ARCHIVE="${S1_4X_SCALAFMT_ARCHIVE:?set exact pinned Scalafmt 3.11.4 archive path}"
SCALAFMT_EXECUTABLE="${S1_4X_SCALAFMT_BIN:?set exact resolved Scalafmt 3.11.4 executable path}"

fail() {
  printf 'SCALA_TOOLCHAIN_FAIL %s\n' "$1" >&2
  exit 1
}

usage() {
  printf 'usage: %s [--lock <absolute-json> --merged-provenance <absolute-json>]\n' \
    "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --lock)
      (($# >= 2)) || usage
      LOCK="$2"
      shift 2
      ;;
    --merged-provenance)
      (($# >= 2)) || usage
      PROVENANCE="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

[[ "$LOCK" == /* && -f "$LOCK" && ! -L "$LOCK" ]] ||
  fail "toolchain lock must be a regular absolute path"
[[ "$PROVENANCE" == /* && -f "$PROVENANCE" && ! -L "$PROVENANCE" ]] ||
  fail "merged provenance must be a regular absolute path"
[[ "$(realpath -- "$PROVENANCE")" == "$S1_ROOT/contract/toolchain-provenance.v1.json" ]] ||
  fail "merged provenance repo path mismatch"
[[ -x "$SCALA_CLI" && "$SCALA_CLI" == /* && ! -L "$SCALA_CLI" ]] ||
  fail "scala-cli executable missing or unsafe"
[[ -x "$SCALAFIX" && "$SCALAFIX" == /* && ! -L "$SCALAFIX" ]] ||
  fail "scalafix executable missing or unsafe"
[[ -f "$SCALAFMT_ARCHIVE" && "$SCALAFMT_ARCHIVE" == /* && ! -L "$SCALAFMT_ARCHIVE" ]] ||
  fail "Scalafmt archive missing or unsafe"
[[ -x "$SCALAFMT_EXECUTABLE" && "$SCALAFMT_EXECUTABLE" == /* && ! -L "$SCALAFMT_EXECUTABLE" ]] ||
  fail "Scalafmt executable missing or unsafe"
[[ -n "${JAVA_HOME:-}" && "$JAVA_HOME" == /* && -d "$JAVA_HOME" && ! -L "$JAVA_HOME" ]] ||
  fail "JAVA_HOME missing or unsafe"
[[ -x "$JAVA_HOME/bin/java" && -x "$JAVA_HOME/bin/javac" \
  && -f "$JAVA_HOME/release" && -f "$JAVA_HOME/lib/modules" \
  && ! -L "$JAVA_HOME/lib/modules" ]] ||
  fail "JAVA_HOME Java, javac, release, or modules file missing"
[[ -z "${JAVA_TOOL_OPTIONS:-}" && -z "${_JAVA_OPTIONS:-}" && -z "${JDK_JAVA_OPTIONS:-}" ]] ||
  fail "ambient JVM option variables must be empty"

java_path="$(realpath -- "$JAVA_HOME/bin/java")"
javac_path="$(realpath -- "$JAVA_HOME/bin/javac")"
jdk_modules_path="$(realpath -- "$JAVA_HOME/lib/modules")"
export PATH="$JAVA_HOME/bin:$PATH"

release_value() {
  local key="$1"
  awk -F= -v expected="$key" '
    $1 == expected {
      value = substr($0, index($0, "=") + 1)
      gsub(/^"|"$/, "", value)
      print value
      exit
    }
  ' "$JAVA_HOME/release"
}

[[ "$(release_value IMPLEMENTOR)" == "Eclipse Adoptium" ]] ||
  fail "JDK IMPLEMENTOR mismatch"
[[ "$(release_value JAVA_RUNTIME_VERSION)" == "25.0.3+9-LTS" ]] ||
  fail "JDK runtime version mismatch"

java_settings="$("$JAVA_HOME/bin/java" -XshowSettings:properties -version 2>&1)"
java_home_actual="$(
  awk -F'= ' '/java.home =/ {print $2; exit}' <<<"$java_settings"
)"
java_runtime_actual="$(
  awk -F'= ' '/java.runtime.version =/ {print $2; exit}' <<<"$java_settings"
)"
java_vendor_actual="$(
  awk -F'= ' '/java.vendor =/ {print $2; exit}' <<<"$java_settings"
)"
java_vm_actual="$(
  awk -F'= ' '/java.vm.name =/ {print $2; exit}' <<<"$java_settings"
)"
java_spec_actual="$(
  awk -F'= ' '/java.specification.version =/ {print $2; exit}' <<<"$java_settings"
)"
[[ "$(realpath -- "$java_home_actual")" == "$(realpath -- "$JAVA_HOME")" ]] ||
  fail "invoked java.home mismatch"
[[ "$java_runtime_actual" == "25.0.3+9-LTS" ]] || fail "runtime property mismatch"
[[ "$java_vendor_actual" == "Eclipse Adoptium" ]] || fail "vendor property mismatch"
[[ "$java_vm_actual" == "OpenJDK 64-Bit Server VM" ]] || fail "VM name mismatch"
[[ "$java_spec_actual" == "25" ]] || fail "JDK specification version mismatch"

java_sha="$(sha256sum "$java_path" | awk '{print $1}')"
javac_sha="$(sha256sum "$javac_path" | awk '{print $1}')"
jdk_modules_sha="$(sha256sum "$jdk_modules_path" | awk '{print $1}')"
scala_cli_sha="$(sha256sum "$SCALA_CLI" | awk '{print $1}')"
scalafix_sha="$(sha256sum "$SCALAFIX" | awk '{print $1}')"
scalafmt_archive_sha="$(sha256sum "$SCALAFMT_ARCHIVE" | awk '{print $1}')"
scalafmt_executable_sha="$(sha256sum "$SCALAFMT_EXECUTABLE" | awk '{print $1}')"
project_sha="$(sha256sum "$SCALA_ROOT/project.scala" | awk '{print $1}')"
scalafmt_config_sha="$(sha256sum "$SCALA_ROOT/.scalafmt.conf" | awk '{print $1}')"
provenance_sha="$(sha256sum "$PROVENANCE" | awk '{print $1}')"

[[ "$scala_cli_sha" == "54b93b8401e333095526da5e4853780d5bf37494baa1ba5486e9e643084253d0" ]] ||
  fail "scala-cli SHA-256 mismatch"
[[ "$scalafix_sha" == "9db6db7359e580de8f4b72cd7c104d70023cf32a278db0c30aefb79c939eb0f3" ]] ||
  fail "scalafix SHA-256 mismatch"
[[ "$scalafmt_archive_sha" == "e7d43a5621074a63a46d5b287d0b0bb0650033deeb836af2b27515b2127476f2" ]] ||
  fail "Scalafmt archive SHA-256 mismatch"
[[ "$scalafmt_executable_sha" == "88526f9f4d64c2fb023d54578812419f49e2ec09e30e4fb77443a05f1a59cac0" ]] ||
  fail "Scalafmt executable SHA-256 mismatch"
[[ "$provenance_sha" == "cd9e29a22473fba6203daa4f3a0cbaa57b8b6e5c5fc22de05ca0801c404ffa98" ]] ||
  fail "merged provenance SHA-256 mismatch"

scala_version="$("$SCALA_CLI" version 2>&1)"
grep -Fq "Scala CLI version: 1.15.0" <<<"$scala_version" ||
  fail "scala-cli numeric version mismatch"
grep -Fq "Scala version (default): 3.8.4" <<<"$scala_version" ||
  fail "default Scala version mismatch"
"$SCALAFIX" --version 2>&1 | grep -Fxq "0.14.7" ||
  fail "scalafix numeric version mismatch"
scalafmt_version="$("$SCALAFMT_EXECUTABLE" --version 2>&1)"
[[ "$scalafmt_version" == "scalafmt 3.11.4" ]] ||
  fail "Scalafmt numeric version mismatch"

grep -Fxq '//> using scala 3.8.4' "$SCALA_ROOT/project.scala" ||
  fail "project Scala pin mismatch"
grep -Fxq '//> using jvm system' "$SCALA_ROOT/project.scala" ||
  fail "project JVM selection mismatch"
grep -Fxq '//> using option -release:25' "$SCALA_ROOT/project.scala" ||
  fail "project JDK release mismatch"
grep -Fxq 'version = 3.11.4' "$SCALA_ROOT/.scalafmt.conf" ||
  fail "Scalafmt config version mismatch"
grep -Fxq 'runner.dialect = scala3' "$SCALA_ROOT/.scalafmt.conf" ||
  fail "Scalafmt dialect mismatch"
grep -Fxq 'lineEndings = unix' "$SCALA_ROOT/.scalafmt.conf" ||
  fail "Scalafmt line-ending policy mismatch"

jq -e \
  --arg java_sha "$java_sha" \
  --arg javac_sha "$javac_sha" \
  --arg jdk_modules_sha "$jdk_modules_sha" \
  --arg scala_cli_sha "$scala_cli_sha" \
  --arg scalafix_sha "$scalafix_sha" \
  --arg scalafmt_archive_sha "$scalafmt_archive_sha" \
  --arg scalafmt_executable_sha "$scalafmt_executable_sha" \
  --arg project_sha "$project_sha" \
  --arg scalafmt_config_sha "$scalafmt_config_sha" \
  --arg provenance_sha "$provenance_sha" \
  --slurpfile provenance "$PROVENANCE" '
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
    .mergedToolchainProvenancePath ==
      "workspaces/decision-platform/research/s1-4x-numeric-parity/contract/toolchain-provenance.v1.json" and
    .mergedToolchainProvenanceSha256 == $provenance_sha and
    .jdk == {
      javaHomePathId: "TEMURIN_25_0_3_9_LTS",
      implementor: "Eclipse Adoptium",
      runtimeVersion: "25.0.3+9-LTS",
      vmName: "OpenJDK 64-Bit Server VM",
      javaExecutableSha256: $java_sha,
      javacExecutableSha256: $javac_sha,
      jdkModulesPathId: "TEMURIN_25_0_3_9_LTS/lib/modules",
      jdkModulesSha256: $jdk_modules_sha
    } and
    .scalaCli == {
      pathId: "SCALA_CLI_1_15_0",
      version: "1.15.0",
      binarySha256: $scala_cli_sha,
      defaultScalaVersion: "3.8.4"
    } and
    .scala == {
      version: "3.8.4",
      projectPath:
        "workspaces/decision-platform/research/s1-4x-numeric-parity/scala/project.scala",
      projectSha256: $project_sha
    } and
    .scalafmt == {
      version: "3.11.4",
      configPath:
        "workspaces/decision-platform/research/s1-4x-numeric-parity/scala/.scalafmt.conf",
      configSha256: $scalafmt_config_sha,
      runnerPathId: "SCALA_CLI_1_15_0",
      archiveUri:
        "https://github.com/scalameta/scalafmt/releases/download/v3.11.4/scalafmt-x86_64-pc-linux.zip",
      archivePathId:
        "S1_4X_CACHE_ROOT/coursier/https/github.com/scalameta/scalafmt/releases/download/v3.11.4/scalafmt-x86_64-pc-linux.zip",
      archiveSha256: $scalafmt_archive_sha,
      executablePathId:
        "COURSIER_ARCHIVE_CACHE/https/github.com/scalameta/scalafmt/releases/download/v3.11.4/scalafmt-x86_64-pc-linux.zip/scalafmt",
      executableSha256: $scalafmt_executable_sha,
      resolvedVersionOutput: "scalafmt 3.11.4",
      resolutionLogUri:
        "evidence://s1-4x-scala-scalafmt-evidence-9c3cb8f-01/logs/first-apply.stderr",
      resolutionLogSha256:
        "1cc7516d57c230f10242f43884f12f3d26cbd6d681dbaed317262148c136b781",
      networkPolicy: "OFFLINE_PINNED_LAUNCHER"
    } and
    .scalafix == {
      pathId: "SCALAFIX_0_14_7",
      version: "0.14.7",
      binarySha256: $scalafix_sha
    } and
    .sharedDistributionProvenance == {
      stackPolicy: $provenance[0].stackPolicy,
      stackInstallCommand: $provenance[0].stackInstallCommand,
      ghcupToolId: $provenance[0].ghcupToolId,
      ghcupVersion: $provenance[0].ghcupVersion,
      ghcupAssetSha256: $provenance[0].ghcupAssetSha256,
      ghcupMetadataCommit: $provenance[0].ghcupMetadataCommit,
      ghcupMetadataUri: $provenance[0].ghcupMetadataUri,
      ghcupMetadataRawUri: $provenance[0].ghcupMetadataRawUri,
      ghcupMetadataRawSha256: $provenance[0].ghcupMetadataRawSha256,
      stackDistributionChannel: $provenance[0].stackDistributionChannel,
      stackArchiveUri: $provenance[0].stackArchiveUri,
      stackArchiveSha256: $provenance[0].stackArchiveSha256,
      stackBinPathId: $provenance[0].stackBinPathId,
      stackBinResolver: $provenance[0].stackBinResolver,
      stackBinSha256: $provenance[0].stackBinSha256,
      stackNumericVersion: $provenance[0].stackNumericVersion,
      upstreamStandaloneAssetSha256: $provenance[0].upstreamStandaloneAssetSha256,
      upstreamStandaloneAssetRole: $provenance[0].upstreamStandaloneAssetRole
    }
  ' "$LOCK" >/dev/null ||
  fail "toolchain lock or structured provenance mismatch"

printf 'SCALA_TOOLCHAIN_PASS lockSha256=%s provenanceSha256=%s scalaCliSha256=%s scalafixSha256=%s scalafmtSha256=%s javaSha256=%s javacSha256=%s jdkModulesSha256=%s jdk=%s\n' \
  "$(sha256sum "$LOCK" | awk '{print $1}')" \
  "$provenance_sha" \
  "$scala_cli_sha" \
  "$scalafix_sha" \
  "$scalafmt_executable_sha" \
  "$java_sha" \
  "$javac_sha" \
  "$jdk_modules_sha" \
  "$java_spec_actual"
