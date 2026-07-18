#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/..")"
NUMERIC_ROOT="$(realpath "$HASKELL_ROOT/..")"
"$HASKELL_ROOT/tools/assert-toolchain.sh" >/dev/null

HLINT_BIN="${S1_4X_HLINT_BIN:?S1_4X_HLINT_BIN readiness path is required}"
HLINT_CONFIGURATION="$HASKELL_ROOT/.hlint.yaml"
SOURCE_MANIFEST="$HASKELL_ROOT/source-inputs.v1.json"
EXCEPTION_MANIFEST="$HASKELL_ROOT/lint-exceptions.v1.json"
EXCEPTION_SCHEMA="$NUMERIC_ROOT/contract/schemas/suppression-exception.schema.json"
FIXTURE_MANIFEST="$HASKELL_ROOT/tools/fixtures/hlint-negative.v1.json"
HLINT_ARGS=(
  "--hint=$HLINT_CONFIGURATION"
  --with-group=partial
  --with-group=partial-strict
)
SOURCE_ROOTS=(
  "$HASKELL_ROOT/src"
  "$HASKELL_ROOT/app"
  "$HASKELL_ROOT/test"
  "$HASKELL_ROOT/benchmark"
)

if [[ "$#" -eq 0 ]]; then
  OUTPUT_DIRECTORY="$(mktemp -d)"
  REMOVE_OUTPUT=1
