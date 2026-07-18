#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
POLICY=""
OUTPUT="${S1_4X_SCALA_SOURCE_POLICY_OUTPUT:-}"
SEMANTIC_RECEIPT=""
ALL_INPUTS=false

usage() {
  printf 'usage: %s --policy <absolute-json> --semantic-receipt <absolute-json> --all-production-and-benchmark-inputs [--output <absolute-json>]\n' \
    "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --policy)
      (($# >= 2)) || usage
      POLICY="$2"
      shift 2
      ;;
    --all-production-and-benchmark-inputs)
      ALL_INPUTS=true
      shift
      ;;
    --semantic-receipt)
      (($# >= 2)) || usage
      SEMANTIC_RECEIPT="$2"
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

[[ "$POLICY" == /* && -f "$POLICY" ]] || usage
[[ "$SEMANTIC_RECEIPT" == /* && -f "$SEMANTIC_RECEIPT" ]] || usage
[[ "$ALL_INPUTS" == true ]] || usage
if [[ -z "$OUTPUT" ]]; then
  OUTPUT="${S1_4X_RESULT_DIR:?set S1_4X_RESULT_DIR or pass --output}"
  OUTPUT="$OUTPUT/scala-source-policy-result.v1.json"
fi
[[ "$OUTPUT" == /* && ! -e "$OUTPUT" ]] || {
  printf 'source-policy output must be a new absolute path\n' >&2
  exit 64
}

CORE_OUTPUT="$OUTPUT.core"
STDOUT_LOG="$OUTPUT.stdout"
STDERR_LOG="$OUTPUT.stderr"
[[ ! -e "$CORE_OUTPUT" && ! -e "$STDOUT_LOG" && ! -e "$STDERR_LOG" ]] || {
  printf 'source-policy sibling evidence paths must be new\n' >&2
  exit 64
}
command=(
  python3 "$SCALA_ROOT/tools/check_source_policy.py"
  --scala-root "$SCALA_ROOT" \
  --policy "$POLICY" \
  --manifest "$SCALA_ROOT/source-inputs.v1.json" \
  --semantic-receipt "$SEMANTIC_RECEIPT" \
  --require-git-source-equality \
  --output "$CORE_OUTPUT"
)
set +e
"${command[@]}" >"$STDOUT_LOG" 2>"$STDERR_LOG"
exit_code=$?
set -e
[[ "$exit_code" -eq 0 ]] || {
  printf 'Scala source-policy core failed: exit=%s stderr=%s\n' \
    "$exit_code" "$STDERR_LOG" >&2
  exit "$exit_code"
}

python3 - \
  "$CORE_OUTPUT" "$STDOUT_LOG" "$STDERR_LOG" "$OUTPUT" \
  "$SCALA_ROOT" "$S1_ROOT" "$SEMANTIC_RECEIPT" "${command[@]}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

core, stdout, stderr, output, scala_root, s1_root, semantic = map(
    Path, sys.argv[1:8]
)
runtime_argv = sys.argv[8:]

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
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

result = json.loads(
    core.read_text(encoding="utf-8"),
    object_pairs_hook=unique_object,
    parse_constant=lambda value: (_ for _ in ()).throw(
        ValueError(f"NONFINITE_JSON:{value}")
    ),
)
if not isinstance(result, dict) or result.get("aggregateStatus") != "PASS":
    raise SystemExit("SOURCE_POLICY_CORE_RESULT_INVALID")
portable = []
for item in runtime_argv:
    if item == str(semantic):
        portable.append("SEMANTIC_RECEIPT")
    elif item == str(output) or item == str(core):
        portable.append("SOURCE_POLICY_RESULT")
    elif item.startswith(f"{scala_root}/"):
        portable.append(f"SCALA_ROOT/{item.removeprefix(f'{scala_root}/')}")
    elif item.startswith(f"{s1_root}/"):
        portable.append(f"S1_ROOT/{item.removeprefix(f'{s1_root}/')}")
    else:
        portable.append(item)
result["coreResultSha256"] = digest(core)
result["process"] = {
    "portableArgv": portable,
    "portableArgvSha256": canonical(portable),
    "runtimeArgvSha256": canonical(runtime_argv),
    "exitCode": 0,
    "stdoutSha256": digest(stdout),
    "stderrSha256": digest(stderr),
    "status": "PASS",
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

printf 'SCALA_SOURCE_POLICY_PASS mode=semanticdb supplemental=token-receiver-audit output=%s\n' \
  "$OUTPUT"
