#!/usr/bin/env bash
set -euo pipefail

ALLOW_PENDING_COMPATIBILITY=0
if [[ "$#" -eq 1 && "$1" == "--allow-pending-compatibility" ]]; then
  ALLOW_PENDING_COMPATIBILITY=1
elif [[ "$#" -ne 0 ]]; then
  echo "usage: assert-toolchain.sh [--allow-pending-compatibility]" >&2
  exit 64
fi

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/..")"
NUMERIC_ROOT="$(realpath "$HASKELL_ROOT/..")"

# readiness packet의 runtime path를 명시적으로 주입해 tracked host path와 PATH discovery를 금지한다.
GHCUP_BIN="${S1_4X_GHCUP_BIN:?S1_4X_GHCUP_BIN readiness path is required}"
AUTHORITATIVE_GHC_BIN="${S1_4X_AUTHORITATIVE_GHC_BIN:?S1_4X_AUTHORITATIVE_GHC_BIN readiness path is required}"
LATEST_GHC_BIN="${S1_4X_LATEST_GHC_BIN:?S1_4X_LATEST_GHC_BIN readiness path is required}"
STACK_BIN="${S1_4X_STACK_BIN:?S1_4X_STACK_BIN readiness path is required}"
HLINT_BIN="${S1_4X_HLINT_BIN:?S1_4X_HLINT_BIN readiness path is required}"
STYLISH_BIN="${S1_4X_STYLISH_BIN:?S1_4X_STYLISH_BIN readiness path is required}"