elif [[ "$#" -eq 2 && "$1" == "--output-dir" && "$2" == /* && ! -e "$2" ]]; then
  OUTPUT_DIRECTORY="$2"
  mkdir -m 700 "$OUTPUT_DIRECTORY"
  REMOVE_OUTPUT=0
else
  echo "usage: check-hlint.sh [--output-dir ABSOLUTE_NEW_DIRECTORY]" >&2
  exit 64
fi
trap 'if [[ "${REMOVE_OUTPUT:-0}" -eq 1 ]]; then rm -rf -- "$OUTPUT_DIRECTORY"; fi' EXIT

python3 "$HASKELL_ROOT/tools/haskell_evidence.py" source-inputs \
  --haskell-root "$HASKELL_ROOT" \
  --manifest "$SOURCE_MANIFEST" \
  >"$OUTPUT_DIRECTORY/source-inputs.before.stdout" \
  2>"$OUTPUT_DIRECTORY/source-inputs.before.stderr"

"$HLINT_BIN" "${HLINT_ARGS[@]}" "${SOURCE_ROOTS[@]}" \
  >"$OUTPUT_DIRECTORY/positive.stdout" \
  2>"$OUTPUT_DIRECTORY/positive.stderr"

set +e
"$HLINT_BIN" "${HLINT_ARGS[@]}" --show --json "${SOURCE_ROOTS[@]}" \
  >"$OUTPUT_DIRECTORY/ignored.json" \
  2>"$OUTPUT_DIRECTORY/ignored.stderr"
ignored_exit_code=$?
set -e
[[ "$ignored_exit_code" -eq 1 ]] || {
  printf 'HLint ignored-diagnostic inventory exit drift: %s\n' "$ignored_exit_code" >&2
  exit 2
}

python3 - \
  "$EXCEPTION_SCHEMA" \
  "$EXCEPTION_MANIFEST" \
  >"$OUTPUT_DIRECTORY/exception-schema.stdout" \
  2>"$OUTPUT_DIRECTORY/exception-schema.stderr" <<'PY'
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

schema_path, manifest_path = map(Path, sys.argv[1:])
schema = json.loads(schema_path.read_text(encoding="utf-8"))
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
Draft202012Validator.check_schema(schema)
Draft202012Validator(schema).validate(manifest)
PY

python3 "$HASKELL_ROOT/tools/hlint_inventory.py" \
  --haskell-root "$HASKELL_ROOT" \
  --configuration "$HLINT_CONFIGURATION" \
  --manifest "$EXCEPTION_MANIFEST" \
  --diagnostics "$OUTPUT_DIRECTORY/ignored.json" \
  >"$OUTPUT_DIRECTORY/managed-inventory.stdout" \
  2>"$OUTPUT_DIRECTORY/managed-inventory.stderr"

mapfile -t FIXTURE_ROWS < <(
  python3 - "$FIXTURE_MANIFEST" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if value.get("schemaVersion") != "s1.4x-haskell-hlint-negative-fixtures-v1":
    raise SystemExit("invalid HLint negative fixture manifest")
fixtures = value.get("fixtures")
if not isinstance(fixtures, list) or not fixtures:
    raise SystemExit("empty HLint negative fixture manifest")
seen = set()
for fixture in fixtures:
    if set(fixture) != {"fixtureId", "path", "expectedTokens"}:
        raise SystemExit("HLint negative fixture field drift")
    fixture_id = fixture["fixtureId"]
    path = fixture["path"]
    tokens = fixture["expectedTokens"]
    if (
        not isinstance(fixture_id, str)
        or not fixture_id
        or fixture_id in seen
        or not isinstance(path, str)
        or not path
        or not isinstance(tokens, list)
        or not tokens
        or any(not isinstance(token, str) or not token for token in tokens)
    ):
        raise SystemExit("invalid HLint negative fixture entry")
    seen.add(fixture_id)
    print("\t".join((fixture_id, path, json.dumps(tokens, separators=(",", ":")))))
PY
)

for row in "${FIXTURE_ROWS[@]}"; do
  IFS=$'\t' read -r fixture_id relative_path expected_tokens_json <<<"$row"
  fixture_path="$HASKELL_ROOT/$relative_path"
  [[ -f "$fixture_path" && ! -L "$fixture_path" ]] || {
    printf 'HLint fixture is not a regular file: %s\n' "$relative_path" >&2
    exit 2
  }
  set +e
  "$HLINT_BIN" "${HLINT_ARGS[@]}" "$fixture_path" \
    >"$OUTPUT_DIRECTORY/$fixture_id.stdout" \
    2>"$OUTPUT_DIRECTORY/$fixture_id.stderr"
  exit_code=$?
  set -e
  [[ "$exit_code" -eq 1 ]] || {
    printf 'HLint negative fixture exit drift: %s:%s\n' "$fixture_id" "$exit_code" >&2
    exit 2
  }
  python3 - \
    "$expected_tokens_json" \
    "$OUTPUT_DIRECTORY/$fixture_id.stdout" \
    "$OUTPUT_DIRECTORY/$fixture_id.stderr" <<'PY'
import json
import sys
from pathlib import Path

tokens = json.loads(sys.argv[1])
combined = Path(sys.argv[2]).read_text(encoding="utf-8") + Path(sys.argv[3]).read_text(
    encoding="utf-8"
)
missing = [token for token in tokens if token not in combined]
if missing:
    raise SystemExit(f"HLint expected diagnostics are missing: {missing}")
PY
done

python3 "$HASKELL_ROOT/tools/haskell_evidence.py" source-inputs \
  --haskell-root "$HASKELL_ROOT" \
  --manifest "$SOURCE_MANIFEST" \
  >"$OUTPUT_DIRECTORY/source-inputs.after.stdout" \
  2>"$OUTPUT_DIRECTORY/source-inputs.after.stderr"

python3 - \
  "$OUTPUT_DIRECTORY" \
  "$SOURCE_MANIFEST" \
  "$FIXTURE_MANIFEST" \
  "$EXCEPTION_MANIFEST" \
  "$EXCEPTION_SCHEMA" \
  "$HLINT_CONFIGURATION" \
  "$HLINT_BIN" \
  "$ignored_exit_code" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

(
    output,
    source_manifest,
    fixture_manifest,
    exception_manifest,
    exception_schema,
    configuration,
    hlint,
) = map(Path, sys.argv[1:8])
ignored_exit_code = int(sys.argv[8])


def sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"receipt input is not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


source_inputs = json.loads(source_manifest.read_text(encoding="utf-8"))
files = source_inputs["files"]
logs = {
    path.name: sha256(path)
    for path in sorted(output.iterdir(), key=lambda item: item.name.encode())
    if path.is_file() and path.name != "receipt.json"
}
receipt = {
    "schemaVersion": "s1.4x-haskell-hlint-evidence-v1",
    "hlintPathId": "HLINT_3_10",
    "hlintPath": str(hlint.resolve(strict=True)),
    "hlintSha256": sha256(hlint),
    "hlintVersion": "3.10",
    "configurationSha256": sha256(configuration),
    "sourceInputManifestSha256": sha256(source_manifest),
    "sourceInputCanonicalManifestSha256": source_inputs["canonicalManifestSha256"],
    "sourceInputFileCount": len(files),
    "sourceInputPathsSha256": hashlib.sha256(
        "".join(f"{path}\n" for path in sorted(files, key=str.encode)).encode()
    ).hexdigest(),
    "exceptionManifestSha256": sha256(exception_manifest),
    "exceptionSchemaSha256": sha256(exception_schema),
    "fixtureManifestSha256": sha256(fixture_manifest),
    "positiveArgv": [
        str(hlint.resolve(strict=True)),
        f"--hint={configuration.resolve(strict=True)}",
        "--with-group=partial",
        "--with-group=partial-strict",
        *[
            str((source_manifest.parent / relative).resolve(strict=True))
            for relative in ("src", "app", "test", "benchmark")
        ],
    ],
    "ignoredInventoryArgv": [
        str(hlint.resolve(strict=True)),
        f"--hint={configuration.resolve(strict=True)}",
        "--with-group=partial",
        "--with-group=partial-strict",
        "--show",
        "--json",
        *[
            str((source_manifest.parent / relative).resolve(strict=True))
            for relative in ("src", "app", "test", "benchmark")
        ],
    ],
    "ignoredInventoryExitCode": ignored_exit_code,
    "negativeFixtureCount": 9,
    "logs": logs,
    "status": "PASS",
}
payload = (
    json.dumps(receipt, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    + "\n"
).encode()
descriptor, temporary_name = tempfile.mkstemp(
    dir=output,
    prefix=".receipt.",
    suffix=".tmp",
)
temporary = Path(temporary_name)
try:
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output / "receipt.json")
except BaseException:
    temporary.unlink(missing_ok=True)
    raise
PY

printf 'HASKELL_HLINT_PASS fixtureCount=%s receiptSha256=%s\n' \
  "${#FIXTURE_ROWS[@]}" \
  "$(sha256sum "$OUTPUT_DIRECTORY/receipt.json" | awk '{print $1}')"
