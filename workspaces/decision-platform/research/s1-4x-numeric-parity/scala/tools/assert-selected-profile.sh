#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
[[ "${1:-}" == "--benchmark-subject" && "$#" -eq 1 ]] || {
  printf 'usage: %s --benchmark-subject\n' "$0" >&2
  exit 64
}

SELECTED_RESULT="${S1_4X_SCALA_SELECTED_PROFILE_RESULT:?set absolute selected-profile result path}"
QUALIFICATION="${S1_4X_SCALA_QUALIFICATION_RESULT:?set absolute qualification result path}"
CORRECTNESS_ROOT="${S1_4X_SCALA_CORRECTNESS_ROOT:?set absolute A/B/C correctness root}"
SUBJECT_COMMIT="${S1_4X_BENCHMARK_SUBJECT_COMMIT:?set exact benchmark subject commit}"
[[ "$SELECTED_RESULT" == /* && -f "$SELECTED_RESULT" && ! -L "$SELECTED_RESULT" ]] || exit 64
[[ "$QUALIFICATION" == /* && -f "$QUALIFICATION" && ! -L "$QUALIFICATION" ]] || exit 64
[[ "$CORRECTNESS_ROOT" == /* && -d "$CORRECTNESS_ROOT" && ! -L "$CORRECTNESS_ROOT" ]] || exit 64
[[ "$SUBJECT_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 64
[[ "$(git -C "$SCALA_ROOT" rev-parse HEAD)" == "$SUBJECT_COMMIT" ]] || {
  printf 'benchmark subject commit mismatch\n' >&2
  exit 1
}

"$SCALA_ROOT/tools/assert-toolchain.sh"
"$SCALA_ROOT/tools/select-proven-profile.sh" --check \
  --plan "$S1_ROOT/benchmarks/benchmark-plan.v1.json" \
  --qualification "$QUALIFICATION" \
  --correctness-root "$CORRECTNESS_ROOT" \
  --output "$SELECTED_RESULT" >/dev/null

python3 - \
  "$SELECTED_RESULT" \
  "$SCALA_ROOT/compiler-profiles.v1.json" \
  "$SCALA_ROOT/selected-profile.scala" \
  "$SCALA_ROOT/source-inputs.v1.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

result_path, profiles_path, selected_source, manifest_path = map(Path, sys.argv[1:])

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result

def strict_object(path: Path) -> dict:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"NONFINITE_JSON:{token}")
        ),
    )
    if not isinstance(value, dict):
        raise SystemExit(f"JSON_OBJECT_REQUIRED:{path}")
    return value

result = strict_object(result_path)
profiles = strict_object(profiles_path)
manifest = strict_object(manifest_path)

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

profile = result.get("selectedProfileId")
if (
    result.get("schemaVersion") != "s1.4x-scala-selected-profile-result-v1"
    or result.get("selectionStatus") != "PASS"
    or profile not in {"A", "B", "C"}
    or result.get("selectedProfileOptions")
    != profiles["profiles"][profile]["additionalOptions"]
    or result.get("selectedProfileSourceSha256") != digest(selected_source)
    or result.get("sourceInputManifestSha256") != digest(manifest_path)
    or manifest["files"]["selected-profile.scala"]["sha256"] != digest(selected_source)
):
    raise SystemExit("SCALA_SELECTED_PROFILE_CLOSURE_MISMATCH")
PY

printf 'SCALA_SELECTED_PROFILE_PASS profile=%s selectedResultSha256=%s\n' \
  "$(jq -r '.selectedProfileId' "$SELECTED_RESULT")" \
  "$(sha256sum "$SELECTED_RESULT" | awk '{print $1}')"
