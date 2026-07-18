#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:?set exact Scala CLI 1.15.0 binary path from readiness packet}"
PLAN=""
PROFILE=""
CASE_ID=""
MODE=""
OUTPUT_DIR=""
JVM_ALLOWLIST="${S1_4X_SCALA_JVM_ALLOWLIST_RESULT:-}"

usage() {
  printf 'usage: %s --plan <absolute-json> --profile A|B|C --case-id <id> --mode smoke|qualification|full [--jvm-allowlist <absolute-json>] --output-dir <new-absolute-directory>\n' \
    "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --plan)
      (($# >= 2)) || usage
      PLAN="$2"
      shift 2
      ;;
    --profile)
      (($# >= 2)) || usage
      PROFILE="$2"
      shift 2
      ;;
    --case-id)
      (($# >= 2)) || usage
      CASE_ID="$2"
      shift 2
      ;;
    --mode)
      (($# >= 2)) || usage
      MODE="$2"
      shift 2
      ;;
    --output-dir)
      (($# >= 2)) || usage
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --jvm-allowlist)
      (($# >= 2)) || usage
      JVM_ALLOWLIST="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

[[ "$PLAN" == /* && -f "$PLAN" && ! -L "$PLAN" ]] || usage
case "$PROFILE" in
  A | B | C) ;;
  *) usage ;;
esac
case "$MODE" in
  smoke | qualification | full) ;;
  *) usage ;;
esac
[[ "$CASE_ID" =~ ^[a-z0-9][a-z0-9._/-]{0,191}$ ]] || usage
[[ "$OUTPUT_DIR" == /* && ! -e "$OUTPUT_DIR" && ! -L "$OUTPUT_DIR" ]] || usage
if [[ "$MODE" == "smoke" ]]; then
  [[ -z "$JVM_ALLOWLIST" ]] || usage
else
  [[ "$JVM_ALLOWLIST" == /* && -f "$JVM_ALLOWLIST" && ! -L "$JVM_ALLOWLIST" ]] ||
    usage
fi

JAVA_EXECUTABLE="${JAVA_HOME:?JAVA_HOME is required}/bin/java"
[[ "$JAVA_EXECUTABLE" == /* \
  && -f "$JAVA_EXECUTABLE" \
  && -x "$JAVA_EXECUTABLE" \
  && ! -L "$JAVA_EXECUTABLE" ]] || usage
java_sha="$(sha256sum "$JAVA_EXECUTABLE" | awk '{print $1}')"
pinned_java_input="${S1_4X_SCALA_JAVA_PINNED_FD_PATH:-}"
if [[ -z "$pinned_java_input" ]]; then
  [[ "$MODE" == "smoke" ]] || {
    printf 'qualification/full JMH requires a parent-sealed Java FD\n' >&2
    exit 69
  }
  # Standalone smoke도 pathname 재개방 없이 같은 inode를 JMH fork까지 전달한다.
  exec {java_pin_fd}<"$JAVA_EXECUTABLE"
  JAVA_EXEC="/proc/$$/fd/$java_pin_fd"
elif [[ "$pinned_java_input" =~ ^/proc/self/fd/([0-9]+)$ ]]; then
  # JMH의 nested fork에서도 열 수 있도록 이 shell의 안정된 PID로 정규화한다.
  JAVA_EXEC="/proc/$$/fd/${BASH_REMATCH[1]}"
elif [[ "$pinned_java_input" =~ ^/proc/[1-9][0-9]*/fd/[0-9]+$ ]]; then
  JAVA_EXEC="$pinned_java_input"
else
  printf 'Java pinned FD path is invalid\n' >&2
  exit 69
fi
if [[ ! -f "$JAVA_EXEC" \
  || ! -x "$JAVA_EXEC" \
  || "$(sha256sum "$JAVA_EXEC" | awk '{print $1}')" != "$java_sha" ]]; then
  printf 'Java pinned FD identity mismatch\n' >&2
  exit 69
fi
export S1_4X_SCALA_JAVA_PINNED_FD_PATH="$JAVA_EXEC"

CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
[[ "$CACHE_ROOT" == /* && -d "$CACHE_ROOT" && ! -L "$CACHE_ROOT" ]] || usage
expected_coursier_cache="$CACHE_ROOT/coursier"
mkdir -p "$expected_coursier_cache" "$CACHE_ROOT/scala-isolation"
[[ "${COURSIER_CACHE:-$expected_coursier_cache}" == "$expected_coursier_cache" ]] ||
  {
    printf 'ambient Coursier cache is forbidden\n' >&2
    exit 69
  }
export COURSIER_CACHE="$expected_coursier_cache"
export S1_4X_CACHE_ROOT="$CACHE_ROOT"

isolation_key="$(
  printf '%s' "$OUTPUT_DIR" | sha256sum | awk '{print $1}'
)"
default_isolation="$CACHE_ROOT/scala-isolation/$isolation_key"
if [[ -n "${S1_4X_SCALA_ENVIRONMENT_VALUES_SHA256:-}" ]]; then
  [[ "$S1_4X_SCALA_ENVIRONMENT_VALUES_SHA256" =~ ^[0-9a-f]{64}$ ]] || usage
  scala_cli_home="${SCALA_CLI_HOME:?sealed Scala CLI home is required}"
  coursier_config="${COURSIER_CONFIG_DIR:?sealed Coursier config is required}"
  scala_workspace="${S1_4X_SCALA_WORKSPACE:?sealed Scala workspace is required}"
  xdg_config="${XDG_CONFIG_HOME:?sealed XDG config is required}"
  scala_cli_config="${SCALA_CLI_CONFIG:?sealed Scala CLI config is required}"
  sealed_block_root="$(dirname -- "$(dirname -- "$OUTPUT_DIR")")"
  sealed_workspace_key="$(
    printf '%s' "$sealed_block_root" | sha256sum | awk '{print $1}'
  )"
  [[ "$scala_cli_home" == "$sealed_block_root/scala-cli-home" \
    && "$coursier_config" == "$sealed_block_root/coursier-config" \
    && "$scala_workspace" == \
      "$CACHE_ROOT/scala-workspaces/$sealed_workspace_key" \
    && "$xdg_config" == "$sealed_block_root/xdg-config" \
    && "$scala_cli_config" == "$sealed_block_root/scala-cli-home/config.json" ]] ||
    {
      printf 'sealed Scala isolation closure differs from block identity\n' >&2
      exit 69
    }
else
  for ambient_name in \
    COURSIER_CONFIG_DIR COURSIER_REPOSITORIES SCALA_CLI_CONFIG \
    SCALA_CLI_HOME S1_4X_SCALA_WORKSPACE XDG_CONFIG_HOME; do
    [[ -z "${!ambient_name+x}" ]] || {
      printf 'ambient Scala configuration is forbidden: %s\n' \
        "$ambient_name" >&2
      exit 69
    }
  done
  [[ ! -e "$default_isolation" && ! -L "$default_isolation" ]] || {
    printf 'ambient Scala isolation directory already exists\n' >&2
    exit 69
  }
  scala_cli_home="$default_isolation/scala-cli-home"
  coursier_config="$default_isolation/coursier-config"
  scala_workspace="$default_isolation/scala-workspace"
  xdg_config="$default_isolation/xdg-config"
  scala_cli_config="$scala_cli_home/config.json"
fi
for path in \
  "$scala_cli_home" "$coursier_config" "$scala_workspace" "$xdg_config"; do
  [[ "$path" == /* && "$path" != "$SCALA_ROOT"/* && ! -L "$path" ]] || {
    printf 'unsafe Scala isolation path: %s\n' "$path" >&2
    exit 69
  }
  mkdir -p "$path"
done
[[ "$scala_cli_config" == "$scala_cli_home/config.json" ]] || {
  printf 'ambient Scala CLI config is forbidden\n' >&2
  exit 69
}
export SCALA_CLI_HOME="$scala_cli_home"
export COURSIER_CONFIG_DIR="$coursier_config"
export S1_4X_SCALA_WORKSPACE="$scala_workspace"
export XDG_CONFIG_HOME="$xdg_config"
export SCALA_CLI_CONFIG="$scala_cli_config"
unset COURSIER_REPOSITORIES
SCALA_CLI_EXEC="${S1_4X_SCALA_CLI_EXEC_PATH:-$SCALA_CLI}"
[[ "$SCALA_CLI_EXEC" == /* && -x "$SCALA_CLI_EXEC" ]] || {
  printf 'Scala CLI execution path is invalid\n' >&2
  exit 69
}

"$SCALA_ROOT/tools/assert-toolchain.sh"
"$SCALA_ROOT/tools/assert-compiler-profiles.sh" >/dev/null
"$SCALA_ROOT/tools/check-jmh-plan-integrity.sh" --plan "$PLAN"

case_json="$(
  jq -ce --arg caseId "$CASE_ID" '
    [.cases[] | select(.caseId == $caseId)] as $matches |
    if ($matches | length) == 1 then $matches[0] else error("case closure") end
  ' "$PLAN"
)"
family="$(jq -r '.familyId' <<<"$case_json")"
logical_operations="$(jq -er '.logicalOperationsPerInvocation' <<<"$case_json")"
selector="$(jq -ce --arg family "$family" '
  [.familySelectors[] |
    select(.boundaryId == "scala" and .familyId == $family)] as $matches |
  if ($matches | length) == 1 then $matches[0] else error("selector closure") end
' "$PLAN")"
include_regex="$(jq -r '.jmhIncludeRegex' <<<"$selector")"

case "$family" in
  path-transform)
    benchmark='s1_4x.benchmarks.path_transform.PathTransformBenchmark.benchmark'
    ;;
  classical-path-risk)
    benchmark='s1_4x.benchmarks.classical_path_risk.ClassicalPathRiskBenchmark.benchmark'
    ;;
  intraday-realized)
    benchmark='s1_4x.benchmarks.intraday_realized.IntradayRealizedBenchmark.benchmark'
    ;;
  serial-sharpe)
    benchmark='s1_4x.benchmarks.serial_sharpe.SerialSharpeBenchmark.benchmark'
    ;;
  probabilistic-scalar)
    benchmark='s1_4x.benchmarks.probabilistic_scalar.ProbabilisticScalarBenchmark.benchmark'
    ;;
  coverage-batch)
    benchmark='s1_4x.benchmarks.coverage_batch.CoverageBatchBenchmark.benchmark'
    ;;
  *)
    printf 'unknown frozen Scala family: %s\n' "$family" >&2
    exit 1
    ;;
esac

mkdir -p "$OUTPUT_DIR/fork-evidence"
list_file="$OUTPUT_DIR/jmh-list.txt"
"$SCALA_ROOT/tools/compile-benchmarks.sh" \
  --profile "$PROFILE" \
  --output "$list_file"
[[ "$(grep -Fxc "$benchmark" "$list_file")" -eq 1 ]] || {
  printf 'JMH list did not contain the exact benchmark once: %s\n' "$benchmark" >&2
  exit 1
}

mapfile -t profile_options < <(
  jq -er --arg profile "$PROFILE" \
    '.profiles[$profile].scalaCliArguments[]' \
    "$SCALA_ROOT/compiler-profiles.v1.json"
)
mapfile -t benchmark_sources < <(
  python3 "$SCALA_ROOT/tools/source_input_manifest.py" \
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
if [[ "$MODE" == "smoke" ]]; then
  forks=1
  warmup_iterations=1
  measurement_iterations=1
  warmup_time=200ms
  measurement_time=200ms
elif [[ "$MODE" == "qualification" ]]; then
  forks="$(jq -er '.scalaProfileQualification.forks' "$PLAN")"
  warmup_iterations="$(jq -er '.scalaProfileQualification.warmupIterations' "$PLAN")"
  measurement_iterations="$(
    jq -er '.scalaProfileQualification.measurementIterations' "$PLAN"
  )"
  warmup_time="$(jq -er '.scalaProfileQualification.warmupTime' "$PLAN")"
  measurement_time="$(
    jq -er '.scalaProfileQualification.measurementTime' "$PLAN"
  )"
else
  forks="$(jq -er '.execution.forks.scala' "$PLAN")"
  warmup_iterations="$(jq -er '.execution.warmupIterations.scala' "$PLAN")"
  measurement_iterations="$(
    jq -er '.execution.measurementIterations.scala' "$PLAN"
  )"
  warmup_time="$(jq -er '.execution.warmupTimeSeconds.scala | tostring + "s"' "$PLAN")"
  measurement_time="$(
    jq -er '.execution.measurementTimeSeconds.scala | tostring + "s"' "$PLAN"
  )"
fi

native_json="$OUTPUT_DIR/native.json"
command=(
  "$SCALA_CLI_EXEC" --power run
  "${benchmark_sources[@]}"
  --workspace "$S1_4X_SCALA_WORKSPACE"
  --server=false
  --jvm system
  --coursier-validate-checksums
  "${profile_options[@]}"
  --jmh --jmh-version 1.37 --
  -bm avgt -tu ns -t 1
  -jvm "$JAVA_EXEC"
  -f "$forks"
  -wi "$warmup_iterations"
  -i "$measurement_iterations"
  -w "$warmup_time"
  -r "$measurement_time"
  -rf json
  -rff "$native_json"
  "$include_regex"
)

export S1_4X_BENCHMARK_CASE_ID="$CASE_ID"
export S1_4X_BENCHMARK_PLAN="$PLAN"
export S1_4X_BENCHMARK_PROFILE="$PROFILE"
export S1_4X_BENCHMARK_RUN_MODE="$MODE"
export S1_4X_FIXTURE_ROOT="$S1_ROOT/contract/fixtures"
export S1_4X_EFFECTIVE_JVM_EVIDENCE_DIR="$OUTPUT_DIR/fork-evidence"
export S1_4X_MEASUREMENT_READY_MARKER="$OUTPUT_DIR/measurement-ready.v1.json"
"${command[@]}" >"$OUTPUT_DIR/jmh.stdout" 2>"$OUTPUT_DIR/jmh.stderr"

python3 - "$OUTPUT_DIR/fork-evidence" "$OUTPUT_DIR/fork-evidence.normalized.json" "$forks" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SystemExit(f"DUPLICATE_JSON_KEY:{key}")
        value[key] = item
    return value

root = Path(sys.argv[1])
output = Path(sys.argv[2])
expected = int(sys.argv[3])
paths = sorted(root.glob("jvm-fork-*.json"), key=lambda path: path.name)
if len(paths) != expected:
    raise SystemExit("JVM_FORK_FILE_COUNT_MISMATCH")
values = []
process_ids = set()
for index, path in enumerate(paths, start=1):
    raw_bytes = path.read_bytes()
    raw = json.loads(
        raw_bytes,
        object_pairs_hook=unique_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"NONFINITE_JSON:{value}")
        ),
    )
    if raw.get("schemaVersion") != "s1.4x-scala-jvm-fork-raw-evidence-v1":
        raise SystemExit("JVM_FORK_RAW_SCHEMA_MISMATCH")
    process_id = raw.pop("forkProcessId", None)
    start_time = raw.pop("runtimeStartTimeEpochMillis", None)
    if (
        type(process_id) is not int
        or process_id <= 0
        or process_id in process_ids
        or type(start_time) is not int
        or start_time <= 0
    ):
        raise SystemExit("JVM_FORK_PROCESS_IDENTITY_INVALID")
    process_ids.add(process_id)
    raw["schemaVersion"] = "s1.4x-scala-jvm-fork-evidence-v1"
    raw["forkIndex"] = index
    raw["evidenceSha256"] = hashlib.sha256(raw_bytes).hexdigest()
    values.append(raw)
with output.open("x", encoding="utf-8", newline="\n") as stream:
    stream.write(
        json.dumps(
            values,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
PY

if [[ "$MODE" == "smoke" ]]; then
  JVM_ALLOWLIST="$OUTPUT_DIR/scala-jvm-argument-allowlist.v1.json"
  python3 "$SCALA_ROOT/tools/t3_evidence.py" create-jvm-allowlist \
    --fork-evidence "$OUTPUT_DIR/fork-evidence.normalized.json" \
    --benchmark-plan "$PLAN" \
    --capability-smoke-plan "$S1_ROOT/contract/capability-smoke-plan.v1.json" \
    --toolchain-lock "$SCALA_ROOT/toolchain-lock.v1.json" \
    --java-executable-sha256 "$java_sha" \
    --output "$JVM_ALLOWLIST"
fi
python3 "$SCALA_ROOT/tools/t3_evidence.py" validate-effective-jvm \
  --fork-evidence "$OUTPUT_DIR/fork-evidence.normalized.json" \
  --expected-forks "$forks" \
  --jvm-allowlist "$JVM_ALLOWLIST" \
  --output "$OUTPUT_DIR/scala-effective-jvm-args-result.v1.json"
python3 "$SCALA_ROOT/tools/t3_evidence.py" validate-native-jmh \
  --native "$native_json" \
  --expected-benchmark "$benchmark" \
  --expected-forks "$forks" \
  --expected-warmup-iterations "$warmup_iterations" \
  --expected-warmup-time "$warmup_time" \
  --expected-measurement-iterations "$measurement_iterations" \
  --expected-measurement-time "$measurement_time" \
  --logical-operations-per-invocation "$logical_operations" \
  --effective-jvm-arguments "$OUTPUT_DIR/scala-effective-jvm-args-result.v1.json" \
  --output "$OUTPUT_DIR/scala-jmh-native-validation.v1.json"

python3 - \
  "$PLAN" "$SCALA_ROOT/source-inputs.v1.json" \
  "$native_json" "$OUTPUT_DIR/scala-effective-jvm-args-result.v1.json" \
  "$OUTPUT_DIR/scala-jmh-native-validation.v1.json" \
  "$OUTPUT_DIR/measurement-ready.v1.json" \
  "$OUTPUT_DIR/jmh.stdout" "$OUTPUT_DIR/jmh.stderr" \
  "$JVM_ALLOWLIST" "$SCALA_ROOT/compiler-profiles.v1.json" "$SCALA_CLI" \
  "$OUTPUT_DIR/scala-jmh-run-result.v1.json" \
  "$PROFILE" "$CASE_ID" "$MODE" "$logical_operations" "${command[@]}" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

(
    plan,
    manifest,
    native,
    effective,
    validation,
    measurement_ready,
    stdout,
    stderr,
    allowlist,
    compiler_profiles,
    scala_cli,
    output,
) = map(Path, sys.argv[1:13])
profile, case_id, mode = sys.argv[13:16]
logical_operations = int(sys.argv[16])
runtime_argv = sys.argv[17:]

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def strict_object(path: Path) -> dict:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise SystemExit(f"DUPLICATE_JSON_KEY:{key}")
            value[key] = item
        return value

    result = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"NONFINITE_JSON:{value}")
        ),
    )
    if not isinstance(result, dict):
        raise SystemExit(f"JSON_OBJECT_REQUIRED:{path}")
    return result

scala_root = manifest.parent
output_root = output.parent
scala_workspace = Path(os.environ["S1_4X_SCALA_WORKSPACE"])
java_exec = os.environ["S1_4X_SCALA_JAVA_PINNED_FD_PATH"]
compiler_config = strict_object(compiler_profiles)
source_manifest = strict_object(manifest)
native_validation = strict_object(validation)
expected_inputs = [
    path
    for path, metadata in source_manifest["files"].items()
    if metadata["role"] in {"configuration", "main", "benchmark"}
]
workspace_index = runtime_argv.index("--workspace")
if (
    workspace_index < 3
    or workspace_index + 2 >= len(runtime_argv)
    or runtime_argv[workspace_index + 2] != "--server=false"
):
    raise SystemExit("JMH_RUNTIME_WORKSPACE_POSITION_DRIFT")
actual_inputs = [
    item.removeprefix(f"{scala_root}/")
    for item in runtime_argv[3:workspace_index]
]
if actual_inputs != expected_inputs:
    raise SystemExit("JMH_RUNTIME_SOURCE_INPUT_DRIFT")
portable = []
for item in runtime_argv:
    if item == runtime_argv[0]:
        portable.append("SCALA_CLI_1_15_0")
    elif item == java_exec:
        portable.append("PINNED_JAVA_FD")
    elif item.startswith(f"{scala_root}/"):
        portable.append(f"SCALA_ROOT/{item.removeprefix(f'{scala_root}/')}")
    elif item == str(plan):
        portable.append("BENCHMARK_PLAN")
    elif item == str(output_root):
        portable.append("EVIDENCE_ROOT")
    elif item.startswith(f"{output_root}/"):
        portable.append(f"EVIDENCE_ROOT/{item.removeprefix(f'{output_root}/')}")
    elif item == str(scala_workspace):
        portable.append("SCALA_WORKSPACE")
    else:
        portable.append(item)
jvm_index = portable.index("-jvm")
if portable[jvm_index : jvm_index + 2] != ["-jvm", "PINNED_JAVA_FD"]:
    raise SystemExit("JMH_RUNTIME_JAVA_PIN_DRIFT")

def canonical(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

tool_paths = [
    ("SCALA_CLI_1_15_0", scala_cli),
    ("TEMURIN_25_0_3_9_LTS/bin/java", Path(os.environ["JAVA_HOME"]) / "bin/java"),
]
if mode == "full":
    tool_paths.append(
        (
            "SCALA_ROOT/tools/run-jmh-native-full.sh",
            scala_root / "tools/run-jmh-native-full.sh",
        )
    )
tool_paths.extend(
    [
        (
            "SCALA_ROOT/tools/run-jmh-native-smoke.sh",
            scala_root / "tools/run-jmh-native-smoke.sh",
        ),
        (
            "SCALA_ROOT/tools/compile-benchmarks.sh",
            scala_root / "tools/compile-benchmarks.sh",
        ),
        (
            "SCALA_ROOT/tools/assert-toolchain.sh",
            scala_root / "tools/assert-toolchain.sh",
        ),
        (
            "SCALA_ROOT/tools/assert-compiler-profiles.sh",
            scala_root / "tools/assert-compiler-profiles.sh",
        ),
        (
            "SCALA_ROOT/tools/check-jmh-plan-integrity.sh",
            scala_root / "tools/check-jmh-plan-integrity.sh",
        ),
        (
            "SCALA_ROOT/tools/source_input_manifest.py",
            scala_root / "tools/source_input_manifest.py",
        ),
        (
            "SCALA_ROOT/tools/t3_evidence.py",
            scala_root / "tools/t3_evidence.py",
        ),
    ]
)
tool_closure = [
    {"pathId": path_id, "sha256": digest(path)}
    for path_id, path in tool_paths
]
cache_root = Path(os.environ["S1_4X_CACHE_ROOT"])
if Path(os.environ["COURSIER_CACHE"]) != cache_root / "coursier":
    raise SystemExit("COURSIER_CACHE_IDENTITY_DRIFT")
environment_values = {
    "COURSIER_CACHE": "CACHE_ROOT/coursier",
    "COURSIER_CONFIG_DIR": "SCALA_ISOLATION/coursier-config",
    "SCALA_CLI_CONFIG": "SCALA_ISOLATION/scala-cli-home/config.json",
    "SCALA_CLI_HOME": "SCALA_ISOLATION/scala-cli-home",
    "S1_4X_SCALA_WORKSPACE": "SCALA_WORKSPACE",
    "XDG_CONFIG_HOME": "SCALA_ISOLATION/xdg-config",
}
execution_path_id = (
    "PINNED_SCALA_CLI_FD"
    if runtime_argv[0].startswith("/proc/self/fd/")
    else "SCALA_CLI_1_15_0"
)

result = {
    "schemaVersion": "s1.4x-scala-jmh-run-result-v1",
    "profileId": profile,
    "caseId": case_id,
    "logicalOperationsPerInvocation": logical_operations,
    "rawScoreNsPerInvocation": native_validation[
        "rawScoreNsPerInvocation"
    ],
    "normalizedScoreNsPerLogicalOperation": native_validation[
        "normalizedScoreNsPerLogicalOperation"
    ],
    "runMode": mode,
    "benchmarkPlanSha256": digest(plan),
    "sourceInputManifestSha256": digest(manifest),
    "scalaCliBinarySha256": digest(scala_cli),
    "scalaCliExecutionPathId": execution_path_id,
    "compilerProfilesSha256": digest(compiler_profiles),
    "profileOptionsSha256": canonical(
        compiler_config["profiles"][profile]["additionalOptions"]
    ),
    "inputPaths": actual_inputs,
    "portableArgv": portable,
    "portableArgvSha256": canonical(portable),
    "runtimeArgvSha256": canonical(runtime_argv),
    "commandToolClosure": tool_closure,
    "commandToolClosureSha256": canonical(tool_closure),
    "environmentValuesSha256": canonical(environment_values),
    "scalaWorkspacePathId": "SCALA_WORKSPACE",
    "rawNativeJsonSha256": digest(native),
    "effectiveJvmArgsSha256": digest(effective),
    "jvmArgumentAllowlistSha256": digest(allowlist),
    "nativeValidationSha256": digest(validation),
    "measurementReadyMarkerSha256": digest(measurement_ready),
    "stdoutSha256": digest(stdout),
    "stderrSha256": digest(stderr),
    "exitCode": 0,
    "status": "PASS",
    "aggregateStatus": "PASS",
}
with output.open("x", encoding="utf-8", newline="\n") as stream:
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

printf 'SCALA_JMH_NATIVE_PASS profile=%s caseId=%s mode=%s nativeSha256=%s\n' \
  "$PROFILE" "$CASE_ID" "$MODE" "$(sha256sum "$native_json" | awk '{print $1}')"
