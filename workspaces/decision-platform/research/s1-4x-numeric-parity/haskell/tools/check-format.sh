#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/..")"
source "$HASKELL_ROOT/tools/python-runtime.sh"
s1_4x_pin_benchmark_python
"$HASKELL_ROOT/tools/assert-toolchain.sh" >/dev/null

STYLISH_BIN="${S1_4X_STYLISH_BIN:?S1_4X_STYLISH_BIN readiness path is required}"
MANDATED_CONFIGURATION="$HASKELL_ROOT/.stylish-haskell.yaml"
CONFIGURATION="$HASKELL_ROOT/.stylish-haskell-ghc2024-expanded.yaml"
FALLBACK_CONTRACT="$HASKELL_ROOT/stylish-ghc2024-fallback.v1.json"
SOURCE_MANIFEST="$HASKELL_ROOT/source-inputs.v1.json"
MISFORMATTED_FIXTURE="$HASKELL_ROOT/tools/fixtures/stylish/misformatted.hs"
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
  echo "usage: check-format.sh [--output-dir ABSOLUTE_NEW_DIRECTORY]" >&2
  exit 64
fi
trap 'if [[ "${REMOVE_OUTPUT:-0}" -eq 1 ]]; then rm -rf -- "$OUTPUT_DIRECTORY"; fi' EXIT

# pinned formatter가 mandated GHC2024 이름만 거부하는 exact leaf인지 먼저 재현한 뒤,
# 동일 binary의 공식 edition 확장 config만 formatter-only fallback으로 허용한다.
"$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" "$HASKELL_ROOT/tools/stylish_fallback.py" probe \
  --haskell-root "$HASKELL_ROOT" \
  --formatter-bin "$STYLISH_BIN" \
  --output "$OUTPUT_DIRECTORY/parser-capability.receipt.json" \
  >"$OUTPUT_DIRECTORY/parser-capability.stdout" \
  2>"$OUTPUT_DIRECTORY/parser-capability.stderr"

"$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" "$HASKELL_ROOT/tools/haskell_evidence.py" source-inputs \
  --haskell-root "$HASKELL_ROOT" \
  --manifest "$SOURCE_MANIFEST" \
  >"$OUTPUT_DIRECTORY/source-inputs.before.stdout" \
  2>"$OUTPUT_DIRECTORY/source-inputs.before.stderr"

set +e
"$STYLISH_BIN" \
  "--config=$CONFIGURATION" \
  -r \
  "${SOURCE_ROOTS[@]}" \
  >"$OUTPUT_DIRECTORY/positive.stdout" \
  2>"$OUTPUT_DIRECTORY/positive.stderr"
positive_exit_code=$?
set -e
[[ "$positive_exit_code" -eq 0 ]] || {
  printf 'stylish-haskell positive format exit drift: %s\n' "$positive_exit_code" >&2
  exit 2
}

[[ -f "$MISFORMATTED_FIXTURE" && ! -L "$MISFORMATTED_FIXTURE" ]] || {
  echo "stylish-haskell negative fixture is missing or a symlink" >&2
  exit 2
}
fixture_before="$(sha256sum "$MISFORMATTED_FIXTURE" | awk '{print $1}')"
set +e
"$STYLISH_BIN" \
  "--config=$CONFIGURATION" \
  "$MISFORMATTED_FIXTURE" \
  >"$OUTPUT_DIRECTORY/misformatted.stdout" \
  2>"$OUTPUT_DIRECTORY/misformatted.stderr"
misformatted_exit_code=$?
set -e
fixture_after="$(sha256sum "$MISFORMATTED_FIXTURE" | awk '{print $1}')"
[[ "$misformatted_exit_code" -eq 1 ]] || {
  printf 'stylish-haskell negative format exit drift: %s\n' "$misformatted_exit_code" >&2
  exit 2
}
[[ "$fixture_before" == "$fixture_after" ]] || {
  echo "stylish-haskell mutated the negative fixture" >&2
  exit 2
}
"$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" - "$OUTPUT_DIRECTORY/misformatted.stdout" <<'PY'
import sys
from pathlib import Path

formatted = Path(sys.argv[1]).read_text(encoding="utf-8")
required = (
    "module Negative.Misformatted (value) where",
    "import           Data.Maybe (maybe)",
    "value::Maybe Int->Int",
    "value=maybe 0 id",
)
if not all(token in formatted for token in required):
    raise SystemExit("stylish-haskell negative fixture output is incomplete")
PY

PYTHONPATH="$HASKELL_ROOT" "$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" -m unittest -v \
  tools.tests.test_haskell_evidence.HaskellEvidenceTests.test_git_enumerator_rejects_untracked_candidate_source_escape \
  tools.tests.test_haskell_evidence.HaskellEvidenceTests.test_source_manifest_rejects_a_stale_tracked_entry \
  tools.tests.test_haskell_evidence.HaskellEvidenceTests.test_source_manifest_rejects_an_intermediate_directory_symlink \
  >"$OUTPUT_DIRECTORY/source-input-negatives.stdout" \
  2>"$OUTPUT_DIRECTORY/source-input-negatives.stderr"

