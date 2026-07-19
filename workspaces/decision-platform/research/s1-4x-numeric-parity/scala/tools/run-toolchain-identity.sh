#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
OUTPUT_DIR=""

usage() {
  printf 'usage: %s --output-dir <new-absolute-directory>\n' "$0" >&2
  exit 64
}

[[ "${1:-}" == "--output-dir" && "$#" -eq 2 ]] || usage
OUTPUT_DIR="$2"
[[ "$OUTPUT_DIR" == /* && ! -e "$OUTPUT_DIR" && ! -L "$OUTPUT_DIR" ]] || usage
mkdir -p "$OUTPUT_DIR"

command=(
  "$SCALA_ROOT/tools/assert-toolchain.sh"
  --lock "$SCALA_ROOT/toolchain-lock.v1.json"
  --merged-provenance "$S1_ROOT/contract/toolchain-provenance.v1.json"
)
set +e
"${command[@]}" \
  >"$OUTPUT_DIR/toolchain.stdout" \
  2>"$OUTPUT_DIR/toolchain.stderr"
exit_code=$?
set -e

python3 - \
  "$SCALA_ROOT/toolchain-lock.v1.json" \
  "$S1_ROOT/contract/toolchain-provenance.v1.json" \
  "$OUTPUT_DIR/toolchain.stdout" \
  "$OUTPUT_DIR/toolchain.stderr" \
  "$OUTPUT_DIR/scala-toolchain-identity-result.v1.json" \
  "$exit_code" "${command[@]}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

lock, provenance, stdout, stderr, output = map(Path, sys.argv[1:6])
exit_code = int(sys.argv[6])
runtime_argv = sys.argv[7:]

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

scala_root = lock.parent
s1_root = scala_root.parent
portable = []
for item in runtime_argv:
    if item.startswith(f"{scala_root}/"):
        portable.append(f"SCALA_ROOT/{item.removeprefix(f'{scala_root}/')}")
    elif item.startswith(f"{s1_root}/"):
        portable.append(f"S1_ROOT/{item.removeprefix(f'{s1_root}/')}")
    else:
        portable.append(item)
result = {
    "schemaVersion": "s1.4x-scala-toolchain-identity-result-v1",
    "toolchainLockSha256": digest(lock),
    "mergedProvenanceSha256": digest(provenance),
    "portableArgv": portable,
    "portableArgvSha256": canonical(portable),
    "runtimeArgvSha256": canonical(runtime_argv),
    "exitCode": exit_code,
    "stdoutSha256": digest(stdout),
    "stderrSha256": digest(stderr),
    "status": "PASS" if exit_code == 0 else "FAIL",
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

[[ "$exit_code" -eq 0 ]] || exit "$exit_code"
printf 'SCALA_TOOLCHAIN_IDENTITY_PASS result=%s\n' \
  "$OUTPUT_DIR/scala-toolchain-identity-result.v1.json"