assert_regular_executable() {
  local label="$1"
  local path="$2"
  local expected_sha256="$3"
  [[ "$path" == /* && -f "$path" && -x "$path" && ! -L "$path" ]] || {
    printf '%s executable is not an absolute regular non-symlink: %s\n' "$label" "$path" >&2
    exit 69
  }
  local actual_sha256
  actual_sha256="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual_sha256" == "$expected_sha256" ]] || {
    printf '%s executable SHA-256 mismatch\n' "$label" >&2
    exit 69
  }
}

assert_regular_executable \
  "GHCup" "$GHCUP_BIN" \
  "9ed5da5449b48043a0d17e767c05d2ef585e25a639bb934329496c6d2fad9cf8"
assert_regular_executable \
  "GHC 9.10.3" "$AUTHORITATIVE_GHC_BIN" \
  "d0c0dd79a1bcc5dce3c9e73613c1be51f61b78d5ef7c0970ffe9f142a90a5e2c"
assert_regular_executable \
  "GHC 9.14.1" "$LATEST_GHC_BIN" \
  "ecfd54b4161699f574d2b163bdc817c54df08a08a310323e43b41ab5fc413ef1"
assert_regular_executable \
  "Stack" "$STACK_BIN" \
  "923dbd137756652c67b376e2447c655b87fcc373f4d104b5073bca913471ecbe"
assert_regular_executable \
  "HLint" "$HLINT_BIN" \
  "3ff3fb4b571876d668ddf4ad0245769c19a640283fabb0c2629038aa34197f62"
assert_regular_executable \
  "stylish-haskell" "$STYLISH_BIN" \
  "385dc27bc2d0fb654e76ecadfb57bc0b7e1c58afe74f19923e20b696e6fe0d7b"

[[ "$("$GHCUP_BIN" --numeric-version)" == "0.2.6.2" ]] || {
  echo "GHCup version mismatch" >&2
  exit 69
}
[[ "$("$AUTHORITATIVE_GHC_BIN" --numeric-version)" == "9.10.3" ]] || {
  echo "authoritative GHC version mismatch" >&2
  exit 69
}
[[ "$("$LATEST_GHC_BIN" --numeric-version)" == "9.14.1" ]] || {
  echo "compatibility GHC version mismatch" >&2
  exit 69
}
[[ "$("$STACK_BIN" --numeric-version)" == "3.11.1" ]] || {
  echo "Stack version mismatch" >&2
  exit 69
}
"$HLINT_BIN" --version | grep -E '^HLint (v)?3\.10([ ,.]|$)' >/dev/null || {
  echo "HLint version mismatch" >&2
  exit 69
}
"$STYLISH_BIN" --version | grep -E '^stylish-haskell (v)?0\.15\.1\.0([ .]|$)' >/dev/null || {
  echo "stylish-haskell version mismatch" >&2
  exit 69
}

[[ "$("$GHCUP_BIN" --offline whereis ghc 9.10.3)" == "$AUTHORITATIVE_GHC_BIN" ]] || {
  echo "GHCup authoritative GHC path mismatch" >&2
  exit 69
}
[[ "$("$GHCUP_BIN" --offline whereis ghc 9.14.1)" == "$LATEST_GHC_BIN" ]] || {
  echo "GHCup compatibility GHC path mismatch" >&2
  exit 69
}
[[ "$("$GHCUP_BIN" --offline whereis stack 3.11.1)" == "$STACK_BIN" ]] || {
  echo "GHCup Stack path mismatch" >&2
  exit 69
}

# GHCup의 실제 run resolver가 direct binary 검사와 같은 버전을 선택해야 한다.
[[ "$("$GHCUP_BIN" --offline run --quick --ghc 9.10.3 --stack 3.11.1 -- ghc --numeric-version)" \
  == "9.10.3" ]] || {
  echo "GHCup authoritative run resolver mismatch" >&2
  exit 69
}
[[ "$("$GHCUP_BIN" --offline run --quick --ghc 9.10.3 --stack 3.11.1 -- stack --numeric-version)" \
  == "3.11.1" ]] || {
  echo "GHCup Stack run resolver mismatch" >&2
  exit 69
}
[[ "$("$GHCUP_BIN" --offline run --quick --ghc 9.14.1 --stack 3.11.1 -- ghc --numeric-version)" \
  == "9.14.1" ]] || {
  echo "GHCup compatibility run resolver mismatch" >&2
  exit 69
}

PROVENANCE="$NUMERIC_ROOT/contract/toolchain-provenance.v1.json"
PROVENANCE_SCHEMA="$NUMERIC_ROOT/contract/schemas/toolchain-provenance.schema.json"
LOCK="$HASKELL_ROOT/toolchain-lock.v1.json"

compatibility_status="$(
python3 - \
  "$LOCK" \
  "$PROVENANCE" \
  "$PROVENANCE_SCHEMA" \
  "$HASKELL_ROOT/stack.yaml" \
  "$HASKELL_ROOT/stack-ghc-9.14.1.yaml" \
  "$HASKELL_ROOT/stack.yaml.lock" <<'PY'
import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"toolchain input is not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_json(path: Path):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SystemExit(f"duplicate JSON key: {path}:{key}")
            result[key] = value
        return result

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=object_pairs,
        parse_constant=lambda token: (_ for _ in ()).throw(
            SystemExit(f"non-finite JSON token: {path}:{token}")
        ),
    )


lock_path, provenance_path, schema_path, stack_path, compatibility_path, stack_lock_path = (
    map(Path, sys.argv[1:])
)
lock = strict_json(lock_path)
provenance = strict_json(provenance_path)

expected_outer_keys = {
    "schemaVersion",
    "snapshot",
    "mergedToolchainProvenance",
    "contractProjection",
    "resolvedTools",
    "resolverAssertions",
    "compatibilityPlan",
    "stackConfigurations",
}
if set(lock) != expected_outer_keys:
    raise SystemExit("Haskell toolchain lock outer field drift")
if lock["schemaVersion"] != "s1.4x-haskell-toolchain-lock-v1":
    raise SystemExit("Haskell toolchain lock schema version drift")
if lock["snapshot"] != "lts-24.50":
    raise SystemExit("Haskell toolchain snapshot drift")

expected_merged = {
    "path": "contract/toolchain-provenance.v1.json",
    "sha256": "cd9e29a22473fba6203daa4f3a0cbaa57b8b6e5c5fc22de05ca0801c404ffa98",
    "schemaPath": "contract/schemas/toolchain-provenance.schema.json",
    "schemaSha256": "6dc1701aa04903d4b611929da83fef0a02645c846654dca213811c8b941376bd",
}
if lock["mergedToolchainProvenance"] != expected_merged:
    raise SystemExit("merged toolchain provenance identity drift")
if sha256(provenance_path) != expected_merged["sha256"]:
    raise SystemExit("merged toolchain provenance SHA-256 mismatch")
if sha256(schema_path) != expected_merged["schemaSha256"]:
    raise SystemExit("toolchain provenance schema SHA-256 mismatch")

projection_fields = (
    "stackPolicy",
    "stackInstallCommand",
    "ghcupToolId",
    "ghcupVersion",
    "ghcupReleaseUri",
    "ghcupAssetUri",
    "ghcupAssetSha256",
    "ghcupMetadataCommit",
    "ghcupMetadataUri",
    "ghcupMetadataRawUri",
    "ghcupMetadataRawSha256",
    "stackDistributionChannel",
    "stackArchiveUri",
    "stackArchiveSha256",
    "stackBinPathId",
    "stackBinResolver",
    "stackBinSha256",
    "stackNumericVersion",
    "upstreamStandaloneAssetUri",
    "upstreamStandaloneAssetSha256",
    "upstreamStandaloneAssetRole",
)
expected_projection = {field: provenance[field] for field in projection_fields}
if lock["contractProjection"] != expected_projection:
    raise SystemExit("structured toolchain provenance projection drift")

gate_payload = provenance["gate0MetadataEntryEvidence"]["payload"]
for projected, payload_key in (
    ("ghcupMetadataCommit", "metadataCommit"),
    ("ghcupMetadataRawSha256", "metadataRawSha256"),
    ("ghcupMetadataRawUri", "metadataRawUri"),
    ("stackArchiveSha256", "stackArchiveSha256"),
    ("stackArchiveUri", "stackArchiveUri"),
):
    if expected_projection[projected] != gate_payload[payload_key]:
        raise SystemExit(f"Gate0 provenance projection mismatch: {projected}")

expected_tools = {
    "ghcup": {
        "pathId": "GHCUP_0_2_6_2_LINUX_X86_64",
        "version": "0.2.6.2",
        "sha256": "9ed5da5449b48043a0d17e767c05d2ef585e25a639bb934329496c6d2fad9cf8",
    },
    "authoritativeGhc": {
        "pathId": "GHCUP_GHC_9_10_3",
        "version": "9.10.3",
        "sha256": "d0c0dd79a1bcc5dce3c9e73613c1be51f61b78d5ef7c0970ffe9f142a90a5e2c",
    },
    "compatibilityGhc": {
        "pathId": "GHCUP_GHC_9_14_1",
        "version": "9.14.1",
        "sha256": "ecfd54b4161699f574d2b163bdc817c54df08a08a310323e43b41ab5fc413ef1",
    },
    "stack": {
        "pathId": "GHCUP_STACK_3_11_1",
        "version": "3.11.1",
        "sha256": "923dbd137756652c67b376e2447c655b87fcc373f4d104b5073bca913471ecbe",
    },
    "hlint": {
        "pathId": "HLINT_3_10",
        "version": "3.10",
        "sha256": "3ff3fb4b571876d668ddf4ad0245769c19a640283fabb0c2629038aa34197f62",
    },
    "stylishHaskell": {
        "pathId": "STYLISH_HASKELL_0_15_1_0",
        "version": "0.15.1.0",
        "sha256": "385dc27bc2d0fb654e76ecadfb57bc0b7e1c58afe74f19923e20b696e6fe0d7b",
    },
}
if lock["resolvedTools"] != expected_tools:
    raise SystemExit("resolved Haskell tool identity drift")
if expected_tools["ghcup"]["pathId"] != expected_projection["ghcupToolId"]:
    raise SystemExit("GHCup installed/provenance path identity drift")
if expected_tools["ghcup"]["version"] != expected_projection["ghcupVersion"]:
    raise SystemExit("GHCup installed/provenance version drift")
if expected_tools["ghcup"]["sha256"] != expected_projection["ghcupAssetSha256"]:
    raise SystemExit("GHCup installed/provenance binary drift")
if expected_tools["stack"]["pathId"] != expected_projection["stackBinPathId"]:
    raise SystemExit("Stack installed/provenance path identity drift")
if expected_tools["stack"]["version"] != expected_projection["stackNumericVersion"]:
    raise SystemExit("Stack installed/provenance version drift")
if expected_tools["stack"]["sha256"] != expected_projection["stackBinSha256"]:
    raise SystemExit("Stack installed/provenance binary drift")

expected_resolvers = {
    "authoritativeGhc": [
        "--offline", "run", "--quick", "--ghc", "9.10.3", "--stack", "3.11.1", "--", "ghc",
        "--numeric-version",
    ],
    "authoritativeStack": [
        "--offline", "run", "--quick", "--ghc", "9.10.3", "--stack", "3.11.1", "--", "stack",
        "--numeric-version",
    ],
    "compatibilityGhc": [
        "--offline", "run", "--quick", "--ghc", "9.14.1", "--stack", "3.11.1", "--", "ghc",
        "--numeric-version",
    ],
}
if lock["resolverAssertions"] != expected_resolvers:
    raise SystemExit("GHCup run resolver assertion drift")

expected_compatibility_plan = {
    "status": "PENDING_SOLVE",
    "acceptedMode": False,
    "stackYamlPath": "haskell/stack-ghc-9.14.1.yaml",
    "stackYamlSha256": sha256(compatibility_path),
    "stackLockPath": "haskell/stack-ghc-9.14.1.yaml.lock",
    "stackLockSha256": None,
    "failureResultPath": None,
    "failureResultSha256": None,
}
if lock["compatibilityPlan"] != expected_compatibility_plan:
    raise SystemExit("provisional GHC 9.14 compatibility plan drift")

expected_configurations = {
    "authoritativePath": "haskell/stack.yaml",
    "authoritativeSha256": sha256(stack_path),
    "compatibilityPath": "haskell/stack-ghc-9.14.1.yaml",
    "compatibilitySha256": sha256(compatibility_path),
    "compatibilityLockPath": "haskell/stack-ghc-9.14.1.yaml.lock",
    "compatibilityLockSha256": None,
    "authoritativeLockPath": "haskell/stack.yaml.lock",
    "authoritativeLockSha256": sha256(stack_lock_path),
}
if lock["stackConfigurations"] != expected_configurations:
    raise SystemExit("Stack configuration identity drift")
print(lock["compatibilityPlan"]["status"])
PY
)"

grep -Fx 'snapshot: lts-24.50' "$HASKELL_ROOT/stack.yaml" >/dev/null
grep -Fx 'compiler: ghc-9.10.3' "$HASKELL_ROOT/stack.yaml" >/dev/null
grep -Fx 'compiler-check: match-exact' "$HASKELL_ROOT/stack.yaml" >/dev/null
grep -Fx 'system-ghc: true' "$HASKELL_ROOT/stack.yaml" >/dev/null
grep -Fx 'install-ghc: false' "$HASKELL_ROOT/stack.yaml" >/dev/null
grep -Fx 'compiler: ghc-9.14.1' "$HASKELL_ROOT/stack-ghc-9.14.1.yaml" >/dev/null
grep -Fx 'compiler-check: match-exact' "$HASKELL_ROOT/stack-ghc-9.14.1.yaml" >/dev/null
grep -Fx 'system-ghc: true' "$HASKELL_ROOT/stack-ghc-9.14.1.yaml" >/dev/null
grep -Fx 'install-ghc: false' "$HASKELL_ROOT/stack-ghc-9.14.1.yaml" >/dev/null

if [[ "$compatibility_status" == "PENDING_SOLVE" \
  && "$ALLOW_PENDING_COMPATIBILITY" -ne 1 ]]; then
  echo "HASKELL_TOOLCHAIN_PENDING: GHC 9.14 compatibility solve is not acceptance-eligible" >&2
  exit 78
fi

printf 'HASKELL_TOOLCHAIN_PASS ghcup=%s stack=%s ghc=%s compatibilityGhc=%s compatibilityStatus=%s acceptedMode=%s lockSha256=%s\n' \
  "$GHCUP_BIN" \
  "$STACK_BIN" \
  "$AUTHORITATIVE_GHC_BIN" \
  "$LATEST_GHC_BIN" \
  "$compatibility_status" \
  "$([[ "$compatibility_status" == "PENDING_SOLVE" ]] && printf false || printf true)" \
  "$(sha256sum "$LOCK" | awk '{print $1}')"