"$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" "$HASKELL_ROOT/tools/haskell_evidence.py" source-inputs \
  --haskell-root "$HASKELL_ROOT" \
  --manifest "$SOURCE_MANIFEST" \
  >"$OUTPUT_DIRECTORY/source-inputs.after.stdout" \
  2>"$OUTPUT_DIRECTORY/source-inputs.after.stderr"

"$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" - \
  "$OUTPUT_DIRECTORY" \
  "$SOURCE_MANIFEST" \
  "$MANDATED_CONFIGURATION" \
  "$CONFIGURATION" \
  "$FALLBACK_CONTRACT" \
  "$MISFORMATTED_FIXTURE" \
  "$STYLISH_BIN" \
  "$positive_exit_code" \
  "$misformatted_exit_code" \
  "$fixture_before" \
  "$fixture_after" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

(
    output,
    source_manifest,
    mandated_configuration,
    derived_configuration,
    fallback_contract,
    negative_fixture,
    stylish,
) = map(
    Path, sys.argv[1:8]
)
positive_exit_code = int(sys.argv[8])
misformatted_exit_code = int(sys.argv[9])
fixture_before = sys.argv[10]
fixture_after = sys.argv[11]


def sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"formatter receipt input is not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
files = manifest["files"]
paths = sorted(files, key=str.encode)
capability_receipt_path = output / "parser-capability.receipt.json"
capability_receipt = json.loads(
    capability_receipt_path.read_text(encoding="utf-8")
)
if (
    capability_receipt.get("status") != "PASS"
    or capability_receipt.get("fallbackStatus")
    != "PINNED_PARSER_COMPATIBILITY_FALLBACK"
):
    raise SystemExit("formatter parser capability receipt is not accepted")
logs = {
    path.name: sha256(path)
    for path in sorted(output.iterdir(), key=lambda item: item.name.encode())
    if path.is_file() and path.name != "receipt.json"
}
receipt = {
    "schemaVersion": "s1.4x-haskell-format-evidence-v1",
    "formatterPathId": "STYLISH_HASKELL_0_15_1_0",
    "formatterPath": str(stylish.resolve(strict=True)),
    "formatterSha256": sha256(stylish),
    "formatterVersion": "0.15.1.0",
    "mandatedConfigurationPath": str(mandated_configuration.resolve(strict=True)),
    "mandatedConfigurationSha256": sha256(mandated_configuration),
    "derivedConfigurationPath": str(derived_configuration.resolve(strict=True)),
    "derivedConfigurationSha256": sha256(derived_configuration),
    "fallbackContractPath": str(fallback_contract.resolve(strict=True)),
    "fallbackContractSha256": sha256(fallback_contract),
    "parserCapabilityReceiptSha256": sha256(capability_receipt_path),
    "parserCapabilityStatus": capability_receipt["fallbackStatus"],
    "sourceInputManifestSha256": sha256(source_manifest),
    "sourceInputCanonicalManifestSha256Before": manifest["canonicalManifestSha256"],
    "sourceInputCanonicalManifestSha256After": manifest["canonicalManifestSha256"],
    "sourceInputFileCount": len(files),
    "sourceInputPathsSha256": hashlib.sha256(
        "".join(f"{path}\n" for path in paths).encode()
    ).hexdigest(),
    "positiveArgv": [
        str(stylish.resolve(strict=True)),
        f"--config={derived_configuration.resolve(strict=True)}",
        "-r",
        *[
            str((source_manifest.parent / relative).resolve(strict=True))
            for relative in ("src", "app", "test", "benchmark")
        ],
    ],
    "positiveExitCode": positive_exit_code,
    "negativeArgv": [
        str(stylish.resolve(strict=True)),
        f"--config={derived_configuration.resolve(strict=True)}",
        str(negative_fixture.resolve(strict=True)),
    ],
    "negativeFixturePath": "tools/fixtures/stylish/misformatted.hs",
    "negativeFixtureSha256Before": fixture_before,
    "negativeFixtureSha256After": fixture_after,
    "misformattedExitCode": misformatted_exit_code,
    "sourceInputNegativeTests": [
        "untracked-rogue-source",
        "stale-manifest-entry",
        "intermediate-directory-symlink",
    ],
    "logs": logs,
    "fallbackLimitation": capability_receipt["limitation"],
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

printf 'HASKELL_FORMAT_PASS receiptSha256=%s\n' \
  "$(sha256sum "$OUTPUT_DIRECTORY/receipt.json" | awk '{print $1}')"
