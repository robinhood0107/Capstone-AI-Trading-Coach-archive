#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:?set exact Scala CLI 1.15.0 binary path from readiness packet}"
SCALA_CLI_EXEC="${S1_4X_SCALA_CLI_EXEC_PATH:-$SCALA_CLI}"
SCALA_WORKSPACE="${S1_4X_SCALA_WORKSPACE:?set external Scala CLI workspace}"
JAVAC_BINARY="${JAVA_HOME:?JAVA_HOME is required}/bin/javac"
JAVAC_EXEC="${S1_4X_SCALA_JAVAC_PINNED_FD_PATH:?set pinned javac FD path}"
PROFILE=""
OUTPUT=""

usage() {
  printf 'usage: %s --profile A|B|C --output <new-absolute-list-file>\n' "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --profile)
      (($# >= 2)) || usage
      PROFILE="$2"
      shift 2
      ;;
    --output)
      (($# >= 2)) || usage
      OUTPUT="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

case "$PROFILE" in
  A | B | C) ;;
  *) usage ;;
esac
[[ "$OUTPUT" == /* && ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || usage
[[ "$SCALA_CLI_EXEC" == /* && -x "$SCALA_CLI_EXEC" ]] || usage
[[ "$JAVAC_BINARY" == /* && -f "$JAVAC_BINARY" && -x "$JAVAC_BINARY" \
  && ! -L "$JAVAC_BINARY" ]] || usage
[[ "$JAVAC_EXEC" == /* && -f "$JAVAC_EXEC" && -x "$JAVAC_EXEC" ]] || usage
[[ "$SCALA_WORKSPACE" == /* && "$SCALA_WORKSPACE" != "$SCALA_ROOT"/* \
  && -d "$SCALA_WORKSPACE" && ! -L "$SCALA_WORKSPACE" ]] || usage

"$SCALA_ROOT/tools/assert-toolchain.sh"
"$SCALA_ROOT/tools/assert-compiler-profiles.sh" >/dev/null
jq -e --arg profile "$PROFILE" \
  '.schemaVersion == "s1.4x-scala-compiler-profiles-v1" and
   (.profiles[$profile] != null)' \
  "$SCALA_ROOT/compiler-profiles.v1.json" >/dev/null
mapfile -t profile_options < <(
  jq -er --arg profile "$PROFILE" \
    '.profiles[$profile].scalaCliArguments[]' \
    "$SCALA_ROOT/compiler-profiles.v1.json"
)
mapfile -t benchmark_sources < <(
  python3 -E -s -S "$SCALA_ROOT/tools/source_input_manifest.py" \
    --scala-root "$SCALA_ROOT" \
    --manifest "$SCALA_ROOT/source-inputs.v1.json" \
    --policy "$S1_ROOT/contract/scala-source-policy.v1.json" \
    --role configuration \
    --role main \
    --role benchmark
)
[[ "${#benchmark_sources[@]}" -gt 2 ]] || {
  printf 'benchmark source manifest closure is empty\n' >&2
  exit 1
}

CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
mkdir -p "$CACHE_ROOT/tmp" "$CACHE_ROOT/coursier" "$(dirname -- "$OUTPUT")"
export TMPDIR="$CACHE_ROOT/tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export COURSIER_CACHE="$CACHE_ROOT/coursier"

# compile과 실제 run의 Scala CLI build identity를 같게 만들 외부 classpath를
# 먼저 빈 directory로 생성한다. helper만 이 directory를 채울 수 있다.
precompiled_classes="$(dirname -- "$OUTPUT")/generated-java-classes"
[[ ! -e "$precompiled_classes" && ! -L "$precompiled_classes" ]] || usage
mkdir -p "$precompiled_classes"

# Helper는 exact `compile --server=false --jmh --print-classpath` 뒤 생성된
# 30-file Java closure를 pinned javac으로 외부 sealed classes에 컴파일한다.
python3 -E -s -S "$SCALA_ROOT/tools/precompile_jmh_generated_java.py" precompile \
  --scala-root "$SCALA_ROOT" \
  --workspace "$SCALA_WORKSPACE" \
  --coursier-cache "$COURSIER_CACHE" \
  --evidence-dir "$(dirname -- "$OUTPUT")" \
  --source-manifest "$SCALA_ROOT/source-inputs.v1.json" \
  --source-policy "$S1_ROOT/contract/scala-source-policy.v1.json" \
  --compiler-profiles "$SCALA_ROOT/compiler-profiles.v1.json" \
  --toolchain-lock "$SCALA_ROOT/toolchain-lock.v1.json" \
  --scala-cli-binary "$SCALA_CLI" \
  --scala-cli-exec "$SCALA_CLI_EXEC" \
  --javac-binary "$JAVAC_BINARY" \
  --javac-exec "$JAVAC_EXEC" \
  --profile "$PROFILE" >/dev/null
precompile_receipt="$(
  dirname -- "$OUTPUT"
)/scala-jmh-generated-java-precompile.v1.json"
jq -e '
  .schemaVersion == "s1.4x-scala-jmh-generated-java-precompile-v1" and
  .status == "PASS" and
  .aggregateStatus == "PASS" and
  .generatedClassOutputPathId == "EVIDENCE_ROOT/generated-java-classes"
' "$precompile_receipt" >/dev/null
[[ -d "$precompiled_classes" && ! -L "$precompiled_classes" ]] || usage

# Scala CLI + JDK 25 real JVM/JMH compile/list: --jmh --jmh-version 1.37.
"$SCALA_CLI_EXEC" --power run \
  "${benchmark_sources[@]}" \
  --workspace "$S1_4X_SCALA_WORKSPACE" \
  --server=false \
  --classpath "$precompiled_classes" \
  --jvm system \
  --coursier-validate-checksums \
  "${profile_options[@]}" \
  --jmh --jmh-version 1.37 -- \
  -l >"$OUTPUT"

printf 'SCALA_JMH_LIST_PASS profile=%s outputSha256=%s\n' \
  "$PROFILE" "$(sha256sum "$OUTPUT" | awk '{print $1}')"
