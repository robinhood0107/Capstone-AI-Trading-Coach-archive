#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
UV_BIN="${S1_4X_UV_BIN:?set exact uv 0.11.26 binary path}"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:?set exact Scala CLI 1.15.0 binary path}"
PROFILE_NAME="${1:-}"
case "$PROFILE_NAME" in
  baseline | A) PROFILE="A" ;;
  opt | B) PROFILE="B" ;;
  opt-own-source-inline | C) PROFILE="C" ;;
  *)
    printf 'usage: %s baseline|opt|opt-own-source-inline\n' "$0" >&2
    exit 64
    ;;
esac

RESULT_DIR="${RESULT_DIR:?set RESULT_DIR to an absolute correctness result directory}"
[[ "$RESULT_DIR" == /* ]] || {
  printf 'RESULT_DIR must be absolute\n' >&2
  exit 64
}
PROFILE_DIR="$RESULT_DIR/scala/profiles/$PROFILE"
[[ ! -e "$PROFILE_DIR" ]] || {
  printf 'profile output already exists: %s\n' "$PROFILE_DIR" >&2
  exit 1
}

"$SCALA_ROOT/tools/assert-toolchain.sh"
mkdir -p "$PROFILE_DIR"

PROFILE_CONFIG="$SCALA_ROOT/compiler-profiles.v1.json"
"$SCALA_ROOT/tools/assert-compiler-profiles.sh" >/dev/null
mapfile -t profile_options < <(
  jq -er --arg profile "$PROFILE" \
    '.profiles[$profile].scalaCliArguments[]' "$PROFILE_CONFIG"
)
mapfile -t unit_sources < <(
  python3 "$SCALA_ROOT/tools/source_input_manifest.py" \
    --scala-root "$SCALA_ROOT" \
    --manifest "$SCALA_ROOT/source-inputs.v1.json" \
    --policy "$S1_ROOT/contract/scala-source-policy.v1.json" \
    --role configuration \
    --role main \
    --role test
)
[[ "${#unit_sources[@]}" -gt 2 ]] || {
  printf 'unit source manifest closure is empty\n' >&2
  exit 1
}
unit_command=(
  "$SCALA_CLI" test
  "${unit_sources[@]}"
  --server=false
  --jvm system
  --require-tests
  --coursier-validate-checksums
  "${profile_options[@]}"
)
set +e
"${unit_command[@]}" \
  >"$PROFILE_DIR/unit-test.stdout" \
  2>"$PROFILE_DIR/unit-test.stderr"
unit_exit=$?
set -e

python3 - \
  "$SCALA_ROOT" "$SCALA_CLI" "$PROFILE_CONFIG" \
  "$SCALA_ROOT/source-inputs.v1.json" \
  "$PROFILE_DIR/unit-test.stdout" "$PROFILE_DIR/unit-test.stderr" \
  "$PROFILE_DIR/scala-profile-unit-test-result.v1.json" \
  "$PROFILE" "$unit_exit" "${unit_command[@]}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

(
    scala_root,
    scala_cli,
    compiler_profiles,
    manifest_path,
    stdout_path,
    stderr_path,
    output_path,
) = map(Path, sys.argv[1:8])
profile = sys.argv[8]
exit_code = int(sys.argv[9])
runtime_argv = sys.argv[10:]

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result

def strict_object(path):
    result = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"NONFINITE_JSON:{value}")
        ),
    )
    if not isinstance(result, dict):
        raise SystemExit(f"JSON_OBJECT_REQUIRED:{path}")
    return result

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def canonical(value):
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

manifest = strict_object(manifest_path)
profiles = strict_object(compiler_profiles)
portable = []
for item in runtime_argv:
    if item == str(scala_cli):
        portable.append("SCALA_CLI_1_15_0")
    elif item == str(scala_root):
        portable.append("SCALA_ROOT")
    elif item.startswith(f"{scala_root}/"):
        portable.append(f"SCALA_ROOT/{item.removeprefix(f'{scala_root}/')}")
    else:
        portable.append(item)
inputs = [
    path
    for path, metadata in manifest["files"].items()
    if metadata["role"] in {"configuration", "main", "test"}
]
actual_inputs = [
    item.removeprefix(f"{scala_root}/")
    for item in runtime_argv[2:runtime_argv.index("--server=false")]
]
if actual_inputs != inputs:
    raise SystemExit("UNIT_RUNTIME_SOURCE_INPUT_DRIFT")
result = {
    "schemaVersion": "s1.4x-scala-profile-unit-test-result-v1",
    "profileId": profile,
    "compilerProfilesSha256": digest(compiler_profiles),
    "profileOptionsSha256": canonical(
        profiles["profiles"][profile]["additionalOptions"]
    ),
    "sourceInputManifestSha256": digest(manifest_path),
    "inputPaths": actual_inputs,
    "portableArgv": portable,
    "portableArgvSha256": canonical(portable),
    "runtimeArgvSha256": canonical(runtime_argv),
    "scalaCliBinarySha256": digest(scala_cli),
    "exitCode": exit_code,
    "stdoutSha256": digest(stdout_path),
    "stderrSha256": digest(stderr_path),
    "status": "PASS" if exit_code == 0 else "FAIL",
}
with output_path.open("x", encoding="utf-8", newline="\n") as stream:
    stream.write(
        json.dumps(
            result,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
PY
[[ "$unit_exit" -eq 0 ]] || {
  printf 'Scala profile unit test failed: profile=%s exit=%s\n' \
    "$PROFILE" "$unit_exit" >&2
  exit "$unit_exit"
}

"$SCALA_ROOT/tools/build-candidate.sh" \
  --profile "$PROFILE" \
  --output "$PROFILE_DIR/candidate.jar"
export S1_4X_SCALA_CANDIDATE_JAR="$PROFILE_DIR/candidate.jar"
export S1_4X_SCALA_CANDIDATE_SHA256
S1_4X_SCALA_CANDIDATE_SHA256="$(sha256sum "$S1_4X_SCALA_CANDIDATE_JAR" | awk '{print $1}')"

"$SCALA_ROOT/tools/run-candidate.sh" run \
  --request "$S1_ROOT/contract/fixtures/small/canonical-inputs.v1.json" \
  --fixture-root "$S1_ROOT/contract/fixtures" \
  --output "$PROFILE_DIR/canonical-results.json"
"$SCALA_ROOT/tools/run-candidate.sh" run \
  --request "$S1_ROOT/contract/fixtures/invalid/semantic-errors.v1.json" \
  --fixture-root "$S1_ROOT/contract/fixtures" \
  --output "$PROFILE_DIR/semantic-errors.json"

"$SCALA_ROOT/tools/run-property-evidence.sh" \
  --profile "$PROFILE" \
  --output-dir "$PROFILE_DIR/property"

ORACLE="$S1_ROOT/oracle"
CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
export UV_CACHE_DIR="$CACHE_ROOT/uv"
export UV_PROJECT_ENVIRONMENT="$CACHE_ROOT/oracle-venv"
"$UV_BIN" run --project "$ORACLE" --frozen python "$ORACLE/compare_results.py" \
  --expected "$S1_ROOT/contract/fixtures/expected/canonical-results.v1.json" \
  --actual "$PROFILE_DIR/canonical-results.json" \
  >"$PROFILE_DIR/canonical-comparison.json"
"$UV_BIN" run --project "$ORACLE" --frozen python "$ORACLE/compare_results.py" \
  --expected "$S1_ROOT/contract/fixtures/invalid/semantic-errors.expected.v1.json" \
  --actual "$PROFILE_DIR/semantic-errors.json" \
  >"$PROFILE_DIR/semantic-comparison.json"

jq -e '.status == "PASS" and .mismatchCount == 0' \
  "$PROFILE_DIR/canonical-comparison.json" >/dev/null
jq -e '.status == "PASS" and .mismatchCount == 0' \
  "$PROFILE_DIR/semantic-comparison.json" >/dev/null

python3 "$SCALA_ROOT/tools/assemble_profile_correctness.py" \
  --profile "$PROFILE" \
  --scala-root "$SCALA_ROOT" \
  --compiler-profiles "$PROFILE_CONFIG" \
  --source-manifest "$SCALA_ROOT/source-inputs.v1.json" \
  --toolchain-lock "$SCALA_ROOT/toolchain-lock.v1.json" \
  --candidate "$PROFILE_DIR/candidate.jar" \
  --unit-test-result "$PROFILE_DIR/scala-profile-unit-test-result.v1.json" \
  --property-report "$PROFILE_DIR/property/scala-property-report.v1.json" \
  --registry-report "$PROFILE_DIR/property/scala-registry-report.v1.json" \
  --property-execution \
  "$PROFILE_DIR/property/scala-property-execution-evidence.v1.json" \
  --property-plan "$S1_ROOT/contract/property-plan.v1.json" \
  --property-seeds \
  "$S1_ROOT/contract/fixtures/property/property-seeds.v1.json" \
  --function-registry "$S1_ROOT/contract/function-registry.v1.json" \
  --error-registry "$S1_ROOT/contract/error-registry.v1.json" \
  --property-runner "$SCALA_ROOT/tools/run-property-evidence.sh" \
  --property-output-dir "$PROFILE_DIR/property" \
  --canonical-comparison "$PROFILE_DIR/canonical-comparison.json" \
  --semantic-comparison "$PROFILE_DIR/semantic-comparison.json" \
  --output "$PROFILE_DIR/scala-profile-correctness-result.v1.json"

printf 'SCALA_CORRECTNESS_PROFILE_PASS profile=%s candidateSha256=%s\n' \
  "$PROFILE" "$S1_4X_SCALA_CANDIDATE_SHA256"
