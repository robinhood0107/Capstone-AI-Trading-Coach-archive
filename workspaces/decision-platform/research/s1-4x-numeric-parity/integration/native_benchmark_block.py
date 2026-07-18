#!/usr/bin/env python3
"""Scala/Haskell native aggregate를 frozen 공통 block-result로 변환하고 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmark_input_ledger import validate_input_ledger
from executable_identity import (
    ExecutableIdentityError,
    InspectedExecutable,
    inspect_executable_path,
    inspect_regular_file_path,
)
from gate import GateError, exclusive_json_write, strict_json_load

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))

from benchmark_contract import (  # type: ignore[import-not-found]  # noqa: E402
    ContractError,
    sha256_file,
)
from validate_benchmark_report import (  # type: ignore[import-not-found]  # noqa: E402
    validate_block_result,
    validate_plan,
)

SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$"
)
NATIVE_FIELDS = {
    "schemaVersion",
    "boundaryId",
    "selectorId",
    "nativeBenchmarkMode",
    "nativeTimeUnit",
    "profile",
    "artifactSha256",
    "sourceTreeSha256",
    "toolchainLockSha256",
    "effectiveRuntimeArgumentsSha256",
    "inputLedgerSha256",
    "nativeContractValidationSha256",
    "startedAt",
    "finishedAt",
    "cases",
    "status",
}
NATIVE_CASE_FIELDS = {
    "caseId",
    "nativeValue",
    "samples",
    "warmupIterations",
    "measurementIterations",
}
UNIT_TO_NS = {"ns": 1.0, "us": 1_000.0, "ms": 1_000_000.0, "s": 1_000_000_000.0}
NATIVE_CONTRACT_FIELDS = {
    "schemaVersion",
    "boundaryId",
    "selectorId",
    "framework",
    "frameworkVersion",
    "configuration",
    "cases",
    "status",
}
NATIVE_CONTRACT_CASE_FIELDS = {
    "caseId",
    "nativeSampleCount",
    "rawEvidencePath",
    "rawEvidenceSha256",
    "executionReceiptPath",
    "executionReceiptSha256",
    "status",
}
EXECUTION_RECEIPT_FIELDS = {
    "schemaVersion",
    "boundaryId",
    "selectorId",
    "caseId",
    "commandArgv",
    "environment",
    "exitCode",
    "rawEvidencePath",
    "rawEvidenceSha256",
    "provenance",
    "status",
}
EXECUTION_PROVENANCE_FIELDS = {
    "planPath",
    "planSha256",
    "fixtureRootPath",
    "fixtureFreezeIdentitySha256",
    "inputLedgerPath",
    "inputLedgerSha256",
    "selectorId",
    "caseIds",
    "benchmarkExecutablePath",
    "benchmarkExecutableSha256",
    "effectiveRuntimeArgumentsSha256",
    "candidateProvenance",
}
NATIVE_STATISTICS_CASE_FIELDS = {
    "caseId",
    "nativeSampleCount",
    "nativeP95",
    "confidenceLevel",
    "confidenceLow",
    "confidenceHigh",
    "dispersionMetric",
    "dispersionValue",
    "nativeUnit",
    "logicalOperationsPerInvocation",
    "normalizedP95NsPerLogicalOperation",
    "normalizedConfidenceLowNsPerLogicalOperation",
    "normalizedConfidenceHighNsPerLogicalOperation",
    "normalizedDispersionNsPerLogicalOperation",
}
HASKELL_SELECTED_PROFILE_FIELDS = {
    "schemaVersion",
    "profileId",
    "ghcOptions",
    "compilerVersion",
    "compilerSha256",
    "sourceTreeSha256",
    "optionsSha256",
    "fullCorrectnessSha256",
    "qualificationPlanSha256",
    "qualificationArtifactSha256",
    "selectorConfigSha256",
    "fallbackProfile",
    "selectedBy",
}
HASKELL_SOURCE_MANIFEST_FIELDS = {
    "schemaVersion",
    "language",
    "files",
    "inputSets",
    "canonicalManifestSha256",
}
HASKELL_SOURCE_INPUT_SETS = {
    "tracked": "files",
    "manifest": "files",
    "format": "files",
    "compile": "files",
    "lint": "files",
    "profileRun": "files",
}
HASKELL_CANDIDATE_ROOTS = ("src", "app", "test", "benchmark")
HASKELL_SOURCE_CONFIGURATION_PATHS = (
    "package.yaml",
    "selected-profile.v1.json",
)
HASKELL_BENCHMARK_CONFIGURATION_PATHS = (
    "package.yaml",
    "s1-4x-haskell.cabal",
    "stack.yaml",
    "stack.yaml.lock",
)
HASKELL_FORBIDDEN_COMPILED_SUFFIXES = (".lhs", ".hsc", ".hs-boot")
HASKELL_RUNTIME_IDENTITY_FIELDS = {
    "schemaVersion",
    "boundaryId",
    "selectorId",
    "executedBenchmarkPath",
    "executedBenchmarkSha256",
    "status",
}
SCALA_RUNTIME_SOURCE_PATHS = (
    "benchmarks/scala/ai/trading/coach/s14x/benchmark/BenchmarkInvocation.scala",
    "benchmarks/scala/ai/trading/coach/s14x/benchmark/BenchmarkSetupProbeMain.scala",
    "benchmarks/scala/ai/trading/coach/s14x/benchmark/JvmForkEvidence.scala",
    "benchmarks/scala/s1_4x/benchmarks/classical_path_risk/ClassicalPathRiskBenchmark.scala",
    "benchmarks/scala/s1_4x/benchmarks/coverage_batch/CoverageBatchBenchmark.scala",
    "benchmarks/scala/s1_4x/benchmarks/intraday_realized/IntradayRealizedBenchmark.scala",
    "benchmarks/scala/s1_4x/benchmarks/path_transform/PathTransformBenchmark.scala",
    "benchmarks/scala/s1_4x/benchmarks/probabilistic_scalar/ProbabilisticScalarBenchmark.scala",
    "benchmarks/scala/s1_4x/benchmarks/serial_sharpe/SerialSharpeBenchmark.scala",
    "project.scala",
    "selected-profile.scala",
    "src/main/scala/ai/trading/coach/s14x/core/AdvancedRisk.scala",
    "src/main/scala/ai/trading/coach/s14x/core/Models.scala",
    "src/main/scala/ai/trading/coach/s14x/core/NumericPrimitives.scala",
    "src/main/scala/ai/trading/coach/s14x/core/ProductionMetrics.scala",
    "src/main/scala/ai/trading/coach/s14x/core/StableError.scala",
    "src/main/scala/ai/trading/coach/s14x/core/Validation.scala",
    "src/main/scala/ai/trading/coach/s14x/shell/BinaryArrayReader.scala",
    "src/main/scala/ai/trading/coach/s14x/shell/CandidateRunner.scala",
    "src/main/scala/ai/trading/coach/s14x/shell/ContractDecoder.scala",
    "src/main/scala/ai/trading/coach/s14x/shell/JsonSupport.scala",
    "src/main/scala/ai/trading/coach/s14x/shell/Main.scala",
)
SCALA_SOURCE_MANIFEST_FIELDS = {
    "schemaVersion",
    "language",
    "files",
    "inputSets",
    "canonicalManifestSha256",
}
SCALA_SOURCE_INPUT_SETS = {
    "tracked": "files",
    "manifest": "files",
    "format": "files",
    "compile": "files",
    "lint": "files",
    "profileRun": "files",
}
SCALA_COMPILER_PROFILE_FIELDS = {
    "schemaVersion",
    "scalaVersion",
    "jdkRelease",
    "projectPackage",
    "baseOptionGroups",
    "profiles",
    "warningNegativeFixtures",
    "diagnosticOnlyOptions",
    "forbiddenOptions",
    "fallbackProfile",
}
SCALA_SELECTED_RESULT_FIELDS = {
    "schemaVersion",
    "benchmarkPlanSha256",
    "selectorConfigSha256",
    "qualificationSha256",
    "sourceInputManifestSha256",
    "compilerProfilesSha256",
    "toolchainLockSha256",
    "mergedToolchainProvenanceSha256",
    "scalaCliBinarySha256",
    "javaExecutableSha256",
    "jvmArgumentAllowlistSha256",
    "effectiveJvmArgumentsCapabilitySha256",
    "profileOptionsSha256",
    "selectedProfileSourceSha256",
    "selectedProfileOptions",
    "selectedProfileOptionsSha256",
    "correctnessResultSha256",
    "profiles",
    "selectedProfileId",
    "fallbackProfileId",
    "fallbackExecuted",
    "selectionStatus",
}
SCALA_JVM_ALLOWLIST_FIELDS = {
    "schemaVersion",
    "benchmarkPlanSha256",
    "capabilitySmokePlanSha256",
    "toolchainLockSha256",
    "javaExecutablePathId",
    "javaExecutableSha256",
    "runtimeVersion",
    "vendor",
    "plannedCliJvmArguments",
    "effectiveJvmArguments",
    "stableSystemProperties",
    "ambientJvmOptionVariables",
    "systemPropertiesSha256",
    "environmentAllowlistSha256",
    "smokeForkEvidenceSha256",
    "effectiveArgumentsSha256",
    "status",
}
SCALA_JVM_FORK_FIELDS = {
    "schemaVersion",
    "forkIndex",
    "javaExecutablePathId",
    "javaExecutableSha256",
    "runtimeVersion",
    "vendor",
    "javaHomePathId",
    "inputArguments",
    "stableSystemProperties",
    "ambientJvmOptionVariables",
    "systemPropertiesSha256",
    "environmentAllowlistSha256",
    "runtimeClasspathSha256",
    "evidenceSha256",
}
SCALA_EFFECTIVE_JVM_FIELDS = {
    "schemaVersion",
    "policyId",
    "jvmArgumentAllowlistSha256",
    "capabilitySmokePlanSha256",
    "javaExecutablePathId",
    "javaExecutableSha256",
    "effectiveJvmArguments",
    "forkEvidenceSha256",
    "forkCount",
    "effectiveArgumentsSha256",
    "aggregateStatus",
}
SCALA_JMH_VALIDATION_FIELDS = {
    "schemaVersion",
    "benchmark",
    "mode",
    "timeUnit",
    "threadCount",
    "forks",
    "warmupIterations",
    "warmupTime",
    "measurementIterations",
    "measurementTime",
    "effectiveJvmArguments",
    "logicalOperationsPerInvocation",
    "rawScoreNsPerInvocation",
    "normalizedScoreNsPerLogicalOperation",
    "nativeValue",
    "rawSampleCount",
    "status",
}
SCALA_JMH_RUN_RESULT_FIELDS = {
    "schemaVersion",
    "profileId",
    "caseId",
    "logicalOperationsPerInvocation",
    "rawScoreNsPerInvocation",
    "normalizedScoreNsPerLogicalOperation",
    "runMode",
    "benchmarkPlanSha256",
    "sourceInputManifestSha256",
    "scalaCliBinarySha256",
    "compilerProfilesSha256",
    "profileOptionsSha256",
    "inputPaths",
    "portableArgv",
    "portableArgvSha256",
    "runtimeArgvSha256",
    "rawNativeJsonSha256",
    "effectiveJvmArgsSha256",
    "jvmArgumentAllowlistSha256",
    "nativeValidationSha256",
    "measurementReadyMarkerSha256",
    "stdoutSha256",
    "stderrSha256",
    "exitCode",
    "status",
    "aggregateStatus",
}
SCALA_MEASUREMENT_MARKER_FIELDS = {
    "schemaVersion",
    "benchmarkPlanSha256",
    "caseId",
    "profileId",
    "runMode",
    "setupStatus",
    "markerCardinality",
}
SCALA_CASE_EVIDENCE_FILES = (
    "native.json",
    "scala-jmh-run-result.v1.json",
    "scala-jmh-native-validation.v1.json",
    "scala-effective-jvm-args-result.v1.json",
    "measurement-ready.v1.json",
    "fork-evidence.normalized.json",
    "jmh.stdout",
    "jmh.stderr",
    "jmh-list.txt",
)
SCALA_CASE_JSON_FILES = frozenset(SCALA_CASE_EVIDENCE_FILES[:6])
SCALA_ARTIFACT_CLOSURE_FIELDS = {
    "sourceTreeSha256",
    "selectedProfileResultSha256",
    "selectedProfileSourceSha256",
    "sourceInputManifestSha256",
    "compilerProfilesSha256",
    "scalaCliBinarySha256",
    "javaExecutableSha256",
    "toolchainLockSha256",
    "mergedToolchainProvenanceSha256",
    "effectiveJvmArgumentsCapabilitySha256",
}
SCALA_CANDIDATE_PROVENANCE_FIELDS = {
    "kind",
    "selectedProfileResultPath",
    "selectedProfileResultSha256",
    "selectedProfileSourcePath",
    "selectedProfileSourceSha256",
    "selectedProfileId",
    "sourceInputManifestPath",
    "sourceInputManifestSha256",
    "compilerProfilesPath",
    "compilerProfilesSha256",
    "toolchainLockPath",
    "toolchainLockSha256",
    "mergedToolchainProvenancePath",
    "mergedToolchainProvenanceSha256",
    "effectiveJvmArgumentsCapabilityPath",
    "effectiveJvmArgumentsCapabilitySha256",
    "scalaCliPath",
    "scalaCliBinarySha256",
    "javaExecutablePath",
    "javaExecutableSha256",
}
SCALA_EXPECTED_STABLE_PROPERTIES = {
    "java.runtime.version": "25.0.3+9-LTS",
    "java.specification.version": "25",
    "java.vendor": "Eclipse Adoptium",
    "java.vm.name": "OpenJDK 64-Bit Server VM",
}
SCALA_EXPECTED_AMBIENT_JVM_OPTIONS = {
    "JAVA_TOOL_OPTIONS": "UNSET",
    "_JAVA_OPTIONS": "UNSET",
    "JDK_JAVA_OPTIONS": "UNSET",
}
SCALA_EXPECTED_BENCHMARK_ENVIRONMENT = {
    "S1_4X_BENCHMARK_CASE_ID": "SET",
    "S1_4X_BENCHMARK_PLAN": "SET",
    "S1_4X_BENCHMARK_PROFILE": "SET",
    "S1_4X_BENCHMARK_RUN_MODE": "SET",
    "S1_4X_EFFECTIVE_JVM_EVIDENCE_DIR": "SET",
    "S1_4X_FIXTURE_ROOT": "SET",
    "S1_4X_MEASUREMENT_READY_MARKER": "SET",
}
CRITERION_MEASUREMENT_KEYS = [
    "time",
    "cpuTime",
    "cycles",
    "iters",
    "allocated",
    "peakMbAllocated",
    "numGcs",
    "bytesCopied",
    "mutatorWallSeconds",
    "mutatorCpuSeconds",
    "gcWallSeconds",
    "gcCpuSeconds",
]
# Criterion 1.6.4.0 analyseSample은 total measTime 0.03초 이상만 bootstrap에 사용한다.
CRITERION_BOOTSTRAP_THRESHOLD_SECONDS = 0.03
FROZEN_SCALA_CLI_SHA256 = "54b93b8401e333095526da5e4853780d5bf37494baa1ba5486e9e643084253d0"
FROZEN_SCALAFIX_SHA256 = "9db6db7359e580de8f4b72cd7c104d70023cf32a278db0c30aefb79c939eb0f3"
FROZEN_SCALAFMT_ARCHIVE_SHA256 = (
    "e7d43a5621074a63a46d5b287d0b0bb0650033deeb836af2b27515b2127476f2"
)
FROZEN_SCALAFMT_EXECUTABLE_SHA256 = (
    "88526f9f4d64c2fb023d54578812419f49e2ec09e30e4fb77443a05f1a59cac0"
)
FROZEN_JAVA_EXECUTABLE_SHA256 = (
    "ac3505f0c58282f00a6585591324a86b038c89cd171105fe42a1a0cf2f13b517"
)
FROZEN_GHCUP_SHA256 = "9ed5da5449b48043a0d17e767c05d2ef585e25a639bb934329496c6d2fad9cf8"
FROZEN_STACK_SHA256 = "923dbd137756652c67b376e2447c655b87fcc373f4d104b5073bca913471ecbe"
FROZEN_GHC_910_SHA256 = "d0c0dd79a1bcc5dce3c9e73613c1be51f61b78d5ef7c0970ffe9f142a90a5e2c"
FROZEN_GHC_914_SHA256 = "ecfd54b4161699f574d2b163bdc817c54df08a08a310323e43b41ab5fc413ef1"
FROZEN_HLINT_SHA256 = "3ff3fb4b571876d668ddf4ad0245769c19a640283fabb0c2629038aa34197f62"
FROZEN_STYLISH_HASKELL_SHA256 = (
    "385dc27bc2d0fb654e76ecadfb57bc0b7e1c58afe74f19923e20b696e6fe0d7b"
)
FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256 = (
    "cd9e29a22473fba6203daa4f3a0cbaa57b8b6e5c5fc22de05ca0801c404ffa98"
)
FROZEN_TOOLCHAIN_PROVENANCE_SCHEMA_SHA256 = (
    "6dc1701aa04903d4b611929da83fef0a02645c846654dca213811c8b941376bd"
)
TOOLCHAIN_PROJECTION_FIELDS = (
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


def _exact_object(value: Any, fields: set[str], *, error: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise GateError(error)
    return value


def _sha256_value(value: Any, *, error: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise GateError(error)
    return value


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _canonical_pairs_sha256(value: Any, *, error: str) -> str:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise GateError(error)
    return hashlib.sha256(
        "".join(
            f"{key}={value[key]}\n"
            for key in sorted(value, key=str.encode)
        ).encode("utf-8")
    ).hexdigest()


def _scala_artifact_closure_sha256(closure: Mapping[str, Any]) -> str:
    """Scala source/profile/tool bytes 전체를 candidate artifact identity로 묶는다."""

    if (
        set(closure) != SCALA_ARTIFACT_CLOSURE_FIELDS
        or any(SHA256.fullmatch(str(value)) is None for value in closure.values())
    ):
        raise GateError("SCALA_NATIVE_ARTIFACT_CLOSURE_INVALID")
    return _canonical_sha256(dict(closure))


def _validate_scala_executable_identities(
    *,
    scala_cli_path: Path,
    java_executable_path: Path,
) -> tuple[InspectedExecutable, InspectedExecutable]:
    scala_cli_snapshot = _snapshot_regular_file(
        scala_cli_path,
        role="scala-cli-executable",
        error="SCALA_NATIVE_SCALA_CLI_IDENTITY_INVALID",
        executable=True,
    )
    java_executable_snapshot = _snapshot_regular_file(
        java_executable_path,
        role="scala-java-executable",
        error="SCALA_NATIVE_JAVA_IDENTITY_INVALID",
        executable=True,
    )
    if scala_cli_snapshot.sha256 != FROZEN_SCALA_CLI_SHA256:
        raise GateError("SCALA_NATIVE_SCALA_CLI_IDENTITY_INVALID")
    if java_executable_snapshot.sha256 != FROZEN_JAVA_EXECUTABLE_SHA256:
        raise GateError("SCALA_NATIVE_JAVA_IDENTITY_INVALID")
    return scala_cli_snapshot, java_executable_snapshot


def _scala_effective_runtime_arguments_sha256(
    *,
    selector_id: str,
    expected_case_ids: list[str],
    profile_id: str,
    profile_options_sha256: str,
    case_receipts: Sequence[Mapping[str, Any]],
) -> str:
    """각 case가 실제 기록한 argv/JVM/portable digest를 순서째 실행 identity로 묶는다."""

    receipt_fields = {
        "caseId",
        "runtimeArgvSha256",
        "effectiveJvmArgsSha256",
        "portableArgvSha256",
    }
    if (
        not selector_id
        or not profile_id
        or SHA256.fullmatch(profile_options_sha256) is None
        or [item.get("caseId") for item in case_receipts] != expected_case_ids
        or any(
            set(item) != receipt_fields
            or any(
                SHA256.fullmatch(str(item.get(field))) is None
                for field in (
                    "runtimeArgvSha256",
                    "effectiveJvmArgsSha256",
                    "portableArgvSha256",
                )
            )
            for item in case_receipts
        )
    ):
        raise GateError("SCALA_NATIVE_RUNTIME_RECEIPT_ORDER_INVALID")
    return _canonical_sha256(
        {
            "selectorId": selector_id,
            "expectedCaseIds": expected_case_ids,
            "profileId": profile_id,
            "profileOptionsSha256": profile_options_sha256,
            "cases": [dict(item) for item in case_receipts],
        }
    )


def _scala_full_runtime_argv(
    *,
    scala_cli: Path,
    scala_root: Path,
    source_paths: list[str],
    scala_cli_arguments: list[str],
    raw_path: Path,
    jmh_include_regex: str,
) -> list[str]:
    """Scala lane full runner와 동일한 exact source/options/JMH argv를 재구성한다."""

    if (
        source_paths != list(SCALA_RUNTIME_SOURCE_PATHS)
        or not isinstance(scala_cli_arguments, list)
        or any(
            not isinstance(argument, str) or not argument
            for argument in scala_cli_arguments
        )
        or not jmh_include_regex
    ):
        raise GateError("SCALA_NATIVE_RUNTIME_ARGV_INPUT_INVALID")
    return [
        str(scala_cli),
        "--power",
        "run",
        *(str(scala_root / relative_path) for relative_path in source_paths),
        "--server=false",
        "--jvm",
        "system",
        "--coursier-validate-checksums",
        *scala_cli_arguments,
        "--jmh",
        "--jmh-version",
        "1.37",
        "--",
        "-bm",
        "avgt",
        "-tu",
        "ns",
        "-t",
        "1",
        "-f",
        "3",
        "-wi",
        "5",
        "-i",
        "10",
        "-w",
        "1s",
        "-r",
        "1s",
        "-rf",
        "json",
        "-rff",
        str(raw_path),
        jmh_include_regex,
    ]


def _parse_utc_timestamp(value: str) -> datetime | None:
    if UTC_TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        return None


def _bound_regular_file(
    *,
    path_value: Any,
    sha256_value: Any,
    error: str,
    expected_path: Path | None = None,
    expected_sha256: str | None = None,
) -> Path:
    if (
        not isinstance(path_value, str)
        or not Path(path_value).is_absolute()
        or SHA256.fullmatch(str(sha256_value)) is None
    ):
        raise GateError(error)
    path = Path(path_value)
    if (
        (expected_path is not None and path != expected_path)
        or path.is_symlink()
        or not path.is_file()
        or sha256_file(path) != sha256_value
        or (expected_sha256 is not None and sha256_value != expected_sha256)
    ):
        raise GateError(error)
    return path


def _snapshot_regular_file(
    path: Path,
    *,
    role: str,
    error: str,
    executable: bool = False,
) -> InspectedExecutable:
    """한 FD에서 읽은 regular-file bytes와 digest를 이후 검증의 단일 입력으로 사용한다."""

    absolute = Path(os.path.abspath(path))
    try:
        snapshot = (
            inspect_executable_path(absolute, role=role)
            if executable
            else inspect_regular_file_path(absolute, role=role)
        )
    except ExecutableIdentityError as exc:
        raise GateError(error) from exc
    if snapshot.path != str(absolute) or snapshot.resolved_path != str(absolute):
        raise GateError(error)
    return snapshot


def _snapshot_json_file(
    path: Path,
    *,
    role: str,
    error: str,
) -> tuple[InspectedExecutable, Any]:
    """JSON hash와 parse가 경로 재개방 없이 같은 regular-file snapshot을 공유한다."""

    snapshot = _snapshot_regular_file(path, role=role, error=error)
    try:
        document = strict_json_load(snapshot.payload)
    except GateError as exc:
        raise GateError(error) from exc
    return snapshot, document


@contextmanager
def _sealed_snapshot_path(
    snapshot: InspectedExecutable,
    *,
    error: str,
) -> Iterator[Path]:
    """검증기가 path API만 제공할 때 unlinked read-only FD로 snapshot bytes를 전달한다."""

    temporary_value = os.environ.get("TMPDIR")
    if not temporary_value or not Path(temporary_value).is_absolute():
        raise GateError(error)
    temporary_root = Path(os.path.abspath(temporary_value))
    try:
        if (
            temporary_root.is_symlink()
            or temporary_root.resolve(strict=True) != temporary_root
            or not temporary_root.is_dir()
        ):
            raise GateError(error)
    except OSError as exc:
        raise GateError(error) from exc
    writer, temporary_name = tempfile.mkstemp(
        prefix=".s1-4x-snapshot-",
        dir=str(temporary_root),
    )
    reader = -1
    try:
        remaining = memoryview(snapshot.payload)
        while remaining:
            written = os.write(writer, remaining)
            if written <= 0:
                raise GateError(error)
            remaining = remaining[written:]
        os.fsync(writer)
        os.fchmod(writer, 0o400)
        reader = os.open(
            f"/proc/self/fd/{writer}",
            os.O_RDONLY | os.O_CLOEXEC,
        )
        os.unlink(temporary_name)
        os.close(writer)
        writer = -1
        yield Path(f"/proc/self/fd/{reader}")
    except OSError as exc:
        raise GateError(error) from exc
    finally:
        if writer >= 0:
            os.close(writer)
        if reader >= 0:
            os.close(reader)
        with suppress(FileNotFoundError):
            os.unlink(temporary_name)


def _validate_plan_snapshot(
    snapshot: InspectedExecutable,
    *,
    error: str,
) -> dict[str, Any]:
    """Frozen plan validator가 snapshot FD만 읽도록 path substitution을 차단한다."""

    try:
        with _sealed_snapshot_path(snapshot, error=error) as snapshot_path:
            plan = validate_plan(snapshot_path)
            if not isinstance(plan, dict):
                raise GateError(error)
            return plan
    except (ContractError, GateError, OSError) as exc:
        raise GateError(error) from exc


def _number(value: Any, *, positive: bool = False) -> float | None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or (positive and float(value) <= 0.0)
    ):
        return None
    return float(value)


def _same_number(left: Any, right: Any) -> bool:
    left_number = _number(left)
    right_number = _number(right)
    return (
        left_number is not None
        and right_number is not None
        and math.isclose(
            left_number,
            right_number,
            rel_tol=1e-12,
            abs_tol=1e-15,
        )
    )


def _nearest_rank_p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _ols_slope(predictors: list[float], responses: list[float]) -> float:
    predictor_mean = statistics.fmean(predictors)
    response_mean = statistics.fmean(responses)
    denominator = math.fsum((predictor - predictor_mean) ** 2 for predictor in predictors)
    if denominator <= 0.0:
        raise GateError("CRITERION_REGRESSION_INPUT_INVALID")
    return (
        math.fsum(
            (predictor - predictor_mean) * (response - response_mean)
            for predictor, response in zip(predictors, responses, strict=True)
        )
        / denominator
    )


def _estimate(value: Any, *, error: str) -> dict[str, float]:
    estimate = _exact_object(
        value,
        {"estPoint", "estError"},
        error=error,
    )
    interval = _exact_object(
        estimate["estError"],
        {"confIntLDX", "confIntUDX", "confIntCL"},
        error=error,
    )
    point = _number(estimate["estPoint"])
    lower_distance = _number(interval["confIntLDX"])
    upper_distance = _number(interval["confIntUDX"])
    significance = _number(interval["confIntCL"])
    if (
        point is None
        or lower_distance is None
        or lower_distance < 0.0
        or upper_distance is None
        or upper_distance < 0.0
        or significance is None
        or not 0.0 < significance < 1.0
        or point - lower_distance < 0.0
    ):
        raise GateError(error)
    return {
        "point": point,
        "confidenceLevel": 1.0 - significance,
        "confidenceLow": point - lower_distance,
        "confidenceHigh": point + upper_distance,
    }


def _validate_native_statistics_case(
    value: Any,
    *,
    case_id: str,
    expected: Mapping[str, Any],
    error: str,
) -> None:
    statistics_case = _exact_object(
        value,
        NATIVE_STATISTICS_CASE_FIELDS,
        error=error,
    )
    expected_confidence_level = expected["confidenceLevel"]
    actual_confidence_level = statistics_case["confidenceLevel"]
    if (
        statistics_case["caseId"] != case_id
        or statistics_case["nativeSampleCount"] != expected["nativeSampleCount"]
        or (expected_confidence_level is None and actual_confidence_level is not None)
        or (
            expected_confidence_level is not None
            and not _same_number(
                actual_confidence_level,
                expected_confidence_level,
            )
        )
        or statistics_case["dispersionMetric"] != expected["dispersionMetric"]
        or statistics_case["nativeUnit"] != expected["nativeUnit"]
        or not _same_number(statistics_case["nativeP95"], expected["nativeP95"])
        or not _same_number(
            statistics_case["confidenceLow"],
            expected["confidenceLow"],
        )
        or not _same_number(
            statistics_case["confidenceHigh"],
            expected["confidenceHigh"],
        )
        or not _same_number(
            statistics_case["dispersionValue"],
            expected["dispersionValue"],
        )
        or (
            "logicalOperationsPerInvocation" in expected
            and statistics_case["logicalOperationsPerInvocation"]
            != expected["logicalOperationsPerInvocation"]
        )
        or (
            "normalizedP95NsPerLogicalOperation" in expected
            and not _same_number(
                statistics_case["normalizedP95NsPerLogicalOperation"],
                expected["normalizedP95NsPerLogicalOperation"],
            )
        )
        or (
            "normalizedConfidenceLowNsPerLogicalOperation" in expected
            and not _same_number(
                statistics_case[
                    "normalizedConfidenceLowNsPerLogicalOperation"
                ],
                expected["normalizedConfidenceLowNsPerLogicalOperation"],
            )
        )
        or (
            "normalizedConfidenceHighNsPerLogicalOperation" in expected
            and not _same_number(
                statistics_case[
                    "normalizedConfidenceHighNsPerLogicalOperation"
                ],
                expected["normalizedConfidenceHighNsPerLogicalOperation"],
            )
        )
        or (
            "normalizedDispersionNsPerLogicalOperation" in expected
            and not _same_number(
                statistics_case["normalizedDispersionNsPerLogicalOperation"],
                expected["normalizedDispersionNsPerLogicalOperation"],
            )
        )
    ):
        raise GateError(error)


def _validate_scala_toolchain_lock(
    value: Any,
    *,
    s1_4x_root: Path,
    error: str,
    project_sha256: str | None = None,
    scalafmt_config_sha256: str | None = None,
    merged_provenance_value: Any | None = None,
) -> None:
    lock = _exact_object(
        value,
        {
            "schemaVersion",
            "language",
            "mergedToolchainProvenancePath",
            "mergedToolchainProvenanceSha256",
            "jdk",
            "scalaCli",
            "scala",
            "scalafmt",
            "scalafix",
            "sharedDistributionProvenance",
        },
        error=error,
    )
    jdk = _exact_object(
        lock["jdk"],
        {
            "javaHomePathId",
            "implementor",
            "runtimeVersion",
            "vmName",
            "javaExecutableSha256",
        },
        error=error,
    )
    scala_cli = _exact_object(
        lock["scalaCli"],
        {"pathId", "version", "binarySha256", "defaultScalaVersion"},
        error=error,
    )
    scala = _exact_object(
        lock["scala"],
        {"version", "projectPath", "projectSha256"},
        error=error,
    )
    scalafmt = _exact_object(
        lock["scalafmt"],
        {
            "version",
            "configPath",
            "configSha256",
            "runnerPathId",
            "archiveUri",
            "archivePathId",
            "archiveSha256",
            "executablePathId",
            "executableSha256",
            "resolvedVersionOutput",
            "resolutionLogUri",
            "resolutionLogSha256",
            "networkPolicy",
        },
        error=error,
    )
    scalafix = _exact_object(
        lock["scalafix"],
        {"pathId", "version", "binarySha256"},
        error=error,
    )
    project_path = s1_4x_root / "scala/project.scala"
    scalafmt_path = s1_4x_root / "scala/.scalafmt.conf"
    merged_provenance_path = s1_4x_root / "contract/toolchain-provenance.v1.json"
    merged_provenance = (
        merged_provenance_value
        if merged_provenance_value is not None
        else strict_json_load(merged_provenance_path)
    )
    actual_project_sha256 = (
        project_sha256
        if project_sha256 is not None
        else sha256_file(project_path)
    )
    actual_scalafmt_config_sha256 = (
        scalafmt_config_sha256
        if scalafmt_config_sha256 is not None
        else sha256_file(scalafmt_path)
    )
    if not isinstance(merged_provenance, dict) or any(
        field not in merged_provenance for field in TOOLCHAIN_PROJECTION_FIELDS
    ):
        raise GateError(error)
    scala_projection_fields = tuple(
        field
        for field in TOOLCHAIN_PROJECTION_FIELDS
        if field not in {"ghcupReleaseUri", "ghcupAssetUri", "upstreamStandaloneAssetUri"}
    )
    expected_shared = {
        field: merged_provenance[field] for field in scala_projection_fields
    }
    expected_prefix = "workspaces/decision-platform/research/s1-4x-numeric-parity"
    if (
        lock["schemaVersion"] != "s1.4x-scala-toolchain-lock-v1"
        or lock["language"] != "scala"
        or lock["mergedToolchainProvenancePath"]
        != f"{expected_prefix}/contract/toolchain-provenance.v1.json"
        or lock["mergedToolchainProvenanceSha256"] != FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256
        or jdk["javaHomePathId"] != "TEMURIN_25_0_3_9_LTS"
        or jdk["implementor"] != "Eclipse Adoptium"
        or jdk["runtimeVersion"] != "25.0.3+9-LTS"
        or jdk["vmName"] != "OpenJDK 64-Bit Server VM"
        or jdk["javaExecutableSha256"] != FROZEN_JAVA_EXECUTABLE_SHA256
        or scala_cli
        != {
            "pathId": "SCALA_CLI_1_15_0",
            "version": "1.15.0",
            "binarySha256": FROZEN_SCALA_CLI_SHA256,
            "defaultScalaVersion": "3.8.4",
        }
        or scala["version"] != "3.8.4"
        or scala["projectPath"] != f"{expected_prefix}/scala/project.scala"
        or scala["projectSha256"] != actual_project_sha256
        or scalafmt["version"] != "3.11.4"
        or scalafmt["configPath"] != f"{expected_prefix}/scala/.scalafmt.conf"
        or scalafmt["configSha256"] != actual_scalafmt_config_sha256
        or scalafmt["runnerPathId"] != "SCALA_CLI_1_15_0"
        or scalafmt["archiveUri"]
        != (
            "https://github.com/scalameta/scalafmt/releases/download/"
            "v3.11.4/scalafmt-x86_64-pc-linux.zip"
        )
        or scalafmt["archivePathId"]
        != (
            "S1_4X_CACHE_ROOT/coursier/https/github.com/scalameta/scalafmt/"
            "releases/download/v3.11.4/scalafmt-x86_64-pc-linux.zip"
        )
        or scalafmt["archiveSha256"] != FROZEN_SCALAFMT_ARCHIVE_SHA256
        or scalafmt["executablePathId"]
        != (
            "COURSIER_ARCHIVE_CACHE/https/github.com/scalameta/scalafmt/"
            "releases/download/v3.11.4/scalafmt-x86_64-pc-linux.zip/scalafmt"
        )
        or scalafmt["executableSha256"] != FROZEN_SCALAFMT_EXECUTABLE_SHA256
        or scalafmt["resolvedVersionOutput"] != "scalafmt 3.11.4"
        or scalafmt["resolutionLogUri"]
        != "evidence://s1-4x-scala-scalafmt-evidence-9c3cb8f-01/logs/first-apply.stderr"
        or scalafmt["resolutionLogSha256"]
        != "1cc7516d57c230f10242f43884f12f3d26cbd6d681dbaed317262148c136b781"
        or scalafmt["networkPolicy"] != "OFFLINE_PINNED_LAUNCHER"
        or scalafix
        != {
            "pathId": "SCALAFIX_0_14_7",
            "version": "0.14.7",
            "binarySha256": FROZEN_SCALAFIX_SHA256,
        }
        or lock["sharedDistributionProvenance"] != expected_shared
    ):
        raise GateError(error)


def _validate_scala_compiler_profiles(
    value: Any,
    *,
    error: str,
) -> dict[str, Any]:
    profiles_document = _exact_object(
        value,
        SCALA_COMPILER_PROFILE_FIELDS,
        error=error,
    )
    profiles = profiles_document["profiles"]
    expected_profiles = {
        "A": {
            "profileName": "baseline",
            "additionalOptions": [],
            "scalaCliArguments": [],
        },
        "B": {
            "profileName": "opt",
            "additionalOptions": ["-opt"],
            "scalaCliArguments": ["--scalac-option=-opt"],
        },
        "C": {
            "profileName": "opt-own-source-inline",
            "additionalOptions": [
                "-opt",
                "-opt-inline:ai.trading.coach.s14x.**",
            ],
            "scalaCliArguments": [
                "--scalac-option=-opt",
                "--scalac-option=-opt-inline:ai.trading.coach.s14x.**",
            ],
        },
    }
    if (
        profiles_document["schemaVersion"]
        != "s1.4x-scala-compiler-profiles-v1"
        or profiles_document["scalaVersion"] != "3.8.4"
        or profiles_document["jdkRelease"] != "25"
        or profiles_document["projectPackage"] != "ai.trading.coach.s14x"
        or profiles != expected_profiles
        or profiles_document["fallbackProfile"] != "A"
        or not isinstance(profiles_document["baseOptionGroups"], list)
        or not profiles_document["baseOptionGroups"]
        or not isinstance(profiles_document["warningNegativeFixtures"], list)
        or not profiles_document["warningNegativeFixtures"]
        or not isinstance(profiles_document["diagnosticOnlyOptions"], list)
        or not isinstance(profiles_document["forbiddenOptions"], list)
    ):
        raise GateError(error)
    return profiles_document


def _validate_scala_source_manifest(
    value: Any,
    *,
    scala_root: Path,
    error: str,
) -> tuple[dict[str, Any], dict[str, InspectedExecutable], str]:
    """Manifest 전체와 full JMH exact 22-source closure를 같은 snapshot으로 검증한다."""

    manifest = _exact_object(
        value,
        SCALA_SOURCE_MANIFEST_FIELDS,
        error=error,
    )
    files = manifest["files"]
    if (
        manifest["schemaVersion"] != "s1.4x-source-input-manifest-v1"
        or manifest["language"] != "scala"
        or manifest["inputSets"] != SCALA_SOURCE_INPUT_SETS
        or not isinstance(files, dict)
        or not files
        or list(files) != sorted(files, key=str.encode)
        or SHA256.fullmatch(str(manifest["canonicalManifestSha256"])) is None
    ):
        raise GateError(error)
    snapshots: dict[str, InspectedExecutable] = {}
    manifest_lines: list[str] = []
    runtime_paths: list[str] = []
    source_tree_entries: list[dict[str, str]] = []
    for relative_path, raw_metadata in files.items():
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or Path(relative_path).is_absolute()
            or ".." in Path(relative_path).parts
        ):
            raise GateError(error)
        metadata = _exact_object(
            raw_metadata,
            {"role", "sha256"},
            error=error,
        )
        role = metadata["role"]
        if role not in {"configuration", "main", "benchmark", "test"}:
            raise GateError(error)
        path = scala_root / relative_path
        snapshot = _snapshot_regular_file(
            path,
            role=f"scala-source:{relative_path}",
            error=error,
        )
        if metadata["sha256"] != snapshot.sha256:
            raise GateError(error)
        snapshots[relative_path] = snapshot
        manifest_lines.append(f"{snapshot.sha256}  {relative_path}\n")
        if role in {"configuration", "main", "benchmark"}:
            runtime_paths.append(relative_path)
            source_tree_entries.append(
                {"path": relative_path, "sha256": snapshot.sha256}
            )
    if (
        tuple(runtime_paths) != SCALA_RUNTIME_SOURCE_PATHS
        or len(runtime_paths) != 22
        or manifest["canonicalManifestSha256"]
        != hashlib.sha256("".join(manifest_lines).encode("utf-8")).hexdigest()
    ):
        raise GateError(error)
    expected_runtime_set = set(SCALA_RUNTIME_SOURCE_PATHS)
    actual_runtime_candidates = {
        path.relative_to(scala_root).as_posix()
        for candidate_root in (scala_root / "benchmarks", scala_root / "src/main")
        for path in candidate_root.rglob("*.scala")
        if path.is_file() and not path.is_symlink()
    } | {"project.scala", "selected-profile.scala"}
    if actual_runtime_candidates != expected_runtime_set:
        raise GateError(error)
    return manifest, snapshots, _canonical_sha256(source_tree_entries)


def _validate_scala_jvm_allowlist(
    value: Any,
    *,
    plan_sha256: str,
    toolchain_lock_sha256: str,
    java_executable_sha256: str,
    error: str,
    capability_smoke_plan_sha256: str | None = None,
) -> dict[str, Any]:
    allowlist = _exact_object(
        value,
        SCALA_JVM_ALLOWLIST_FIELDS,
        error=error,
    )
    effective_arguments = allowlist["effectiveJvmArguments"]
    smoke_hashes = allowlist["smokeForkEvidenceSha256"]
    if (
        allowlist["schemaVersion"]
        != "s1.4x-scala-jvm-argument-allowlist-v1"
        or allowlist["benchmarkPlanSha256"] != plan_sha256
        or (
            capability_smoke_plan_sha256 is not None
            and allowlist["capabilitySmokePlanSha256"]
            != capability_smoke_plan_sha256
        )
        or allowlist["toolchainLockSha256"] != toolchain_lock_sha256
        or allowlist["javaExecutablePathId"]
        != "TEMURIN_25_0_3_9_LTS/bin/java"
        or allowlist["javaExecutableSha256"] != java_executable_sha256
        or allowlist["runtimeVersion"] != "25.0.3+9-LTS"
        or allowlist["vendor"] != "Eclipse Adoptium"
        or allowlist["plannedCliJvmArguments"] != []
        or not isinstance(effective_arguments, list)
        or any(not isinstance(item, str) for item in effective_arguments)
        or allowlist["stableSystemProperties"]
        != SCALA_EXPECTED_STABLE_PROPERTIES
        or allowlist["ambientJvmOptionVariables"]
        != SCALA_EXPECTED_AMBIENT_JVM_OPTIONS
        or allowlist["systemPropertiesSha256"]
        != _canonical_pairs_sha256(
            SCALA_EXPECTED_STABLE_PROPERTIES,
            error=error,
        )
        or allowlist["environmentAllowlistSha256"]
        != _canonical_pairs_sha256(
            SCALA_EXPECTED_BENCHMARK_ENVIRONMENT,
            error=error,
        )
        or not isinstance(smoke_hashes, list)
        or len(smoke_hashes) != 1
        or SHA256.fullmatch(str(smoke_hashes[0])) is None
        or SHA256.fullmatch(str(allowlist["capabilitySmokePlanSha256"])) is None
        or allowlist["effectiveArgumentsSha256"]
        != _canonical_sha256(effective_arguments)
        or allowlist["status"] != "PASS"
    ):
        raise GateError(error)
    return allowlist


def _validate_scala_selected_result(
    value: Any,
    *,
    plan: Mapping[str, Any],
    plan_sha256: str,
    selected_source_sha256: str,
    source_manifest_sha256: str,
    compiler_profiles_sha256: str,
    compiler_profiles: Mapping[str, Any],
    toolchain_lock_sha256: str,
    merged_provenance_sha256: str,
    jvm_allowlist_sha256: str,
    scala_cli_sha256: str,
    java_executable_sha256: str,
    error: str,
) -> tuple[dict[str, Any], str, list[str], str]:
    selected = _exact_object(
        value,
        SCALA_SELECTED_RESULT_FIELDS,
        error=error,
    )
    profile_id = selected["selectedProfileId"]
    profile_contracts = compiler_profiles["profiles"]
    profile_contract = (
        profile_contracts.get(profile_id)
        if isinstance(profile_contracts, dict)
        else None
    )
    selected_options = (
        profile_contract.get("additionalOptions")
        if isinstance(profile_contract, dict)
        else None
    )
    scala_cli_arguments = (
        profile_contract.get("scalaCliArguments")
        if isinstance(profile_contract, dict)
        else None
    )
    correctness = selected["correctnessResultSha256"]
    profile_results = selected["profiles"]
    all_profile_options = {
        profile: profile_contracts[profile]["additionalOptions"]
        for profile in ("A", "B", "C")
    }
    if (
        selected["schemaVersion"]
        != "s1.4x-scala-selected-profile-result-v1"
        or selected["benchmarkPlanSha256"] != plan_sha256
        or SHA256.fullmatch(str(selected["selectorConfigSha256"])) is None
        or SHA256.fullmatch(str(selected["qualificationSha256"])) is None
        or selected["sourceInputManifestSha256"] != source_manifest_sha256
        or selected["compilerProfilesSha256"] != compiler_profiles_sha256
        or selected["toolchainLockSha256"] != toolchain_lock_sha256
        or selected["mergedToolchainProvenanceSha256"]
        != merged_provenance_sha256
        or selected["scalaCliBinarySha256"] != scala_cli_sha256
        or selected["javaExecutableSha256"] != java_executable_sha256
        or selected["jvmArgumentAllowlistSha256"] != jvm_allowlist_sha256
        or selected["effectiveJvmArgumentsCapabilitySha256"]
        != jvm_allowlist_sha256
        or selected["selectedProfileSourceSha256"]
        != selected_source_sha256
        or profile_id not in {"A", "B", "C"}
        or selected_options is None
        or scala_cli_arguments is None
        or selected["selectedProfileOptions"] != selected_options
        or selected["selectedProfileOptionsSha256"]
        != _canonical_sha256(selected_options)
        or selected["profileOptionsSha256"]
        != _canonical_sha256(all_profile_options)
        or not isinstance(correctness, dict)
        or set(correctness) != {"A", "B", "C"}
        or any(SHA256.fullmatch(str(item)) is None for item in correctness.values())
        or not isinstance(profile_results, dict)
        or set(profile_results) != {"A", "B", "C"}
        or selected["fallbackProfileId"] != "A"
        or type(selected["fallbackExecuted"]) is not bool
        or selected["fallbackExecuted"] != (profile_id == "A")
        or selected["selectionStatus"] != "PASS"
    ):
        raise GateError(error)
    return (
        selected,
        str(profile_id),
        list(scala_cli_arguments),
        str(selected["selectedProfileOptionsSha256"]),
    )


def _validate_haskell_toolchain_lock(
    value: Any,
    *,
    s1_4x_root: Path,
    error: str,
) -> None:
    lock = _exact_object(
        value,
        {
            "schemaVersion",
            "snapshot",
            "mergedToolchainProvenance",
            "contractProjection",
            "resolvedTools",
            "resolverAssertions",
            "compatibilityPlan",
            "stackConfigurations",
        },
        error=error,
    )
    merged = _exact_object(
        lock["mergedToolchainProvenance"],
        {"path", "sha256", "schemaPath", "schemaSha256"},
        error=error,
    )
    tools = _exact_object(
        lock["resolvedTools"],
        {
            "ghcup",
            "authoritativeGhc",
            "compatibilityGhc",
            "stack",
            "hlint",
            "stylishHaskell",
        },
        error=error,
    )
    ghcup = _exact_object(
        tools["ghcup"],
        {"pathId", "version", "sha256"},
        error=error,
    )
    stack = _exact_object(
        tools["stack"],
        {"pathId", "version", "sha256"},
        error=error,
    )
    authoritative_ghc = _exact_object(
        tools["authoritativeGhc"],
        {"pathId", "version", "sha256"},
        error=error,
    )
    compatibility_ghc = _exact_object(
        tools["compatibilityGhc"],
        {"pathId", "version", "sha256"},
        error=error,
    )
    hlint = _exact_object(
        tools["hlint"],
        {"pathId", "version", "sha256"},
        error=error,
    )
    stylish = _exact_object(
        tools["stylishHaskell"],
        {"pathId", "version", "sha256"},
        error=error,
    )
    merged_provenance_path = s1_4x_root / "contract/toolchain-provenance.v1.json"
    merged_schema_path = s1_4x_root / "contract/schemas/toolchain-provenance.schema.json"
    merged_provenance = strict_json_load(merged_provenance_path)
    if not isinstance(merged_provenance, dict) or any(
        field not in merged_provenance for field in TOOLCHAIN_PROJECTION_FIELDS
    ):
        raise GateError(error)
    expected_projection = {
        field: merged_provenance[field] for field in TOOLCHAIN_PROJECTION_FIELDS
    }
    if (
        lock["schemaVersion"] != "s1.4x-haskell-toolchain-lock-v1"
        or lock["snapshot"] != "lts-24.50"
        or merged
        != {
            "path": "contract/toolchain-provenance.v1.json",
            "sha256": FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256,
            "schemaPath": "contract/schemas/toolchain-provenance.schema.json",
            "schemaSha256": FROZEN_TOOLCHAIN_PROVENANCE_SCHEMA_SHA256,
        }
        or sha256_file(merged_provenance_path)
        != FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256
        or sha256_file(merged_schema_path)
        != FROZEN_TOOLCHAIN_PROVENANCE_SCHEMA_SHA256
        or lock["contractProjection"] != expected_projection
        or ghcup
        != {
            "pathId": "GHCUP_0_2_6_2_LINUX_X86_64",
            "version": "0.2.6.2",
            "sha256": FROZEN_GHCUP_SHA256,
        }
        or stack
        != {
            "pathId": "GHCUP_STACK_3_11_1",
            "version": "3.11.1",
            "sha256": FROZEN_STACK_SHA256,
        }
        or authoritative_ghc
        != {
            "pathId": "GHCUP_GHC_9_10_3",
            "version": "9.10.3",
            "sha256": FROZEN_GHC_910_SHA256,
        }
        or compatibility_ghc
        != {
            "pathId": "GHCUP_GHC_9_14_1",
            "version": "9.14.1",
            "sha256": FROZEN_GHC_914_SHA256,
        }
        or hlint
        != {
            "pathId": "HLINT_3_10",
            "version": "3.10",
            "sha256": FROZEN_HLINT_SHA256,
        }
        or stylish
        != {
            "pathId": "STYLISH_HASKELL_0_15_1_0",
            "version": "0.15.1.0",
            "sha256": FROZEN_STYLISH_HASKELL_SHA256,
        }
        or lock["resolverAssertions"]
        != {
            "authoritativeGhc": [
                "--offline",
                "run",
                "--quick",
                "--ghc",
                "9.10.3",
                "--stack",
                "3.11.1",
                "--",
                "ghc",
                "--numeric-version",
            ],
            "authoritativeStack": [
                "--offline",
                "run",
                "--quick",
                "--ghc",
                "9.10.3",
                "--stack",
                "3.11.1",
                "--",
                "stack",
                "--numeric-version",
            ],
            "compatibilityGhc": [
                "--offline",
                "run",
                "--quick",
                "--ghc",
                "9.14.1",
                "--stack",
                "3.11.1",
                "--",
                "ghc",
                "--numeric-version",
            ],
        }
        or not isinstance(lock["compatibilityPlan"], dict)
        or not isinstance(lock["stackConfigurations"], dict)
    ):
        raise GateError(error)


def _validate_haskell_selected_profile(
    value: Any,
    *,
    plan: Mapping[str, Any],
    plan_path: Path,
    error: str,
    plan_sha256: str | None = None,
) -> dict[str, Any]:
    """최종 Haskell profile이 frozen plan/source/compiler identity를 묶는지 검증한다."""

    profile = _exact_object(
        value,
        HASKELL_SELECTED_PROFILE_FIELDS,
        error=error,
    )
    options = profile["ghcOptions"]
    if (
        profile["schemaVersion"] != "s1.4x-haskell-selected-profile-v1"
        or profile["profileId"] not in {
            "baseline-o0-fasm",
            "optimized-o2-fasm",
        }
        or options not in (["-O0", "-fasm"], ["-O2", "-fasm"])
        or (
            profile["profileId"] == "baseline-o0-fasm"
            and options != ["-O0", "-fasm"]
        )
        or (
            profile["profileId"] == "optimized-o2-fasm"
            and options != ["-O2", "-fasm"]
        )
        or profile["compilerVersion"] != "9.10.3"
        or profile["compilerSha256"] != FROZEN_GHC_910_SHA256
        or any(
            SHA256.fullmatch(str(profile[field])) is None
            for field in (
                "sourceTreeSha256",
                "optionsSha256",
                "fullCorrectnessSha256",
                "qualificationPlanSha256",
                "qualificationArtifactSha256",
                "selectorConfigSha256",
            )
        )
        or profile["optionsSha256"] != _canonical_sha256(options)
        or profile["qualificationPlanSha256"]
        != (
            plan_sha256
            if plan_sha256 is not None
            else sha256_file(plan_path)
        )
        or profile["selectorConfigSha256"]
        != _canonical_sha256(plan.get("haskellProfileQualification"))
        or profile["fallbackProfile"] != "baseline-o0-fasm"
        or profile["selectedBy"] not in {
            "frozen-criterion-selector",
            "proven-fallback",
        }
    ):
        raise GateError(error)
    return profile


def _haskell_candidate_source_files(
    haskell_root: Path,
    *,
    error: str,
) -> dict[str, Path]:
    """Generator와 같은 filesystem 규칙으로 후보 Haskell source exact set을 만든다."""

    candidate_files: dict[str, Path] = {}
    for root_name in HASKELL_CANDIDATE_ROOTS:
        source_root = haskell_root / root_name
        if source_root.is_symlink() or not source_root.is_dir():
            raise GateError(error)
        for path in sorted(
            source_root.rglob("*"),
            key=lambda item: item.relative_to(haskell_root).as_posix().encode(),
        ):
            relative_path = path.relative_to(haskell_root).as_posix()
            if path.is_symlink():
                raise GateError(error)
            if not path.is_file():
                continue
            if relative_path.endswith(HASKELL_FORBIDDEN_COMPILED_SUFFIXES):
                raise GateError(error)
            if path.suffix == ".hs":
                candidate_files[relative_path] = path
    if not candidate_files:
        raise GateError(error)
    return candidate_files


def _haskell_source_role(relative_path: str, *, error: str) -> str:
    if relative_path in HASKELL_SOURCE_CONFIGURATION_PATHS:
        return "configuration"
    if relative_path.startswith("test/"):
        return "test"
    if relative_path.startswith("benchmark/"):
        return "benchmark"
    if relative_path.startswith(("src/", "app/")):
        return "main"
    raise GateError(error)


def _validate_haskell_source_manifest(
    value: Any,
    *,
    haskell_root: Path,
    error: str,
) -> dict[str, Any]:
    """Tracked generator의 exact path-set, role, 현재 bytes를 manifest와 대조한다."""

    manifest = _exact_object(
        value,
        HASKELL_SOURCE_MANIFEST_FIELDS,
        error=error,
    )
    files = manifest["files"]
    candidate_files = _haskell_candidate_source_files(haskell_root, error=error)
    required_files = dict(candidate_files)
    for relative_path in HASKELL_SOURCE_CONFIGURATION_PATHS:
        path = haskell_root / relative_path
        if path.is_symlink() or not path.is_file():
            raise GateError(error)
        required_files[relative_path] = path
    if (
        manifest["schemaVersion"] != "s1.4x-source-input-manifest-v1"
        or manifest["language"] != "haskell"
        or manifest["inputSets"] != HASKELL_SOURCE_INPUT_SETS
        or not isinstance(files, dict)
        or set(files) != set(required_files)
        or SHA256.fullmatch(str(manifest["canonicalManifestSha256"])) is None
    ):
        raise GateError(error)
    manifest_lines: list[str] = []
    for relative_path in sorted(required_files, key=str.encode):
        metadata = _exact_object(
            files[relative_path],
            {"role", "sha256"},
            error=error,
        )
        snapshot = _snapshot_regular_file(
            required_files[relative_path],
            role=f"haskell-source:{relative_path}",
            error=error,
        )
        expected_role = _haskell_source_role(relative_path, error=error)
        if (
            metadata["role"] != expected_role
            or SHA256.fullmatch(str(metadata["sha256"])) is None
            or metadata["sha256"] != snapshot.sha256
        ):
            raise GateError(error)
        manifest_lines.append(f"{snapshot.sha256}  {relative_path}\n")
    closure_sha256 = hashlib.sha256("".join(manifest_lines).encode("utf-8")).hexdigest()
    if manifest["canonicalManifestSha256"] != closure_sha256:
        raise GateError(error)
    return manifest


def _haskell_benchmark_source_tree_sha256(
    haskell_root: Path,
    *,
    error: str,
) -> str:
    """Lane profile helper와 같은 candidate/build closure를 현재 bytes에서 재계산한다."""

    source_files = _haskell_candidate_source_files(haskell_root, error=error)
    benchmark_files = dict(source_files)
    for relative_path in HASKELL_BENCHMARK_CONFIGURATION_PATHS:
        path = haskell_root / relative_path
        if path.is_symlink() or not path.is_file():
            raise GateError(error)
        benchmark_files[relative_path] = path
    entries: list[dict[str, str]] = []
    for relative_path in sorted(benchmark_files, key=str.encode):
        snapshot = _snapshot_regular_file(
            benchmark_files[relative_path],
            role=f"haskell-benchmark-source:{relative_path}",
            error=error,
        )
        entries.append(
            {
                "path": relative_path,
                "sha256": snapshot.sha256,
            }
        )
    return _canonical_sha256(entries)


def _snapshot_scala_case_evidence(
    case_directory: Path,
    *,
    case_id: str,
) -> tuple[
    dict[str, tuple[InspectedExecutable, Any]],
    list[tuple[InspectedExecutable, Any]],
]:
    """한 Scala case의 9개 root evidence와 세 raw fork를 FD별 한 번만 읽는다."""

    if (
        case_directory.is_symlink()
        or not case_directory.is_dir()
        or {
            path.name for path in case_directory.iterdir()
        }
        != {*SCALA_CASE_EVIDENCE_FILES, "fork-evidence"}
    ):
        raise GateError(f"SCALA_NATIVE_CASE_DIRECTORY_INVALID:{case_id}")
    evidence: dict[str, tuple[InspectedExecutable, Any]] = {}
    for name in SCALA_CASE_EVIDENCE_FILES:
        path = case_directory / name
        if name in SCALA_CASE_JSON_FILES:
            snapshot, document = _snapshot_json_file(
                path,
                role=f"scala-case:{case_id}:{name}",
                error=f"SCALA_NATIVE_CASE_EVIDENCE_INVALID:{case_id}:{name}",
            )
        else:
            snapshot = _snapshot_regular_file(
                path,
                role=f"scala-case:{case_id}:{name}",
                error=f"SCALA_NATIVE_CASE_EVIDENCE_INVALID:{case_id}:{name}",
            )
            document = None
        evidence[name] = (snapshot, document)
    fork_root = case_directory / "fork-evidence"
    if fork_root.is_symlink() or not fork_root.is_dir():
        raise GateError(f"SCALA_NATIVE_FORK_EVIDENCE_INVALID:{case_id}")
    fork_paths = sorted(fork_root.glob("jvm-fork-*.json"), key=lambda path: path.name)
    if (
        len(fork_paths) != 3
        or {path.name for path in fork_root.iterdir()}
        != {path.name for path in fork_paths}
    ):
        raise GateError(f"SCALA_NATIVE_FORK_EVIDENCE_INVALID:{case_id}")
    raw_forks = [
        _snapshot_json_file(
            path,
            role=f"scala-case:{case_id}:raw-fork:{index}",
            error=f"SCALA_NATIVE_FORK_EVIDENCE_INVALID:{case_id}",
        )
        for index, path in enumerate(fork_paths, start=1)
    ]
    return evidence, raw_forks


def _validate_scala_effective_jvm_evidence(
    *,
    normalized_value: Any,
    raw_forks: list[tuple[InspectedExecutable, Any]],
    effective_value: Any,
    allowlist: Mapping[str, Any],
    allowlist_sha256: str,
    java_executable_sha256: str,
    case_id: str,
) -> None:
    normalized = normalized_value
    if not isinstance(normalized, list) or len(normalized) != 3:
        raise GateError(f"SCALA_NATIVE_FORK_EVIDENCE_INVALID:{case_id}")
    evidence_hashes: list[str] = []
    for index, (normalized_fork, raw_snapshot_and_value) in enumerate(
        zip(normalized, raw_forks, strict=True),
        start=1,
    ):
        raw_snapshot, raw_value = raw_snapshot_and_value
        if not isinstance(raw_value, dict):
            raise GateError(f"SCALA_NATIVE_FORK_EVIDENCE_INVALID:{case_id}")
        expected_normalized = dict(raw_value)
        process_id = expected_normalized.pop("forkProcessId", None)
        start_time = expected_normalized.pop("runtimeStartTimeEpochMillis", None)
        if (
            expected_normalized.get("schemaVersion")
            != "s1.4x-scala-jvm-fork-raw-evidence-v1"
            or type(process_id) is not int
            or process_id <= 0
            or type(start_time) is not int
            or start_time <= 0
        ):
            raise GateError(f"SCALA_NATIVE_FORK_EVIDENCE_INVALID:{case_id}")
        expected_normalized["schemaVersion"] = (
            "s1.4x-scala-jvm-fork-evidence-v1"
        )
        expected_normalized["forkIndex"] = index
        expected_normalized["evidenceSha256"] = raw_snapshot.sha256
        fork = _exact_object(
            normalized_fork,
            SCALA_JVM_FORK_FIELDS,
            error=f"SCALA_NATIVE_FORK_EVIDENCE_INVALID:{case_id}",
        )
        if (
            fork != expected_normalized
            or fork["forkIndex"] != index
            or fork["javaExecutablePathId"]
            != "TEMURIN_25_0_3_9_LTS/bin/java"
            or fork["javaExecutableSha256"] != java_executable_sha256
            or fork["runtimeVersion"] != "25.0.3+9-LTS"
            or fork["vendor"] != "Eclipse Adoptium"
            or fork["javaHomePathId"] != "TEMURIN_25_0_3_9_LTS"
            or fork["inputArguments"] != allowlist["effectiveJvmArguments"]
            or fork["stableSystemProperties"]
            != SCALA_EXPECTED_STABLE_PROPERTIES
            or fork["ambientJvmOptionVariables"]
            != SCALA_EXPECTED_AMBIENT_JVM_OPTIONS
            or fork["systemPropertiesSha256"]
            != _canonical_pairs_sha256(
                SCALA_EXPECTED_STABLE_PROPERTIES,
                error=f"SCALA_NATIVE_FORK_EVIDENCE_INVALID:{case_id}",
            )
            or fork["environmentAllowlistSha256"]
            != _canonical_pairs_sha256(
                SCALA_EXPECTED_BENCHMARK_ENVIRONMENT,
                error=f"SCALA_NATIVE_FORK_EVIDENCE_INVALID:{case_id}",
            )
            or SHA256.fullmatch(str(fork["runtimeClasspathSha256"])) is None
        ):
            raise GateError(f"SCALA_NATIVE_FORK_EVIDENCE_INVALID:{case_id}")
        evidence_hashes.append(raw_snapshot.sha256)
    effective = _exact_object(
        effective_value,
        SCALA_EFFECTIVE_JVM_FIELDS,
        error=f"SCALA_NATIVE_EFFECTIVE_JVM_EVIDENCE_INVALID:{case_id}",
    )
    expected_effective = {
        "schemaVersion": "s1.4x-scala-effective-jvm-args-result-v1",
        "policyId": "capability-smoke-effective-jvm-args-v1",
        "jvmArgumentAllowlistSha256": allowlist_sha256,
        "capabilitySmokePlanSha256": allowlist[
            "capabilitySmokePlanSha256"
        ],
        "javaExecutablePathId": "TEMURIN_25_0_3_9_LTS/bin/java",
        "javaExecutableSha256": java_executable_sha256,
        "effectiveJvmArguments": allowlist["effectiveJvmArguments"],
        "forkEvidenceSha256": evidence_hashes,
        "forkCount": 3,
        "effectiveArgumentsSha256": allowlist[
            "effectiveArgumentsSha256"
        ],
        "aggregateStatus": "PASS",
    }
    if effective != expected_effective:
        raise GateError(
            f"SCALA_NATIVE_EFFECTIVE_JVM_EVIDENCE_INVALID:{case_id}"
        )


def _scala_native_case_from_raw(
    raw_document: Any,
    *,
    case_id: str,
    jmh_include_regex: str,
    logical_operations: int,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if (
        not isinstance(raw_document, list)
        or len(raw_document) != 1
        or not isinstance(raw_document[0], dict)
    ):
        raise GateError(f"JMH_RAW_DOCUMENT_INVALID:{case_id}")
    result = raw_document[0]
    metric = result.get("primaryMetric")
    raw_data = metric.get("rawData") if isinstance(metric, dict) else None
    score = metric.get("score") if isinstance(metric, dict) else None
    score_number = _number(score, positive=True)
    confidence = (
        metric.get("scoreConfidence") if isinstance(metric, dict) else None
    )
    if (
        type(logical_operations) is not int
        or logical_operations < 1
        or not isinstance(raw_data, list)
        or not isinstance(confidence, list)
        or len(confidence) != 2
        or score_number is None
        or _number(confidence[0]) is None
        or _number(confidence[1]) is None
    ):
        raise GateError(f"JMH_RAW_CONTRACT_INVALID:{case_id}")
    samples = [
        float(sample)
        for fork in raw_data
        if isinstance(fork, list)
        for sample in fork
        if _number(sample, positive=True) is not None
    ]
    if len(samples) != 30:
        raise GateError(f"JMH_RAW_CONTRACT_INVALID:{case_id}")
    native_case = {
        "caseId": case_id,
        "nativeValue": score_number,
        "samples": len(samples),
        "warmupIterations": 5,
        "measurementIterations": 10,
    }
    native_p95 = _nearest_rank_p95(samples)
    dispersion = native_p95 - statistics.median(samples)
    statistics_case = {
        "caseId": case_id,
        "nativeSampleCount": len(samples),
        "nativeP95": native_p95,
        "confidenceLevel": None,
        "confidenceLow": float(confidence[0]),
        "confidenceHigh": float(confidence[1]),
        "dispersionMetric": "p95-minus-median-ns-per-invocation",
        "dispersionValue": dispersion,
        "nativeUnit": "ns",
        "logicalOperationsPerInvocation": logical_operations,
        "normalizedP95NsPerLogicalOperation": (
            native_p95 / logical_operations
        ),
        "normalizedConfidenceLowNsPerLogicalOperation": (
            float(confidence[0]) / logical_operations
        ),
        "normalizedConfidenceHighNsPerLogicalOperation": (
            float(confidence[1]) / logical_operations
        ),
        "normalizedDispersionNsPerLogicalOperation": (
            dispersion / logical_operations
        ),
    }
    _parse_jmh_raw(
        raw_document,
        case_id=case_id,
        jmh_include_regex=jmh_include_regex,
        native_case=native_case,
        native_statistics_case=statistics_case,
    )
    benchmark_name = result.get("benchmark")
    if not isinstance(benchmark_name, str):
        raise GateError(f"JMH_RAW_CASE_SELECTION_INVALID:{case_id}")
    return native_case, statistics_case, benchmark_name


def _validate_scala_case_evidence(
    *,
    case_directory: Path,
    block_directory: Path,
    case_index: int,
    case_id: str,
    logical_operations: int,
    jmh_include_regex: str,
    plan_sha256: str,
    source_manifest_sha256: str,
    compiler_profiles_sha256: str,
    profile_id: str,
    profile_options_sha256: str,
    scala_cli_arguments: list[str],
    scala_cli_snapshot: InspectedExecutable,
    scala_root: Path,
    jvm_allowlist: Mapping[str, Any],
    jvm_allowlist_sha256: str,
    java_executable_sha256: str,
) -> dict[str, Any]:
    evidence, raw_forks = _snapshot_scala_case_evidence(
        case_directory,
        case_id=case_id,
    )
    raw_snapshot, raw_document = evidence["native.json"]
    run_snapshot, run_value = evidence["scala-jmh-run-result.v1.json"]
    validation_snapshot, validation_value = evidence[
        "scala-jmh-native-validation.v1.json"
    ]
    effective_snapshot, effective_value = evidence[
        "scala-effective-jvm-args-result.v1.json"
    ]
    marker_snapshot, marker_value = evidence["measurement-ready.v1.json"]
    normalized_snapshot, normalized_value = evidence[
        "fork-evidence.normalized.json"
    ]
    stdout_snapshot, _ = evidence["jmh.stdout"]
    stderr_snapshot, _ = evidence["jmh.stderr"]
    list_snapshot, _ = evidence["jmh-list.txt"]
    _validate_scala_effective_jvm_evidence(
        normalized_value=normalized_value,
        raw_forks=raw_forks,
        effective_value=effective_value,
        allowlist=jvm_allowlist,
        allowlist_sha256=jvm_allowlist_sha256,
        java_executable_sha256=java_executable_sha256,
        case_id=case_id,
    )
    native_case, statistics_case, benchmark_name = (
        _scala_native_case_from_raw(
            raw_document,
            case_id=case_id,
            jmh_include_regex=jmh_include_regex,
            logical_operations=logical_operations,
        )
    )
    raw_result = raw_document[0]
    if (
        raw_result.get("jvmArgs") != jvm_allowlist["effectiveJvmArguments"]
        or raw_result.get("measurementTime") != "1 s"
        or raw_result.get("warmupTime") != "1 s"
    ):
        raise GateError(f"JMH_RAW_CONTRACT_INVALID:{case_id}")
    metric = raw_result["primaryMetric"]
    expected_validation = {
        "schemaVersion": "s1.4x-scala-jmh-native-validation-v1",
        "benchmark": benchmark_name,
        "mode": "AverageTime",
        "timeUnit": "ns/op",
        "threadCount": 1,
        "forks": 3,
        "warmupIterations": 5,
        "warmupTime": "1 s",
        "measurementIterations": 10,
        "measurementTime": "1 s",
        "effectiveJvmArguments": jvm_allowlist["effectiveJvmArguments"],
        "logicalOperationsPerInvocation": logical_operations,
        "rawScoreNsPerInvocation": float(metric["score"]),
        "normalizedScoreNsPerLogicalOperation": (
            float(metric["score"]) / logical_operations
        ),
        "nativeValue": float(metric["score"]),
        "rawSampleCount": 30,
        "status": "PASS",
    }
    _exact_object(
        validation_value,
        SCALA_JMH_VALIDATION_FIELDS,
        error=f"SCALA_NATIVE_VALIDATION_INVALID:{case_id}",
    )
    if validation_value != expected_validation:
        raise GateError(f"SCALA_NATIVE_VALIDATION_INVALID:{case_id}")
    marker = _exact_object(
        marker_value,
        SCALA_MEASUREMENT_MARKER_FIELDS,
        error=f"SCALA_NATIVE_MEASUREMENT_MARKER_INVALID:{case_id}",
    )
    if marker != {
        "schemaVersion": "s1.4x-scala-measurement-ready-v1",
        "benchmarkPlanSha256": plan_sha256,
        "caseId": case_id,
        "profileId": profile_id,
        "runMode": "full",
        "setupStatus": "PASS",
        "markerCardinality": 1,
    }:
        raise GateError(f"SCALA_NATIVE_MEASUREMENT_MARKER_INVALID:{case_id}")
    try:
        list_text = list_snapshot.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise GateError(f"SCALA_NATIVE_JMH_LIST_INVALID:{case_id}") from exc
    if list_text.splitlines().count(benchmark_name) != 1:
        raise GateError(f"SCALA_NATIVE_JMH_LIST_INVALID:{case_id}")
    expected_argv = _scala_full_runtime_argv(
        scala_cli=Path(scala_cli_snapshot.path),
        scala_root=scala_root,
        source_paths=list(SCALA_RUNTIME_SOURCE_PATHS),
        scala_cli_arguments=scala_cli_arguments,
        raw_path=case_directory / "native.json",
        jmh_include_regex=jmh_include_regex,
    )
    portable_argv = _scala_full_runtime_argv(
        scala_cli=Path("SCALA_CLI_1_15_0"),
        scala_root=Path("SCALA_ROOT"),
        source_paths=list(SCALA_RUNTIME_SOURCE_PATHS),
        scala_cli_arguments=scala_cli_arguments,
        raw_path=Path("EVIDENCE_ROOT/native.json"),
        jmh_include_regex=jmh_include_regex,
    )
    run = _exact_object(
        run_value,
        SCALA_JMH_RUN_RESULT_FIELDS,
        error=f"SCALA_NATIVE_RUN_RECEIPT_INVALID:{case_id}",
    )
    expected_run = {
        "schemaVersion": "s1.4x-scala-jmh-run-result-v1",
        "profileId": profile_id,
        "caseId": case_id,
        "logicalOperationsPerInvocation": logical_operations,
        "rawScoreNsPerInvocation": float(metric["score"]),
        "normalizedScoreNsPerLogicalOperation": (
            float(metric["score"]) / logical_operations
        ),
        "runMode": "full",
        "benchmarkPlanSha256": plan_sha256,
        "sourceInputManifestSha256": source_manifest_sha256,
        "scalaCliBinarySha256": scala_cli_snapshot.sha256,
        "compilerProfilesSha256": compiler_profiles_sha256,
        "profileOptionsSha256": profile_options_sha256,
        "inputPaths": list(SCALA_RUNTIME_SOURCE_PATHS),
        "portableArgv": portable_argv,
        "portableArgvSha256": _canonical_sha256(portable_argv),
        "runtimeArgvSha256": _canonical_sha256(expected_argv),
        "rawNativeJsonSha256": raw_snapshot.sha256,
        "effectiveJvmArgsSha256": effective_snapshot.sha256,
        "jvmArgumentAllowlistSha256": jvm_allowlist_sha256,
        "nativeValidationSha256": validation_snapshot.sha256,
        "measurementReadyMarkerSha256": marker_snapshot.sha256,
        "stdoutSha256": stdout_snapshot.sha256,
        "stderrSha256": stderr_snapshot.sha256,
        "exitCode": 0,
        "status": "PASS",
        "aggregateStatus": "PASS",
    }
    if run != expected_run:
        raise GateError(f"SCALA_NATIVE_RUN_RECEIPT_INVALID:{case_id}")
    expected_case_directory = block_directory / (
        f"scala-jmh/case-{case_index:03d}"
    )
    if case_directory != expected_case_directory:
        raise GateError(f"SCALA_NATIVE_CASE_DIRECTORY_INVALID:{case_id}")
    return {
        "nativeCase": native_case,
        "statisticsCase": statistics_case,
        "rawSnapshot": raw_snapshot,
        "rawDocument": raw_document,
        "runtimeArgv": expected_argv,
        "runtimeReceipt": {
            "caseId": case_id,
            "runtimeArgvSha256": str(run["runtimeArgvSha256"]),
            "effectiveJvmArgsSha256": str(
                run["effectiveJvmArgsSha256"]
            ),
            "portableArgvSha256": str(run["portableArgvSha256"]),
        },
        "rootEvidenceSha256": {
            name: snapshot.sha256
            for name, (snapshot, _) in evidence.items()
        },
        "normalizedForkEvidenceSha256": normalized_snapshot.sha256,
        "runReceiptSha256": run_snapshot.sha256,
    }


def _validate_scala_production_receipt(
    *,
    candidate_provenance: Any,
    arguments: list[str],
    provenance: Mapping[str, Any],
    case_id: str,
    expected_case_ids: list[str],
    selector_id: str,
    block_directory: Path,
    plan: Mapping[str, Any],
    plan_path: Path,
    plan_sha256: str,
    effective_runtime_arguments_sha256: str,
    artifact_sha256: str | None,
    source_tree_sha256: str | None,
    native_toolchain_lock_sha256: str | None,
    profile: str,
) -> str:
    """A/B/C production receipt의 source, tool, 실제 run-result closure를 재검증한다."""

    error = f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}"
    scala_provenance = _exact_object(
        candidate_provenance,
        SCALA_CANDIDATE_PROVENANCE_FIELDS,
        error=error,
    )
    s1_4x_root = plan_path.parent.parent
    scala_root = s1_4x_root / "scala"
    expected_paths = {
        "selectedProfileSourcePath": scala_root / "selected-profile.scala",
        "sourceInputManifestPath": scala_root / "source-inputs.v1.json",
        "compilerProfilesPath": scala_root / "compiler-profiles.v1.json",
        "toolchainLockPath": scala_root / "toolchain-lock.v1.json",
        "mergedToolchainProvenancePath": (
            s1_4x_root / "contract/toolchain-provenance.v1.json"
        ),
    }
    for field, expected_path in expected_paths.items():
        if scala_provenance[field] != str(expected_path):
            raise GateError(error)
    selected_result_path = Path(
        str(scala_provenance["selectedProfileResultPath"])
    )
    selected_source_path = expected_paths["selectedProfileSourcePath"]
    source_manifest_path = expected_paths["sourceInputManifestPath"]
    compiler_profiles_path = expected_paths["compilerProfilesPath"]
    toolchain_lock_path = expected_paths["toolchainLockPath"]
    merged_provenance_path = expected_paths[
        "mergedToolchainProvenancePath"
    ]
    capability_path = Path(
        str(scala_provenance["effectiveJvmArgumentsCapabilityPath"])
    )
    scala_cli_path = Path(str(scala_provenance["scalaCliPath"]))
    java_executable_path = Path(
        str(scala_provenance["javaExecutablePath"])
    )
    if any(
        not path.is_absolute()
        for path in (
            selected_result_path,
            capability_path,
            scala_cli_path,
            java_executable_path,
        )
    ):
        raise GateError(error)
    selected_result_snapshot, selected_result_value = _snapshot_json_file(
        selected_result_path,
        role=f"scala-receipt-selected-result:{case_id}",
        error=error,
    )
    selected_source_snapshot = _snapshot_regular_file(
        selected_source_path,
        role=f"scala-receipt-selected-source:{case_id}",
        error=error,
    )
    source_manifest_snapshot, source_manifest_value = _snapshot_json_file(
        source_manifest_path,
        role=f"scala-receipt-source-manifest:{case_id}",
        error=error,
    )
    compiler_profiles_snapshot, compiler_profiles_value = _snapshot_json_file(
        compiler_profiles_path,
        role=f"scala-receipt-compiler-profiles:{case_id}",
        error=error,
    )
    toolchain_lock_snapshot, toolchain_lock_value = _snapshot_json_file(
        toolchain_lock_path,
        role=f"scala-receipt-toolchain-lock:{case_id}",
        error=error,
    )
    merged_provenance_snapshot, merged_provenance_value = _snapshot_json_file(
        merged_provenance_path,
        role=f"scala-receipt-toolchain-provenance:{case_id}",
        error=error,
    )
    capability_snapshot, capability_value = _snapshot_json_file(
        capability_path,
        role=f"scala-receipt-jvm-capability:{case_id}",
        error=error,
    )
    scala_cli_snapshot = _snapshot_regular_file(
        scala_cli_path,
        role=f"scala-receipt-scala-cli:{case_id}",
        error=error,
        executable=True,
    )
    java_snapshot = _snapshot_regular_file(
        java_executable_path,
        role=f"scala-receipt-java:{case_id}",
        error=error,
        executable=True,
    )
    expected_hashes = {
        "selectedProfileResultSha256": selected_result_snapshot.sha256,
        "selectedProfileSourceSha256": selected_source_snapshot.sha256,
        "sourceInputManifestSha256": source_manifest_snapshot.sha256,
        "compilerProfilesSha256": compiler_profiles_snapshot.sha256,
        "toolchainLockSha256": toolchain_lock_snapshot.sha256,
        "mergedToolchainProvenanceSha256": (
            merged_provenance_snapshot.sha256
        ),
        "effectiveJvmArgumentsCapabilitySha256": (
            capability_snapshot.sha256
        ),
        "scalaCliBinarySha256": scala_cli_snapshot.sha256,
        "javaExecutableSha256": java_snapshot.sha256,
    }
    if (
        scala_provenance["kind"] != "scala"
        or scala_provenance["selectedProfileId"] != profile
        or any(
            scala_provenance[field] != value
            for field, value in expected_hashes.items()
        )
        or scala_cli_snapshot.sha256 != FROZEN_SCALA_CLI_SHA256
        or java_snapshot.sha256 != FROZEN_JAVA_EXECUTABLE_SHA256
        or merged_provenance_snapshot.sha256
        != FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256
    ):
        raise GateError(error)
    _, source_snapshots, computed_source_tree_sha256 = (
        _validate_scala_source_manifest(
            source_manifest_value,
            scala_root=scala_root,
            error=error,
        )
    )
    if (
        source_snapshots["selected-profile.scala"].sha256
        != selected_source_snapshot.sha256
    ):
        raise GateError(error)
    computed_artifact_sha256 = _scala_artifact_closure_sha256(
        {
            "sourceTreeSha256": computed_source_tree_sha256,
            "selectedProfileResultSha256": (
                selected_result_snapshot.sha256
            ),
            "selectedProfileSourceSha256": (
                selected_source_snapshot.sha256
            ),
            "sourceInputManifestSha256": (
                source_manifest_snapshot.sha256
            ),
            "compilerProfilesSha256": compiler_profiles_snapshot.sha256,
            "scalaCliBinarySha256": scala_cli_snapshot.sha256,
            "javaExecutableSha256": java_snapshot.sha256,
            "toolchainLockSha256": toolchain_lock_snapshot.sha256,
            "mergedToolchainProvenanceSha256": (
                merged_provenance_snapshot.sha256
            ),
            "effectiveJvmArgumentsCapabilitySha256": (
                capability_snapshot.sha256
            ),
        }
    )
    if (
        artifact_sha256 != computed_artifact_sha256
        or source_tree_sha256 != computed_source_tree_sha256
        or native_toolchain_lock_sha256
        != toolchain_lock_snapshot.sha256
    ):
        raise GateError(error)
    compiler_profiles = _validate_scala_compiler_profiles(
        compiler_profiles_value,
        error=error,
    )
    scalafmt_config_snapshot = _snapshot_regular_file(
        scala_root / ".scalafmt.conf",
        role=f"scala-receipt-scalafmt-config:{case_id}",
        error=error,
    )
    _validate_scala_toolchain_lock(
        toolchain_lock_value,
        s1_4x_root=s1_4x_root,
        error=error,
        project_sha256=source_snapshots["project.scala"].sha256,
        scalafmt_config_sha256=scalafmt_config_snapshot.sha256,
        merged_provenance_value=merged_provenance_value,
    )
    capability_smoke_plan_snapshot = _snapshot_regular_file(
        s1_4x_root / "contract/capability-smoke-plan.v1.json",
        role=f"scala-receipt-capability-smoke-plan:{case_id}",
        error=error,
    )
    _validate_scala_jvm_allowlist(
        capability_value,
        plan_sha256=plan_sha256,
        toolchain_lock_sha256=toolchain_lock_snapshot.sha256,
        java_executable_sha256=java_snapshot.sha256,
        error=error,
        capability_smoke_plan_sha256=(
            capability_smoke_plan_snapshot.sha256
        ),
    )
    _, selected_profile, scala_cli_arguments, profile_options_sha256 = (
        _validate_scala_selected_result(
            selected_result_value,
            plan=plan,
            plan_sha256=plan_sha256,
            selected_source_sha256=selected_source_snapshot.sha256,
            source_manifest_sha256=source_manifest_snapshot.sha256,
            compiler_profiles_sha256=compiler_profiles_snapshot.sha256,
            compiler_profiles=compiler_profiles,
            toolchain_lock_sha256=toolchain_lock_snapshot.sha256,
            merged_provenance_sha256=merged_provenance_snapshot.sha256,
            jvm_allowlist_sha256=capability_snapshot.sha256,
            scala_cli_sha256=scala_cli_snapshot.sha256,
            java_executable_sha256=java_snapshot.sha256,
            error=error,
        )
    )
    selector = next(
        (
            entry
            for entry in plan.get("familySelectors", [])
            if isinstance(entry, dict)
            and entry.get("selectorId") == selector_id
        ),
        None,
    )
    if (
        selected_profile != profile
        or not isinstance(selector, dict)
        or selector.get("boundaryId") != "scala"
        or selector.get("expectedCaseIds") != expected_case_ids
        or not isinstance(selector.get("jmhIncludeRegex"), str)
        or not selector["jmhIncludeRegex"]
    ):
        raise GateError(error)
    frozen_case_by_id = {
        item.get("caseId"): item
        for item in plan.get("cases", [])
        if isinstance(item, dict)
    }
    runtime_receipts: list[dict[str, str]] = []
    expected_arguments_for_case: list[str] | None = None
    for index, expected_case_id in enumerate(expected_case_ids, start=1):
        case_directory = block_directory / f"scala-jmh/case-{index:03d}"
        raw_path = case_directory / "native.json"
        expected_argv = _scala_full_runtime_argv(
            scala_cli=scala_cli_path,
            scala_root=scala_root,
            source_paths=list(SCALA_RUNTIME_SOURCE_PATHS),
            scala_cli_arguments=scala_cli_arguments,
            raw_path=raw_path,
            jmh_include_regex=str(selector["jmhIncludeRegex"]),
        )
        portable_argv = _scala_full_runtime_argv(
            scala_cli=Path("SCALA_CLI_1_15_0"),
            scala_root=Path("SCALA_ROOT"),
            source_paths=list(SCALA_RUNTIME_SOURCE_PATHS),
            scala_cli_arguments=scala_cli_arguments,
            raw_path=Path("EVIDENCE_ROOT/native.json"),
            jmh_include_regex=str(selector["jmhIncludeRegex"]),
        )
        run_snapshot, run_value = _snapshot_json_file(
            case_directory / "scala-jmh-run-result.v1.json",
            role=f"scala-receipt-run-result:{expected_case_id}",
            error=error,
        )
        del run_snapshot
        run = _exact_object(
            run_value,
            SCALA_JMH_RUN_RESULT_FIELDS,
            error=error,
        )
        effective_snapshot = _snapshot_regular_file(
            case_directory / "scala-effective-jvm-args-result.v1.json",
            role=f"scala-receipt-effective-jvm:{expected_case_id}",
            error=error,
        )
        frozen_case = frozen_case_by_id.get(expected_case_id)
        logical_operations = (
            frozen_case.get("logicalOperationsPerInvocation")
            if isinstance(frozen_case, dict)
            else None
        )
        if (
            type(logical_operations) is not int
            or logical_operations < 1
            or run["schemaVersion"]
            != "s1.4x-scala-jmh-run-result-v1"
            or run["profileId"] != profile
            or run["caseId"] != expected_case_id
            or run["logicalOperationsPerInvocation"] != logical_operations
            or run["runMode"] != "full"
            or run["benchmarkPlanSha256"] != plan_sha256
            or run["sourceInputManifestSha256"]
            != source_manifest_snapshot.sha256
            or run["scalaCliBinarySha256"] != scala_cli_snapshot.sha256
            or run["compilerProfilesSha256"]
            != compiler_profiles_snapshot.sha256
            or run["profileOptionsSha256"] != profile_options_sha256
            or run["inputPaths"] != list(SCALA_RUNTIME_SOURCE_PATHS)
            or run["portableArgv"] != portable_argv
            or run["portableArgvSha256"]
            != _canonical_sha256(portable_argv)
            or run["runtimeArgvSha256"] != _canonical_sha256(expected_argv)
            or run["effectiveJvmArgsSha256"] != effective_snapshot.sha256
            or run["jvmArgumentAllowlistSha256"]
            != capability_snapshot.sha256
            or run["exitCode"] != 0
            or run["status"] != "PASS"
            or run["aggregateStatus"] != "PASS"
        ):
            raise GateError(error)
        runtime_receipts.append(
            {
                "caseId": expected_case_id,
                "runtimeArgvSha256": str(run["runtimeArgvSha256"]),
                "effectiveJvmArgsSha256": str(
                    run["effectiveJvmArgsSha256"]
                ),
                "portableArgvSha256": str(run["portableArgvSha256"]),
            }
        )
        if expected_case_id == case_id:
            expected_arguments_for_case = expected_argv
    aggregate_sha256 = _scala_effective_runtime_arguments_sha256(
        selector_id=selector_id,
        expected_case_ids=expected_case_ids,
        profile_id=profile,
        profile_options_sha256=profile_options_sha256,
        case_receipts=runtime_receipts,
    )
    if (
        expected_arguments_for_case is None
        or arguments != expected_arguments_for_case
        or provenance["benchmarkExecutablePath"] != str(scala_cli_path)
        or provenance["benchmarkExecutableSha256"]
        != scala_cli_snapshot.sha256
        or provenance["effectiveRuntimeArgumentsSha256"]
        != aggregate_sha256
        or effective_runtime_arguments_sha256 != aggregate_sha256
    ):
        raise GateError(f"NATIVE_EXECUTION_ARGV_INVALID:{case_id}")
    return str(selector["jmhIncludeRegex"])


def _validate_execution_receipt(
    *,
    item: Mapping[str, Any],
    boundary_id: str,
    selector_id: str,
    case_id: str,
    expected_case_ids: list[str],
    block_directory: Path,
    plan_path: Path,
    fixture_root_path: Path,
    input_ledger_path: Path,
    effective_runtime_arguments_sha256: str,
    profile: str,
    receipt_snapshots: dict[Path, tuple[InspectedExecutable, Any]],
    plan_document: Mapping[str, Any] | None = None,
    plan_sha256: str | None = None,
    artifact_sha256: str | None = None,
    source_tree_sha256: str | None = None,
    native_toolchain_lock_sha256: str | None = None,
) -> str | None:
    receipt_path_text = item["executionReceiptPath"]
    if (
        not isinstance(receipt_path_text, str)
        or not receipt_path_text
        or Path(receipt_path_text).is_absolute()
        or ".." in Path(receipt_path_text).parts
        or SHA256.fullmatch(str(item["executionReceiptSha256"])) is None
    ):
        raise GateError(f"NATIVE_EXECUTION_RECEIPT_PATH_INVALID:{case_id}")
    receipt_path = block_directory / receipt_path_text
    try:
        receipt_path.resolve(strict=True).relative_to(block_directory.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise GateError(f"NATIVE_EXECUTION_RECEIPT_PATH_INVALID:{case_id}") from exc
    receipt_snapshot_and_document = receipt_snapshots.get(receipt_path)
    if receipt_snapshot_and_document is None:
        receipt_snapshot_and_document = _snapshot_json_file(
            receipt_path,
            role=f"native-execution-receipt:{case_id}",
            error=f"NATIVE_EXECUTION_RECEIPT_DIGEST_INVALID:{case_id}",
        )
        receipt_snapshots[receipt_path] = receipt_snapshot_and_document
    receipt_snapshot, receipt_document = receipt_snapshot_and_document
    if receipt_snapshot.sha256 != item["executionReceiptSha256"]:
        raise GateError(f"NATIVE_EXECUTION_RECEIPT_DIGEST_INVALID:{case_id}")
    receipt = _exact_object(
        receipt_document,
        EXECUTION_RECEIPT_FIELDS,
        error=f"NATIVE_EXECUTION_RECEIPT_INVALID:{case_id}",
    )
    arguments = receipt["commandArgv"]
    provenance = _exact_object(
        receipt["provenance"],
        EXECUTION_PROVENANCE_FIELDS,
        error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
    )
    resolved_plan = plan_path.resolve(strict=True)
    resolved_fixture_root = fixture_root_path.resolve(strict=True)
    resolved_input_ledger = input_ledger_path.resolve(strict=True)
    plan_value: Any = (
        dict(plan_document)
        if plan_document is not None
        else strict_json_load(resolved_plan)
    )
    if not isinstance(plan_value, dict):
        raise GateError(f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}")
    plan = plan_value
    effective_plan_sha256 = (
        plan_sha256
        if plan_sha256 is not None
        else sha256_file(resolved_plan)
    )
    if SHA256.fullmatch(str(effective_plan_sha256)) is None:
        raise GateError(f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}")
    benchmark_executable_text = provenance["benchmarkExecutablePath"]
    if (
        not isinstance(benchmark_executable_text, str)
        or not Path(benchmark_executable_text).is_absolute()
    ):
        raise GateError(f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}")
    benchmark_executable = Path(benchmark_executable_text)
    candidate_provenance = provenance["candidateProvenance"]
    expected_receipt_case_id: str | None = case_id if boundary_id == "scala" else None
    expected_environment = (
        {"S1_4X_BENCHMARK_CASE_ID": case_id}
        if boundary_id == "scala"
        else {"S1_4X_BENCHMARK_SELECTOR_ID": selector_id}
    )
    if (
        receipt["schemaVersion"] != "s1.4x-native-case-execution-receipt-v1"
        or receipt["boundaryId"] != boundary_id
        or receipt["selectorId"] != selector_id
        or receipt["caseId"] != expected_receipt_case_id
        or not isinstance(arguments, list)
        or not arguments
        or not all(isinstance(argument, str) and argument for argument in arguments)
        or receipt["environment"] != expected_environment
        or receipt["exitCode"] != 0
        or receipt["rawEvidencePath"] != item["rawEvidencePath"]
        or receipt["rawEvidenceSha256"] != item["rawEvidenceSha256"]
        or receipt["status"] != "PASS"
        or provenance["planPath"] != str(resolved_plan)
        or provenance["planSha256"] != effective_plan_sha256
        or provenance["fixtureRootPath"] != str(resolved_fixture_root)
        or provenance["fixtureFreezeIdentitySha256"]
        != _canonical_sha256(plan.get("fixtureFreezeIdentity"))
        or provenance["inputLedgerPath"] != str(resolved_input_ledger)
        or provenance["inputLedgerSha256"] != sha256_file(resolved_input_ledger)
        or provenance["selectorId"] != selector_id
        or provenance["caseIds"] != expected_case_ids
        or provenance["effectiveRuntimeArgumentsSha256"] != effective_runtime_arguments_sha256
        or SHA256.fullmatch(str(provenance["benchmarkExecutableSha256"])) is None
        or benchmark_executable.is_symlink()
        or not benchmark_executable.is_file()
        or sha256_file(benchmark_executable) != provenance["benchmarkExecutableSha256"]
        or (boundary_id == "scala" and benchmark_executable_text not in arguments)
    ):
        raise GateError(f"NATIVE_EXECUTION_RECEIPT_INVALID:{case_id}")
    if boundary_id == "scala":
        if profile in {"A", "B", "C"}:
            return _validate_scala_production_receipt(
                candidate_provenance=candidate_provenance,
                arguments=arguments,
                provenance=provenance,
                case_id=case_id,
                expected_case_ids=expected_case_ids,
                selector_id=selector_id,
                block_directory=block_directory,
                plan=plan,
                plan_path=resolved_plan,
                plan_sha256=effective_plan_sha256,
                effective_runtime_arguments_sha256=(
                    effective_runtime_arguments_sha256
                ),
                artifact_sha256=artifact_sha256,
                source_tree_sha256=source_tree_sha256,
                native_toolchain_lock_sha256=(
                    native_toolchain_lock_sha256
                ),
                profile=profile,
            )
        scala_provenance = _exact_object(
            candidate_provenance,
            {
                "kind",
                "selectedProfilePath",
                "selectedProfileSha256",
                "selectedProfileId",
                "sourceInputManifestPath",
                "sourceInputManifestSha256",
                "toolchainLockPath",
                "toolchainLockSha256",
                "mergedToolchainProvenancePath",
                "mergedToolchainProvenanceSha256",
                "effectiveJvmArgumentsCapabilityPath",
                "effectiveJvmArgumentsCapabilitySha256",
            },
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        selected_profile_path_text = scala_provenance["selectedProfilePath"]
        source_manifest_path_text = scala_provenance["sourceInputManifestPath"]
        capability_path_text = scala_provenance["effectiveJvmArgumentsCapabilityPath"]
        s1_4x_root = resolved_plan.parent.parent
        expected_project_path = s1_4x_root / "scala/project.scala"
        expected_profile_path = s1_4x_root / "scala/selected-profile.scala"
        expected_source_manifest_path = s1_4x_root / "scala/source-inputs.v1.json"
        expected_source_path = s1_4x_root / "scala/src/main/scala"
        expected_benchmark_path = s1_4x_root / "scala/benchmarks"
        expected_toolchain_lock_path = s1_4x_root / "scala/toolchain-lock.v1.json"
        expected_merged_provenance_path = s1_4x_root / "contract/toolchain-provenance.v1.json"
        if (
            scala_provenance["kind"] != "scala"
            or provenance["benchmarkExecutableSha256"] != FROZEN_SCALA_CLI_SHA256
            or not isinstance(selected_profile_path_text, str)
            or not Path(selected_profile_path_text).is_absolute()
            or selected_profile_path_text != str(expected_profile_path)
            or SHA256.fullmatch(str(scala_provenance["selectedProfileSha256"])) is None
            or Path(selected_profile_path_text).is_symlink()
            or not Path(selected_profile_path_text).is_file()
            or sha256_file(Path(selected_profile_path_text))
            != scala_provenance["selectedProfileSha256"]
            or scala_provenance["selectedProfileId"] != profile
            or not isinstance(source_manifest_path_text, str)
            or not Path(source_manifest_path_text).is_absolute()
            or source_manifest_path_text != str(expected_source_manifest_path)
            or SHA256.fullmatch(str(scala_provenance["sourceInputManifestSha256"])) is None
            or Path(source_manifest_path_text).is_symlink()
            or not Path(source_manifest_path_text).is_file()
            or sha256_file(Path(source_manifest_path_text))
            != scala_provenance["sourceInputManifestSha256"]
            or not isinstance(capability_path_text, str)
            or not Path(capability_path_text).is_absolute()
            or SHA256.fullmatch(str(scala_provenance["effectiveJvmArgumentsCapabilitySha256"]))
            is None
            or Path(capability_path_text).is_symlink()
            or not Path(capability_path_text).is_file()
            or sha256_file(Path(capability_path_text))
            != scala_provenance["effectiveJvmArgumentsCapabilitySha256"]
            or expected_project_path.is_symlink()
            or not expected_project_path.is_file()
            or expected_source_path.is_symlink()
            or not expected_source_path.is_dir()
            or expected_benchmark_path.is_symlink()
            or not expected_benchmark_path.is_dir()
        ):
            raise GateError(f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}")
        toolchain_lock_path = _bound_regular_file(
            path_value=scala_provenance["toolchainLockPath"],
            sha256_value=scala_provenance["toolchainLockSha256"],
            expected_path=expected_toolchain_lock_path,
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        _bound_regular_file(
            path_value=scala_provenance["mergedToolchainProvenancePath"],
            sha256_value=scala_provenance["mergedToolchainProvenanceSha256"],
            expected_path=expected_merged_provenance_path,
            expected_sha256=FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256,
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        _validate_scala_toolchain_lock(
            strict_json_load(toolchain_lock_path),
            s1_4x_root=s1_4x_root,
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        selector = next(
            (
                entry
                for entry in plan.get("familySelectors", [])
                if isinstance(entry, dict) and entry.get("selectorId") == selector_id
            ),
            None,
        )
        jmh_include_regex = selector.get("jmhIncludeRegex") if isinstance(selector, dict) else None
        selector_case_ids = selector.get("expectedCaseIds") if isinstance(selector, dict) else None
        raw_path = str(block_directory / item["rawEvidencePath"])
        expected_arguments = [
            benchmark_executable_text,
            "--power",
            "run",
            str(expected_project_path),
            str(expected_profile_path),
            str(expected_source_path),
            str(expected_benchmark_path),
            "--server=false",
            "--jvm",
            "system",
            "--jmh",
            "--jmh-version",
            "1.37",
            "--",
            "-bm",
            "avgt",
            "-tu",
            "ns",
            "-t",
            "1",
            "-f",
            "3",
            "-wi",
            "5",
            "-i",
            "10",
            "-w",
            "1s",
            "-r",
            "1s",
            "-rf",
            "json",
            "-rff",
            raw_path,
            str(jmh_include_regex),
        ]
        try:
            compiled_selector = (
                re.compile(jmh_include_regex) if isinstance(jmh_include_regex, str) else None
            )
        except re.error as exc:
            raise GateError(f"NATIVE_EXECUTION_ARGV_INVALID:{case_id}") from exc
        if (
            compiled_selector is None
            or selector_case_ids != expected_case_ids
            or arguments != expected_arguments
        ):
            raise GateError(f"NATIVE_EXECUTION_ARGV_INVALID:{case_id}")
        return jmh_include_regex
    else:
        haskell_provenance = _exact_object(
            candidate_provenance,
            {
                "kind",
                "selectedProfilePath",
                "selectedProfileSha256",
                "selectedProfileId",
                "sourceInputManifestPath",
                "sourceInputManifestSha256",
                "effectiveCompilerFlagsSha256",
                "markerPythonPath",
                "markerPythonSha256",
                "markerScriptPath",
                "markerScriptSha256",
                "markerArgv",
                "markerArgvSha256",
                "ghcupPath",
                "ghcupSha256",
                "stackPath",
                "stackSha256",
                "stackYamlPath",
                "stackYamlSha256",
                "runtimeIdentityPath",
                "runtimeIdentitySha256",
                "executedBenchmarkPath",
                "executedBenchmarkSha256",
                "authoritativeGhcPath",
                "authoritativeGhcSha256",
                "selectedGhcOptions",
                "toolchainLockPath",
                "toolchainLockSha256",
                "mergedToolchainProvenancePath",
                "mergedToolchainProvenanceSha256",
            },
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        selected_profile_text = haskell_provenance["selectedProfilePath"]
        source_manifest_text = haskell_provenance["sourceInputManifestPath"]
        marker_python_text = haskell_provenance["markerPythonPath"]
        marker_script_text = haskell_provenance["markerScriptPath"]
        marker_argv = haskell_provenance["markerArgv"]
        ghcup_path_text = haskell_provenance["ghcupPath"]
        stack_path_text = haskell_provenance["stackPath"]
        stack_yaml_path_text = haskell_provenance["stackYamlPath"]
        runtime_identity_path_text = haskell_provenance["runtimeIdentityPath"]
        executed_benchmark_path_text = haskell_provenance["executedBenchmarkPath"]
        authoritative_ghc_path_text = haskell_provenance["authoritativeGhcPath"]
        selected_options = haskell_provenance["selectedGhcOptions"]
        s1_4x_root = resolved_plan.parent.parent
        expected_profile_path = s1_4x_root / "haskell/selected-profile.v1.json"
        expected_source_manifest_path = s1_4x_root / "haskell/source-inputs.v1.json"
        expected_marker_script_path = s1_4x_root / "benchmarks/run_rotated_blocks.py"
        expected_qualification_path = block_directory / "timeout-qualification.json"
        expected_stack_yaml_path = s1_4x_root / "haskell/stack.yaml"
        expected_runtime_identity_path = (
            block_directory / "benchmark-runtime-identity.json"
        )
        expected_toolchain_lock_path = s1_4x_root / "haskell/toolchain-lock.v1.json"
        expected_merged_provenance_path = s1_4x_root / "contract/toolchain-provenance.v1.json"
        if (
            haskell_provenance["kind"] != "haskell"
            or not isinstance(selected_profile_text, str)
            or not Path(selected_profile_text).is_absolute()
            or selected_profile_text != str(expected_profile_path)
            or not isinstance(source_manifest_text, str)
            or not Path(source_manifest_text).is_absolute()
            or source_manifest_text != str(expected_source_manifest_path)
            or not isinstance(marker_python_text, str)
            or not Path(marker_python_text).is_absolute()
            or not isinstance(marker_script_text, str)
            or marker_script_text != str(expected_marker_script_path)
            or not isinstance(marker_argv, list)
            or not isinstance(ghcup_path_text, str)
            or not isinstance(stack_path_text, str)
            or not isinstance(stack_yaml_path_text, str)
            or not isinstance(runtime_identity_path_text, str)
            or not isinstance(executed_benchmark_path_text, str)
            or not isinstance(authoritative_ghc_path_text, str)
            or not Path(ghcup_path_text).is_absolute()
            or not Path(stack_path_text).is_absolute()
            or not Path(stack_yaml_path_text).is_absolute()
            or not Path(runtime_identity_path_text).is_absolute()
            or not Path(executed_benchmark_path_text).is_absolute()
            or not Path(authoritative_ghc_path_text).is_absolute()
            or stack_yaml_path_text != str(expected_stack_yaml_path)
            or runtime_identity_path_text != str(expected_runtime_identity_path)
            or SHA256.fullmatch(str(haskell_provenance["selectedProfileSha256"])) is None
            or Path(selected_profile_text).is_symlink()
            or not Path(selected_profile_text).is_file()
            or sha256_file(Path(selected_profile_text))
            != haskell_provenance["selectedProfileSha256"]
            or SHA256.fullmatch(
                str(haskell_provenance["sourceInputManifestSha256"])
            )
            is None
            or Path(source_manifest_text).is_symlink()
            or not Path(source_manifest_text).is_file()
            or sha256_file(Path(source_manifest_text))
            != haskell_provenance["sourceInputManifestSha256"]
            or SHA256.fullmatch(str(haskell_provenance["markerPythonSha256"]))
            is None
            or SHA256.fullmatch(str(haskell_provenance["markerScriptSha256"]))
            is None
            or SHA256.fullmatch(str(haskell_provenance["markerArgvSha256"]))
            is None
            or marker_argv
            != [
                marker_python_text,
                marker_script_text,
                "mark-measurement-entered",
                "--qualification",
                str(expected_qualification_path),
            ]
            or haskell_provenance["markerArgvSha256"]
            != _canonical_sha256(marker_argv)
            or haskell_provenance["selectedProfileId"] != profile
            or haskell_provenance["effectiveCompilerFlagsSha256"]
            != effective_runtime_arguments_sha256
            or selected_options not in (["-O0", "-fasm"], ["-O2", "-fasm"])
            or _canonical_sha256(selected_options) != effective_runtime_arguments_sha256
            or SHA256.fullmatch(str(haskell_provenance["ghcupSha256"])) is None
            or SHA256.fullmatch(str(haskell_provenance["stackSha256"])) is None
            or SHA256.fullmatch(str(haskell_provenance["stackYamlSha256"])) is None
            or SHA256.fullmatch(str(haskell_provenance["runtimeIdentitySha256"]))
            is None
            or SHA256.fullmatch(str(haskell_provenance["executedBenchmarkSha256"]))
            is None
            or SHA256.fullmatch(str(haskell_provenance["authoritativeGhcSha256"]))
            is None
            or haskell_provenance["ghcupSha256"] != FROZEN_GHCUP_SHA256
            or haskell_provenance["stackSha256"] != FROZEN_STACK_SHA256
            or haskell_provenance["authoritativeGhcSha256"] != FROZEN_GHC_910_SHA256
            or executed_benchmark_path_text != benchmark_executable_text
            or haskell_provenance["executedBenchmarkSha256"]
            != provenance["benchmarkExecutableSha256"]
        ):
            raise GateError(f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}")
        ghcup_path = Path(ghcup_path_text)
        stack_path = Path(stack_path_text)
        stack_yaml_path = Path(stack_yaml_path_text)
        marker_python_path = Path(marker_python_text)
        marker_script_path = Path(marker_script_text)
        runtime_identity_path = Path(runtime_identity_path_text)
        executed_benchmark_path = Path(executed_benchmark_path_text)
        authoritative_ghc_path = Path(authoritative_ghc_path_text)
        if (
            any(
                path.is_symlink() or not path.is_file()
                for path in (
                    ghcup_path,
                    stack_path,
                    stack_yaml_path,
                    marker_python_path,
                    marker_script_path,
                )
            )
            or sha256_file(ghcup_path) != haskell_provenance["ghcupSha256"]
            or sha256_file(stack_path) != haskell_provenance["stackSha256"]
            or sha256_file(stack_yaml_path) != haskell_provenance["stackYamlSha256"]
            or sha256_file(marker_python_path)
            != haskell_provenance["markerPythonSha256"]
            or sha256_file(marker_script_path)
            != haskell_provenance["markerScriptSha256"]
        ):
            raise GateError(f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}")
        runtime_identity_snapshot, runtime_identity_value = _snapshot_json_file(
            runtime_identity_path,
            role=f"haskell-runtime-identity:{case_id}",
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        runtime_identity = _exact_object(
            runtime_identity_value,
            HASKELL_RUNTIME_IDENTITY_FIELDS,
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        executed_benchmark_snapshot = _snapshot_regular_file(
            executed_benchmark_path,
            role=f"haskell-executed-benchmark:{case_id}",
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
            executable=True,
        )
        authoritative_ghc_snapshot = _snapshot_regular_file(
            authoritative_ghc_path,
            role=f"haskell-authoritative-ghc:{case_id}",
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
            executable=True,
        )
        try:
            artifact_relative = executed_benchmark_path.relative_to(
                s1_4x_root / "haskell"
            )
        except ValueError as exc:
            raise GateError(
                f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}"
            ) from exc
        artifact_parts = artifact_relative.parts
        if (
            runtime_identity_snapshot.sha256
            != haskell_provenance["runtimeIdentitySha256"]
            or runtime_identity
            != {
                "schemaVersion": "s1.4x-haskell-benchmark-runtime-identity-v1",
                "boundaryId": "haskell",
                "selectorId": selector_id,
                "executedBenchmarkPath": executed_benchmark_path_text,
                "executedBenchmarkSha256": (
                    haskell_provenance["executedBenchmarkSha256"]
                ),
                "status": "PASS",
            }
            or executed_benchmark_snapshot.sha256
            != haskell_provenance["executedBenchmarkSha256"]
            or authoritative_ghc_snapshot.sha256
            != haskell_provenance["authoritativeGhcSha256"]
            or len(artifact_parts) != 7
            or artifact_parts[0:2] != (".stack-work", "dist")
            or artifact_parts[3:]
            != (
                "ghc-9.10.3",
                "build",
                "s1-4x-haskell-benchmark",
                "s1-4x-haskell-benchmark",
            )
        ):
            raise GateError(f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}")
        toolchain_lock_path = _bound_regular_file(
            path_value=haskell_provenance["toolchainLockPath"],
            sha256_value=haskell_provenance["toolchainLockSha256"],
            expected_path=expected_toolchain_lock_path,
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        _bound_regular_file(
            path_value=haskell_provenance["mergedToolchainProvenancePath"],
            sha256_value=haskell_provenance["mergedToolchainProvenanceSha256"],
            expected_path=expected_merged_provenance_path,
            expected_sha256=FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256,
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        _validate_haskell_toolchain_lock(
            strict_json_load(toolchain_lock_path),
            s1_4x_root=s1_4x_root,
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        selected_profile = _validate_haskell_selected_profile(
            strict_json_load(Path(selected_profile_text)),
            plan=plan,
            plan_path=resolved_plan,
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
            plan_sha256=effective_plan_sha256,
        )
        _validate_haskell_source_manifest(
            strict_json_load(Path(source_manifest_text)),
            haskell_root=s1_4x_root / "haskell",
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        source_tree_sha256 = _haskell_benchmark_source_tree_sha256(
            s1_4x_root / "haskell",
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        if (
            selected_profile["profileId"] != profile
            or selected_profile["optionsSha256"] != effective_runtime_arguments_sha256
            or selected_profile["ghcOptions"] != selected_options
            or selected_profile["sourceTreeSha256"] != source_tree_sha256
        ):
            raise GateError(f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}")
        selector = next(
            (
                entry
                for entry in plan.get("familySelectors", [])
                if isinstance(entry, dict) and entry.get("selectorId") == selector_id
            ),
            None,
        )
        criterion_prefix = selector.get("criterionPrefix") if isinstance(selector, dict) else None
        selector_case_ids = selector.get("expectedCaseIds") if isinstance(selector, dict) else None
        raw_path = str(block_directory / item["rawEvidencePath"])
        expected_arguments = [
            str(ghcup_path),
            "--offline",
            "run",
            "--quick",
            "--ghc",
            "9.10.3",
            "--stack",
            "3.11.1",
            "--",
            str(stack_path),
            "--stack-yaml",
            str(stack_yaml_path),
            "--no-terminal",
            "--color",
            "never",
            "--system-ghc",
            "--no-install-ghc",
            "bench",
            f"--ghc-options={' '.join(selected_options)}",
            (
                "--benchmark-arguments=--time-limit 5 "
                f"--json {raw_path} --match prefix {criterion_prefix} "
                "+RTS -N1 -RTS"
            ),
        ]
        if (
            not isinstance(criterion_prefix, str)
            or not criterion_prefix
            or selector_case_ids != expected_case_ids
            or arguments != expected_arguments
        ):
            raise GateError(f"NATIVE_EXECUTION_ARGV_INVALID:{case_id}")
    return None


def _parse_jmh_raw(
    value: Any,
    *,
    case_id: str,
    jmh_include_regex: str,
    native_case: Mapping[str, Any],
    native_statistics_case: Mapping[str, Any],
) -> None:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise GateError(f"JMH_RAW_DOCUMENT_INVALID:{case_id}")
    result = value[0]
    benchmark_name = result.get("benchmark")
    hidden_parameters = result.get("params", {})
    try:
        benchmark_matches = (
            isinstance(benchmark_name, str)
            and re.fullmatch(jmh_include_regex, benchmark_name) is not None
        )
    except re.error as exc:
        raise GateError(f"JMH_RAW_CASE_SELECTION_INVALID:{case_id}") from exc
    if not benchmark_matches or hidden_parameters != {}:
        raise GateError(f"JMH_RAW_CASE_SELECTION_INVALID:{case_id}")
    metric = result.get("primaryMetric")
    raw_data = metric.get("rawData") if isinstance(metric, dict) else None
    score = metric.get("score") if isinstance(metric, dict) else None
    score_confidence = metric.get("scoreConfidence") if isinstance(metric, dict) else None
    if (
        result.get("jmhVersion") != "1.37"
        or result.get("mode") != "avgt"
        or result.get("threads") != 1
        or result.get("forks") != 3
        or result.get("warmupIterations") != 5
        or result.get("warmupTime") != "1 s"
        or result.get("measurementIterations") != 10
        or result.get("measurementTime") != "1 s"
        or not isinstance(metric, dict)
        or metric.get("scoreUnit") != "ns/op"
        or not isinstance(score_confidence, list)
        or len(score_confidence) != 2
        or _number(score_confidence[0]) is None
        or _number(score_confidence[1]) is None
        or float(score_confidence[0]) > float(score_confidence[1])
        or not isinstance(score, (int, float))
        or isinstance(score, bool)
        or not math.isfinite(float(score))
        or float(score) <= 0.0
        or not isinstance(raw_data, list)
        or len(raw_data) != 3
        or any(
            not isinstance(fork, list)
            or len(fork) != 10
            or any(
                not isinstance(sample, (int, float))
                or isinstance(sample, bool)
                or not math.isfinite(float(sample))
                or float(sample) <= 0.0
                for sample in fork
            )
            for fork in raw_data
        )
        or native_case.get("samples") != 30
        or native_case.get("warmupIterations") != 5
        or native_case.get("measurementIterations") != 10
        or not math.isclose(
            float(score),
            float(native_case.get("nativeValue", math.nan)),
            rel_tol=1e-12,
            abs_tol=1e-9,
        )
    ):
        raise GateError(f"JMH_RAW_CONTRACT_INVALID:{case_id}")
    samples = [float(sample) for fork in raw_data for sample in fork]
    _validate_native_statistics_case(
        native_statistics_case,
        case_id=case_id,
        expected={
            "nativeSampleCount": len(samples),
            "nativeP95": _nearest_rank_p95(samples),
            "confidenceLevel": None,
            "confidenceLow": float(score_confidence[0]),
            "confidenceHigh": float(score_confidence[1]),
            "dispersionMetric": "p95-minus-median-ns-per-invocation",
            "dispersionValue": (_nearest_rank_p95(samples) - statistics.median(samples)),
            "nativeUnit": "ns",
        },
        error=f"JMH_NATIVE_STATISTICS_MISMATCH:{case_id}",
    )


def _parse_criterion_report(
    report_value: Any,
    *,
    report_number: int,
    case_id: str,
    logical_operations_per_invocation: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = _exact_object(
        report_value,
        {
            "reportNumber",
            "reportName",
            "reportKeys",
            "reportMeasured",
            "reportAnalysis",
            "reportOutliers",
            "reportKDEs",
        },
        error=f"CRITERION_RAW_CONTRACT_INVALID:{case_id}",
    )
    measurements = report["reportMeasured"]
    if (
        report["reportNumber"] != report_number
        or report["reportName"] != case_id
        or report["reportKeys"] != CRITERION_MEASUREMENT_KEYS
        or not isinstance(measurements, list)
        or len(measurements) < 2
        or type(logical_operations_per_invocation) is not int
        or logical_operations_per_invocation < 1
    ):
        raise GateError(f"CRITERION_RAW_CONTRACT_INVALID:{case_id}")
    samples: list[float] = []
    bootstrap_samples: list[float] = []
    iteration_counts: list[float] = []
    elapsed_times: list[float] = []
    for measurement in measurements:
        if not isinstance(measurement, list) or len(measurement) != 12:
            raise GateError(f"CRITERION_RAW_CONTRACT_INVALID:{case_id}")
        elapsed = _number(measurement[0], positive=True)
        cpu_time = _number(measurement[1])
        cycles = measurement[2]
        iterations = measurement[3]
        optional_integers = measurement[4:8]
        optional_seconds = measurement[8:12]
        if (
            elapsed is None
            or cpu_time is None
            or cpu_time < 0.0
            or type(cycles) is not int
            or cycles < 0
            or type(iterations) is not int
            or iterations < 1
            or any(
                item is not None and (type(item) is not int or item < 0)
                for item in optional_integers
            )
            or any(
                item is not None and (_number(item) is None or float(item) < 0.0)
                for item in optional_seconds
            )
        ):
            raise GateError(f"CRITERION_RAW_CONTRACT_INVALID:{case_id}")
        samples.append(elapsed / iterations)
        if elapsed >= CRITERION_BOOTSTRAP_THRESHOLD_SECONDS:
            bootstrap_samples.append(elapsed / iterations)
        iteration_counts.append(float(iterations))
        elapsed_times.append(elapsed)
    analysis = _exact_object(
        report["reportAnalysis"],
        {"anRegress", "anMean", "anStdDev", "anOutlierVar"},
        error=f"CRITERION_RAW_CONTRACT_INVALID:{case_id}",
    )
    regressions = analysis["anRegress"]
    if not isinstance(regressions, list) or not regressions:
        raise GateError(f"CRITERION_RAW_CONTRACT_INVALID:{case_id}")
    time_regressions = [
        regression
        for regression in regressions
        if isinstance(regression, dict) and regression.get("regResponder") == "time"
    ]
    if len(time_regressions) != 1:
        raise GateError(f"CRITERION_RAW_CONTRACT_INVALID:{case_id}")
    time_regression = _exact_object(
        time_regressions[0],
        {"regResponder", "regCoeffs", "regRSquare"},
        error=f"CRITERION_RAW_CONTRACT_INVALID:{case_id}",
    )
    coefficients = time_regression["regCoeffs"]
    if (
        time_regression["regResponder"] != "time"
        or not isinstance(coefficients, dict)
        or "iters" not in coefficients
    ):
        raise GateError(f"CRITERION_RAW_CONTRACT_INVALID:{case_id}")
    regression_time = _estimate(
        coefficients["iters"],
        error=f"CRITERION_RAW_CONTRACT_INVALID:{case_id}",
    )
    _estimate(
        time_regression["regRSquare"],
        error=f"CRITERION_RAW_CONTRACT_INVALID:{case_id}",
    )
    mean = _estimate(
        analysis["anMean"],
        error=f"CRITERION_RAW_CONTRACT_INVALID:{case_id}",
    )
    standard_deviation = _estimate(
        analysis["anStdDev"],
        error=f"CRITERION_RAW_CONTRACT_INVALID:{case_id}",
    )
    if (
        len(bootstrap_samples) < 2
        or len(set(iteration_counts)) < 2
        or not _same_number(
            regression_time["point"],
            _ols_slope(iteration_counts, elapsed_times),
        )
        or not _same_number(
            mean["point"],
            statistics.fmean(bootstrap_samples),
        )
        or not _same_number(
            standard_deviation["point"],
            statistics.stdev(bootstrap_samples),
        )
    ):
        raise GateError(f"CRITERION_RAW_CONTRACT_INVALID:{case_id}")
    outlier_variance = _exact_object(
        analysis["anOutlierVar"],
        {"ovEffect", "ovDesc", "ovFraction"},
        error=f"CRITERION_RAW_CONTRACT_INVALID:{case_id}",
    )
    outliers = _exact_object(
        report["reportOutliers"],
        {"samplesSeen", "lowSevere", "lowMild", "highMild", "highSevere"},
        error=f"CRITERION_RAW_CONTRACT_INVALID:{case_id}",
    )
    kdes = report["reportKDEs"]
    if (
        outlier_variance["ovEffect"] not in {"Unaffected", "Slight", "Moderate", "Severe"}
        or not isinstance(outlier_variance["ovDesc"], str)
        or _number(outlier_variance["ovFraction"]) is None
        or not 0.0 <= float(outlier_variance["ovFraction"]) <= 1.0
        or type(outliers["samplesSeen"]) is not int
        or outliers["samplesSeen"] != len(bootstrap_samples)
        or any(
            type(outliers[field]) is not int or outliers[field] < 0
            for field in ("lowSevere", "lowMild", "highMild", "highSevere")
        )
        or not isinstance(kdes, list)
        or not kdes
    ):
        raise GateError(f"CRITERION_RAW_CONTRACT_INVALID:{case_id}")
    for raw_kde in kdes:
        kde = _exact_object(
            raw_kde,
            {"kdeType", "kdeValues", "kdePDF"},
            error=f"CRITERION_RAW_CONTRACT_INVALID:{case_id}",
        )
        if (
            kde["kdeType"] != "time"
            or not isinstance(kde["kdeValues"], list)
            or not isinstance(kde["kdePDF"], list)
            or not kde["kdeValues"]
            or len(kde["kdeValues"]) != len(kde["kdePDF"])
            or any(_number(item) is None for item in kde["kdeValues"])
            or any(_number(item) is None or float(item) < 0.0 for item in kde["kdePDF"])
        ):
            raise GateError(f"CRITERION_RAW_CONTRACT_INVALID:{case_id}")
    scale = 1_000_000_000.0 / logical_operations_per_invocation
    native_p95 = _nearest_rank_p95(samples)
    dispersion = standard_deviation["point"]
    native_case = {
        "caseId": case_id,
        "nativeValue": regression_time["point"],
        "samples": len(measurements),
        "warmupIterations": 0,
        "measurementIterations": len(measurements),
    }
    native_statistics_case = {
        "caseId": case_id,
        "nativeSampleCount": len(samples),
        "nativeP95": native_p95,
        "confidenceLevel": regression_time["confidenceLevel"],
        "confidenceLow": regression_time["confidenceLow"],
        "confidenceHigh": regression_time["confidenceHigh"],
        "dispersionMetric": (
            "criterion-bootstrap-standard-deviation-seconds-per-invocation"
        ),
        "dispersionValue": dispersion,
        "nativeUnit": "s",
        "logicalOperationsPerInvocation": logical_operations_per_invocation,
        "normalizedP95NsPerLogicalOperation": native_p95 * scale,
        "normalizedConfidenceLowNsPerLogicalOperation": (
            regression_time["confidenceLow"] * scale
        ),
        "normalizedConfidenceHighNsPerLogicalOperation": (
            regression_time["confidenceHigh"] * scale
        ),
        "normalizedDispersionNsPerLogicalOperation": dispersion * scale,
    }
    return native_case, native_statistics_case


def _parse_criterion_family_raw(
    value: Any,
    *,
    expected_case_ids: list[str],
    logical_operations_by_case: Mapping[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or value[0] != "criterion"
        or value[1] != "1.6.4.0"
        or not isinstance(value[2], list)
        or not 2 <= len(expected_case_ids) <= 45
        or len(value[2]) != len(expected_case_ids)
        or set(logical_operations_by_case) != set(expected_case_ids)
    ):
        raise GateError("CRITERION_RAW_DOCUMENT_INVALID")
    actual_names = [
        report.get("reportName") if isinstance(report, dict) else None for report in value[2]
    ]
    if actual_names != expected_case_ids:
        raise GateError("CRITERION_RAW_CASE_ORDER_INVALID")
    native_cases: list[dict[str, Any]] = []
    native_statistics_cases: list[dict[str, Any]] = []
    for report_number, report in enumerate(value[2]):
        case_id = expected_case_ids[report_number]
        native_case, native_statistics_case = _parse_criterion_report(
            report,
            report_number=report_number,
            case_id=case_id,
            logical_operations_per_invocation=logical_operations_by_case[case_id],
        )
        native_cases.append(native_case)
        native_statistics_cases.append(native_statistics_case)
    return native_cases, native_statistics_cases


def validate_native_contract_evidence(
    value: Any,
    *,
    boundary_id: str,
    selector_id: str,
    block_directory: Path,
    native_cases: list[dict[str, Any]],
    native_statistics_cases: list[dict[str, Any]] | None = None,
    plan_path: Path | None = None,
    fixture_root_path: Path | None = None,
    input_ledger_path: Path | None = None,
    effective_runtime_arguments_sha256: str | None = None,
    profile: str | None = None,
    _raw_snapshots: dict[Path, tuple[InspectedExecutable, Any]] | None = None,
    _receipt_snapshots: dict[Path, tuple[InspectedExecutable, Any]] | None = None,
    _plan_document: Mapping[str, Any] | None = None,
    _plan_sha256: str | None = None,
    _artifact_sha256: str | None = None,
    _source_tree_sha256: str | None = None,
    _native_toolchain_lock_sha256: str | None = None,
) -> dict[str, Any]:
    """Candidate framework별 frozen timing 설정과 raw evidence bytes를 검증한다."""

    document = _exact_object(
        value,
        NATIVE_CONTRACT_FIELDS,
        error="NATIVE_CONTRACT_DOCUMENT_INVALID",
    )
    expected_configuration = {
        "scala": {
            "benchmarkMode": "AverageTime",
            "nativeTimeUnit": "ns",
            "threads": 1,
            "forks": 3,
            "warmupIterations": 5,
            "warmupSeconds": 1,
            "measurementIterations": 10,
            "measurementSeconds": 1,
        },
        "haskell": {
            "benchmarkMode": "Criterion",
            "nativeTimeUnit": "s",
            "threads": 1,
            "timeLimitSeconds": 5,
            "rtsArguments": ["+RTS", "-N1", "-RTS"],
        },
        "python-numpy-s1-4": {
            "benchmarkMode": "precomputed-batch",
            "nativeTimeUnit": "ns",
            "threads": 1,
            "warmupIterations": 5,
            "measurementIterations": 30,
            "compileAndSetupOutsideTiming": True,
            "measurementMarkerAfterForcedSetup": True,
        },
        "python-numpy-s1-4r": {
            "benchmarkMode": "precomputed-batch",
            "nativeTimeUnit": "ns",
            "threads": 1,
            "warmupIterations": 5,
            "measurementIterations": 30,
            "compileAndSetupOutsideTiming": True,
            "measurementMarkerAfterForcedSetup": True,
        },
        "python-jax-eager-s1-4r": {
            "benchmarkMode": "precomputed-batch",
            "nativeTimeUnit": "ns",
            "threads": 1,
            "warmupIterations": 5,
            "measurementIterations": 30,
            "compileAndSetupOutsideTiming": True,
            "measurementMarkerAfterForcedSetup": True,
        },
        "python-jax-jit-s1-4r": {
            "benchmarkMode": "precomputed-batch",
            "nativeTimeUnit": "ns",
            "threads": 1,
            "warmupIterations": 5,
            "measurementIterations": 30,
            "compileAndSetupOutsideTiming": True,
            "measurementMarkerAfterForcedSetup": True,
        },
    }
    expected_framework = {
        "scala": "JMH",
        "haskell": "Criterion",
        "python-numpy-s1-4": "NumPy",
        "python-numpy-s1-4r": "NumPy",
        "python-jax-eager-s1-4r": "JAX-eager",
        "python-jax-jit-s1-4r": "JAX-jit",
    }
    if (
        boundary_id not in expected_configuration
        or document["schemaVersion"] != "s1.4x-native-contract-validation-v1"
        or document["boundaryId"] != boundary_id
        or document["selectorId"] != selector_id
        or document["framework"] != expected_framework[boundary_id]
        or not isinstance(document["frameworkVersion"], str)
        or not document["frameworkVersion"]
        or document["configuration"] != expected_configuration[boundary_id]
        or document["status"] != "PASS"
        or not isinstance(document["cases"], list)
    ):
        raise GateError("NATIVE_CONTRACT_CONFIGURATION_INVALID")
    if boundary_id in {"scala", "haskell"} and (
        not isinstance(native_statistics_cases, list)
        or len(native_statistics_cases) != len(native_cases)
        or not native_cases
        or plan_path is None
        or fixture_root_path is None
        or input_ledger_path is None
        or SHA256.fullmatch(str(effective_runtime_arguments_sha256)) is None
        or not isinstance(profile, str)
        or not profile
    ):
        raise GateError("NATIVE_STATISTICS_CASES_INVALID")
    actual_case_ids: list[str] = []
    expected_case_ids = [str(case["caseId"]) for case in native_cases]
    statistics_cases: list[dict[str, Any] | None] = (
        list(native_statistics_cases)
        if native_statistics_cases is not None
        else [None] * len(native_cases)
    )
    if len(document["cases"]) != len(native_cases):
        raise GateError("NATIVE_CONTRACT_CASE_ORDER_INVALID")
    haskell_raw_identities: set[tuple[str, str]] = set()
    haskell_receipt_identities: set[tuple[str, str]] = set()
    haskell_raw_document: Any = None
    raw_snapshots = {} if _raw_snapshots is None else _raw_snapshots
    receipt_snapshots = (
        {} if _receipt_snapshots is None else _receipt_snapshots
    )
    for evidence, native_case, native_statistics_case in zip(
        document["cases"],
        native_cases,
        statistics_cases,
        strict=True,
    ):
        item = _exact_object(
            evidence,
            NATIVE_CONTRACT_CASE_FIELDS,
            error="NATIVE_CONTRACT_CASE_INVALID",
        )
        case_id = native_case.get("caseId")
        raw_samples = native_case.get("rawSamplesNs")
        sample_count = (
            len(raw_samples) if isinstance(raw_samples, list) else native_case.get("samples")
        )
        raw_path_text = item["rawEvidencePath"]
        if (
            item["caseId"] != case_id
            or type(item["nativeSampleCount"]) is not int
            or item["nativeSampleCount"] != sample_count
            or item["status"] != "PASS"
        ):
            raise GateError(f"NATIVE_CONTRACT_CASE_INVALID:{case_id}")
        if boundary_id.startswith("python-"):
            if (
                raw_path_text is not None
                or item["executionReceiptPath"] is not None
                or item["executionReceiptSha256"] is not None
                or not isinstance(raw_samples, list)
                or len(raw_samples) != 30
                or item["rawEvidenceSha256"] != _canonical_sha256(raw_samples)
            ):
                raise GateError(f"NATIVE_CONTRACT_RAW_EVIDENCE_INVALID:{case_id}")
            actual_case_ids.append(str(case_id))
            continue
        if (
            not isinstance(raw_path_text, str)
            or not raw_path_text
            or Path(raw_path_text).is_absolute()
            or ".." in Path(raw_path_text).parts
        ):
            raise GateError(f"NATIVE_CONTRACT_RAW_PATH_INVALID:{case_id}")
        raw_path = block_directory / raw_path_text
        try:
            raw_path.resolve(strict=True).relative_to(block_directory.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise GateError(f"NATIVE_CONTRACT_RAW_PATH_INVALID:{case_id}") from exc
        raw_snapshot_and_document = raw_snapshots.get(raw_path)
        if raw_snapshot_and_document is None:
            raw_snapshot_and_document = _snapshot_json_file(
                raw_path,
                role=f"native-raw:{case_id}",
                error=f"NATIVE_CONTRACT_RAW_EVIDENCE_INVALID:{case_id}",
            )
            raw_snapshots[raw_path] = raw_snapshot_and_document
        raw_snapshot, raw_document = raw_snapshot_and_document
        if (
            SHA256.fullmatch(str(item["rawEvidenceSha256"])) is None
            or raw_snapshot.sha256 != item["rawEvidenceSha256"]
        ):
            raise GateError(f"NATIVE_CONTRACT_RAW_EVIDENCE_INVALID:{case_id}")
        if (
            plan_path is None
            or fixture_root_path is None
            or input_ledger_path is None
            or effective_runtime_arguments_sha256 is None
            or profile is None
        ):
            raise GateError("NATIVE_EXECUTION_CONTEXT_INVALID")
        receipt_selector = _validate_execution_receipt(
            item=item,
            boundary_id=boundary_id,
            selector_id=selector_id,
            case_id=str(case_id),
            expected_case_ids=expected_case_ids,
            block_directory=block_directory,
            plan_path=plan_path,
            fixture_root_path=fixture_root_path,
            input_ledger_path=input_ledger_path,
            effective_runtime_arguments_sha256=str(effective_runtime_arguments_sha256),
            profile=profile,
            receipt_snapshots=receipt_snapshots,
            plan_document=_plan_document,
            plan_sha256=_plan_sha256,
            artifact_sha256=_artifact_sha256,
            source_tree_sha256=_source_tree_sha256,
            native_toolchain_lock_sha256=(
                _native_toolchain_lock_sha256
            ),
        )
        if boundary_id == "scala":
            if native_statistics_case is None or not isinstance(
                receipt_selector,
                str,
            ):
                raise GateError(f"NATIVE_STATISTICS_CASE_INVALID:{case_id}")
            _parse_jmh_raw(
                raw_document,
                case_id=str(case_id),
                jmh_include_regex=receipt_selector,
                native_case=native_case,
                native_statistics_case=native_statistics_case,
            )
        elif boundary_id == "haskell":
            if native_statistics_case is None:
                raise GateError(f"NATIVE_STATISTICS_CASE_INVALID:{case_id}")
            haskell_raw_identities.add((raw_path_text, str(item["rawEvidenceSha256"])))
            haskell_receipt_identities.add(
                (
                    str(item["executionReceiptPath"]),
                    str(item["executionReceiptSha256"]),
                )
            )
            haskell_raw_document = raw_document
        actual_case_ids.append(str(case_id))
    if actual_case_ids != expected_case_ids:
        raise GateError("NATIVE_CONTRACT_CASE_ORDER_INVALID")
    if boundary_id == "haskell":
        if (
            len(haskell_raw_identities) != 1
            or len(haskell_receipt_identities) != 1
            or haskell_raw_document is None
            or native_statistics_cases is None
        ):
            raise GateError("CRITERION_FAMILY_EVIDENCE_NOT_SHARED")
        if plan_path is None:
            raise GateError("NATIVE_EXECUTION_CONTEXT_INVALID")
        plan_document: Any = (
            dict(_plan_document)
            if _plan_document is not None
            else strict_json_load(plan_path)
        )
        frozen_cases = (
            plan_document.get("cases") if isinstance(plan_document, dict) else None
        )
        if not isinstance(frozen_cases, list):
            raise GateError("CRITERION_FROZEN_CASES_INVALID")
        frozen_case_by_id = {
            case.get("caseId"): case
            for case in frozen_cases
            if isinstance(case, dict) and isinstance(case.get("caseId"), str)
        }
        logical_operations_by_case: dict[str, int] = {}
        for case_id in expected_case_ids:
            frozen_case = frozen_case_by_id.get(case_id)
            logical_operations = (
                frozen_case.get("logicalOperationsPerInvocation")
                if isinstance(frozen_case, dict)
                else None
            )
            if type(logical_operations) is not int or logical_operations < 1:
                raise GateError("CRITERION_FROZEN_CASES_INVALID")
            logical_operations_by_case[case_id] = logical_operations
        parsed_native_cases, parsed_statistics_cases = _parse_criterion_family_raw(
            haskell_raw_document,
            expected_case_ids=expected_case_ids,
            logical_operations_by_case=logical_operations_by_case,
        )
        for native_case, expected_native_case in zip(
            native_cases,
            parsed_native_cases,
            strict=True,
        ):
            actual = _exact_object(
                native_case,
                NATIVE_CASE_FIELDS,
                error=(
                    "CRITERION_NATIVE_CASE_MISMATCH:"
                    f"{expected_native_case['caseId']}"
                ),
            )
            if (
                actual["caseId"] != expected_native_case["caseId"]
                or actual["samples"] != expected_native_case["samples"]
                or actual["warmupIterations"]
                != expected_native_case["warmupIterations"]
                or actual["measurementIterations"]
                != expected_native_case["measurementIterations"]
                or not _same_number(
                    actual["nativeValue"],
                    expected_native_case["nativeValue"],
                )
            ):
                raise GateError(
                    "CRITERION_NATIVE_CASE_MISMATCH:"
                    f"{expected_native_case['caseId']}"
                )
        for statistics_case, expected_statistics_case in zip(
            native_statistics_cases,
            parsed_statistics_cases,
            strict=True,
        ):
            _validate_native_statistics_case(
                statistics_case,
                case_id=str(expected_statistics_case["caseId"]),
                expected=expected_statistics_case,
                error=(
                    "CRITERION_NATIVE_STATISTICS_MISMATCH:"
                    f"{expected_statistics_case['caseId']}"
                ),
            )
    return document


def produce_scala_native_evidence(
    *,
    repo_root: Path,
    plan_path: Path,
    block_directory: Path,
    selector_id: str,
    scala_jmh_root: Path,
    input_ledger_path: Path,
    fixture_root_path: Path,
    selected_profile_result_path: Path,
    selected_profile_source_path: Path,
    source_input_manifest_path: Path,
    compiler_profiles_path: Path,
    toolchain_lock_path: Path,
    merged_toolchain_provenance_path: Path,
    jvm_argument_capability_path: Path,
    scala_cli_path: Path,
    java_executable_path: Path,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    """Scala case별 JMH raw를 검증하고 세 공통 native evidence만 배타 발행한다."""

    path_inputs = (
        repo_root,
        plan_path,
        block_directory,
        scala_jmh_root,
        input_ledger_path,
        fixture_root_path,
        selected_profile_result_path,
        selected_profile_source_path,
        source_input_manifest_path,
        compiler_profiles_path,
        toolchain_lock_path,
        merged_toolchain_provenance_path,
        jvm_argument_capability_path,
        scala_cli_path,
        java_executable_path,
    )
    if any(not path.is_absolute() for path in path_inputs):
        raise GateError("SCALA_NATIVE_PRODUCER_INPUT_INVALID")
    repo = Path(os.path.abspath(repo_root))
    plan_file = Path(os.path.abspath(plan_path))
    block = Path(os.path.abspath(block_directory))
    jmh_root = Path(os.path.abspath(scala_jmh_root))
    ledger_file = Path(os.path.abspath(input_ledger_path))
    fixture_root = Path(os.path.abspath(fixture_root_path))
    selected_result_file = Path(
        os.path.abspath(selected_profile_result_path)
    )
    selected_source_file = Path(
        os.path.abspath(selected_profile_source_path)
    )
    source_manifest_file = Path(
        os.path.abspath(source_input_manifest_path)
    )
    compiler_profiles_file = Path(
        os.path.abspath(compiler_profiles_path)
    )
    toolchain_lock_file = Path(os.path.abspath(toolchain_lock_path))
    merged_provenance_file = Path(
        os.path.abspath(merged_toolchain_provenance_path)
    )
    jvm_capability_file = Path(
        os.path.abspath(jvm_argument_capability_path)
    )
    scala_cli_file = Path(os.path.abspath(scala_cli_path))
    java_executable_file = Path(os.path.abspath(java_executable_path))
    started_timestamp = _parse_utc_timestamp(started_at)
    finished_timestamp = _parse_utc_timestamp(finished_at)
    s1_4x_root = plan_file.parent.parent
    expected_s1_4x_root = (
        repo
        / "workspaces/decision-platform/research/s1-4x-numeric-parity"
    )
    scala_root = s1_4x_root / "scala"
    if (
        repo.is_symlink()
        or not repo.is_dir()
        or repo.resolve(strict=True) != repo
        or block.is_symlink()
        or not block.is_dir()
        or block.resolve(strict=True) != block
        or jmh_root.is_symlink()
        or not jmh_root.is_dir()
        or jmh_root.resolve(strict=True) != jmh_root
        or fixture_root.is_symlink()
        or not fixture_root.is_dir()
        or fixture_root.resolve(strict=True) != fixture_root
        or s1_4x_root != expected_s1_4x_root
        or plan_file
        != expected_s1_4x_root / "benchmarks/benchmark-plan.v1.json"
        or jmh_root != block / "scala-jmh"
        or ledger_file != block / "input-ledger.json"
        or fixture_root != s1_4x_root / "contract/fixtures"
        or selected_source_file != scala_root / "selected-profile.scala"
        or source_manifest_file != scala_root / "source-inputs.v1.json"
        or compiler_profiles_file
        != scala_root / "compiler-profiles.v1.json"
        or toolchain_lock_file != scala_root / "toolchain-lock.v1.json"
        or merged_provenance_file
        != s1_4x_root / "contract/toolchain-provenance.v1.json"
        or started_timestamp is None
        or finished_timestamp is None
        or started_timestamp >= finished_timestamp
    ):
        raise GateError("SCALA_NATIVE_PRODUCER_INPUT_INVALID")
    output_paths = {
        "nativeContractValidationSha256": (
            block / "native-contract-validation.json"
        ),
        "nativeReportSha256": block / "native.json",
        "nativeStatisticsSha256": block / "native-statistics.json",
    }
    receipt_root = block / "receipts"
    if (
        receipt_root.exists()
        or receipt_root.is_symlink()
        or any(
            path.exists() or path.is_symlink()
            for path in output_paths.values()
        )
    ):
        raise GateError("SCALA_NATIVE_OUTPUT_ALREADY_EXISTS")
    plan_snapshot, plan_value = _snapshot_json_file(
        plan_file,
        role="scala-benchmark-plan",
        error="SCALA_NATIVE_PLAN_INVALID",
    )
    plan = _validate_plan_snapshot(
        plan_snapshot,
        error="SCALA_NATIVE_PLAN_INVALID",
    )
    if plan != plan_value:
        raise GateError("SCALA_NATIVE_PLAN_INVALID")
    selector = next(
        (
            item
            for item in plan["familySelectors"]
            if item.get("selectorId") == selector_id
        ),
        None,
    )
    if (
        not isinstance(selector, dict)
        or selector.get("boundaryId") != "scala"
        or selector.get("criterionMatchMode") != "none"
        or selector.get("criterionPrefix") is not None
        or not isinstance(selector.get("jmhIncludeRegex"), str)
        or not selector["jmhIncludeRegex"]
        or not isinstance(selector.get("expectedCaseIds"), list)
    ):
        raise GateError("SCALA_NATIVE_SELECTOR_INVALID")
    expected_case_ids = selector["expectedCaseIds"]
    if (
        not 2 <= len(expected_case_ids) <= 45
        or any(
            not isinstance(case_id, str) or not case_id
            for case_id in expected_case_ids
        )
        or len(set(expected_case_ids)) != len(expected_case_ids)
    ):
        raise GateError("SCALA_NATIVE_SELECTOR_INVALID")
    frozen_case_by_id = {
        case["caseId"]: case
        for case in plan["cases"]
        if isinstance(case, dict) and isinstance(case.get("caseId"), str)
    }
    logical_operations_by_case: dict[str, int] = {}
    for case_id in expected_case_ids:
        frozen_case = frozen_case_by_id.get(case_id)
        logical_operations = (
            frozen_case.get("logicalOperationsPerInvocation")
            if isinstance(frozen_case, dict)
            else None
        )
        if type(logical_operations) is not int or logical_operations < 1:
            raise GateError(f"SCALA_NATIVE_FROZEN_CASE_INVALID:{case_id}")
        logical_operations_by_case[case_id] = logical_operations
    ledger_snapshot, ledger_value = _snapshot_json_file(
        ledger_file,
        role="scala-input-ledger",
        error="SCALA_NATIVE_INPUT_LEDGER_INVALID",
    )
    with _sealed_snapshot_path(
        plan_snapshot,
        error="SCALA_NATIVE_INPUT_LEDGER_INVALID",
    ) as sealed_plan_path:
        validate_input_ledger(
            ledger_value,
            plan=plan,
            plan_path=sealed_plan_path,
            repo_root=repo,
            boundary_id="scala",
            selector_id=selector_id,
        )
    source_manifest_snapshot, source_manifest_value = _snapshot_json_file(
        source_manifest_file,
        role="scala-source-input-manifest",
        error="SCALA_NATIVE_SOURCE_MANIFEST_INVALID",
    )
    _, source_snapshots, source_tree_sha256 = (
        _validate_scala_source_manifest(
            source_manifest_value,
            scala_root=scala_root,
            error="SCALA_NATIVE_SOURCE_MANIFEST_INVALID",
        )
    )
    selected_source_snapshot = source_snapshots.get("selected-profile.scala")
    if (
        selected_source_snapshot is None
        or selected_source_snapshot.path != str(selected_source_file)
    ):
        raise GateError("SCALA_NATIVE_SELECTED_PROFILE_SOURCE_INVALID")
    compiler_profiles_snapshot, compiler_profiles_value = _snapshot_json_file(
        compiler_profiles_file,
        role="scala-compiler-profiles",
        error="SCALA_NATIVE_COMPILER_PROFILES_INVALID",
    )
    compiler_profiles = _validate_scala_compiler_profiles(
        compiler_profiles_value,
        error="SCALA_NATIVE_COMPILER_PROFILES_INVALID",
    )
    toolchain_lock_snapshot, toolchain_lock_value = _snapshot_json_file(
        toolchain_lock_file,
        role="scala-toolchain-lock",
        error="SCALA_NATIVE_TOOLCHAIN_LOCK_INVALID",
    )
    merged_provenance_snapshot, merged_provenance_value = _snapshot_json_file(
        merged_provenance_file,
        role="scala-toolchain-provenance",
        error="SCALA_NATIVE_TOOLCHAIN_PROVENANCE_INVALID",
    )
    scalafmt_config_snapshot = _snapshot_regular_file(
        scala_root / ".scalafmt.conf",
        role="scala-scalafmt-config",
        error="SCALA_NATIVE_TOOLCHAIN_LOCK_INVALID",
    )
    _validate_scala_toolchain_lock(
        toolchain_lock_value,
        s1_4x_root=s1_4x_root,
        error="SCALA_NATIVE_TOOLCHAIN_LOCK_INVALID",
        project_sha256=source_snapshots["project.scala"].sha256,
        scalafmt_config_sha256=scalafmt_config_snapshot.sha256,
        merged_provenance_value=merged_provenance_value,
    )
    if (
        merged_provenance_snapshot.sha256
        != FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256
    ):
        raise GateError("SCALA_NATIVE_TOOLCHAIN_PROVENANCE_INVALID")
    scala_cli_snapshot, java_executable_snapshot = (
        _validate_scala_executable_identities(
            scala_cli_path=scala_cli_file,
            java_executable_path=java_executable_file,
        )
    )
    jvm_capability_snapshot, jvm_capability_value = _snapshot_json_file(
        jvm_capability_file,
        role="scala-jvm-argument-capability",
        error="SCALA_NATIVE_JVM_CAPABILITY_INVALID",
    )
    capability_smoke_plan_snapshot = _snapshot_regular_file(
        s1_4x_root / "contract/capability-smoke-plan.v1.json",
        role="scala-capability-smoke-plan",
        error="SCALA_NATIVE_JVM_CAPABILITY_INVALID",
    )
    jvm_allowlist = _validate_scala_jvm_allowlist(
        jvm_capability_value,
        plan_sha256=plan_snapshot.sha256,
        toolchain_lock_sha256=toolchain_lock_snapshot.sha256,
        java_executable_sha256=java_executable_snapshot.sha256,
        error="SCALA_NATIVE_JVM_CAPABILITY_INVALID",
        capability_smoke_plan_sha256=(
            capability_smoke_plan_snapshot.sha256
        ),
    )
    selected_result_snapshot, selected_result_value = _snapshot_json_file(
        selected_result_file,
        role="scala-selected-profile-result",
        error="SCALA_NATIVE_SELECTED_PROFILE_RESULT_INVALID",
    )
    _, profile_id, scala_cli_arguments, profile_options_sha256 = (
        _validate_scala_selected_result(
            selected_result_value,
            plan=plan,
            plan_sha256=plan_snapshot.sha256,
            selected_source_sha256=selected_source_snapshot.sha256,
            source_manifest_sha256=source_manifest_snapshot.sha256,
            compiler_profiles_sha256=compiler_profiles_snapshot.sha256,
            compiler_profiles=compiler_profiles,
            toolchain_lock_sha256=toolchain_lock_snapshot.sha256,
            merged_provenance_sha256=merged_provenance_snapshot.sha256,
            jvm_allowlist_sha256=jvm_capability_snapshot.sha256,
            scala_cli_sha256=scala_cli_snapshot.sha256,
            java_executable_sha256=java_executable_snapshot.sha256,
            error="SCALA_NATIVE_SELECTED_PROFILE_RESULT_INVALID",
        )
    )
    case_evidence = [
        _validate_scala_case_evidence(
            case_directory=jmh_root / f"case-{index:03d}",
            block_directory=block,
            case_index=index,
            case_id=case_id,
            logical_operations=logical_operations_by_case[case_id],
            jmh_include_regex=str(selector["jmhIncludeRegex"]),
            plan_sha256=plan_snapshot.sha256,
            source_manifest_sha256=source_manifest_snapshot.sha256,
            compiler_profiles_sha256=compiler_profiles_snapshot.sha256,
            profile_id=profile_id,
            profile_options_sha256=profile_options_sha256,
            scala_cli_arguments=scala_cli_arguments,
            scala_cli_snapshot=scala_cli_snapshot,
            scala_root=scala_root,
            jvm_allowlist=jvm_allowlist,
            jvm_allowlist_sha256=jvm_capability_snapshot.sha256,
            java_executable_sha256=java_executable_snapshot.sha256,
        )
        for index, case_id in enumerate(expected_case_ids, start=1)
    ]
    runtime_arguments_sha256 = (
        _scala_effective_runtime_arguments_sha256(
            selector_id=selector_id,
            expected_case_ids=expected_case_ids,
            profile_id=profile_id,
            profile_options_sha256=profile_options_sha256,
            case_receipts=[
                item["runtimeReceipt"] for item in case_evidence
            ],
        )
    )
    artifact_sha256 = _scala_artifact_closure_sha256(
        {
            "sourceTreeSha256": source_tree_sha256,
            "selectedProfileResultSha256": (
                selected_result_snapshot.sha256
            ),
            "selectedProfileSourceSha256": (
                selected_source_snapshot.sha256
            ),
            "sourceInputManifestSha256": (
                source_manifest_snapshot.sha256
            ),
            "compilerProfilesSha256": compiler_profiles_snapshot.sha256,
            "scalaCliBinarySha256": scala_cli_snapshot.sha256,
            "javaExecutableSha256": java_executable_snapshot.sha256,
            "toolchainLockSha256": toolchain_lock_snapshot.sha256,
            "mergedToolchainProvenanceSha256": (
                merged_provenance_snapshot.sha256
            ),
            "effectiveJvmArgumentsCapabilitySha256": (
                jvm_capability_snapshot.sha256
            ),
        }
    )
    candidate_provenance = {
        "kind": "scala",
        "selectedProfileResultPath": str(selected_result_file),
        "selectedProfileResultSha256": selected_result_snapshot.sha256,
        "selectedProfileSourcePath": str(selected_source_file),
        "selectedProfileSourceSha256": selected_source_snapshot.sha256,
        "selectedProfileId": profile_id,
        "sourceInputManifestPath": str(source_manifest_file),
        "sourceInputManifestSha256": source_manifest_snapshot.sha256,
        "compilerProfilesPath": str(compiler_profiles_file),
        "compilerProfilesSha256": compiler_profiles_snapshot.sha256,
        "toolchainLockPath": str(toolchain_lock_file),
        "toolchainLockSha256": toolchain_lock_snapshot.sha256,
        "mergedToolchainProvenancePath": str(merged_provenance_file),
        "mergedToolchainProvenanceSha256": (
            merged_provenance_snapshot.sha256
        ),
        "effectiveJvmArgumentsCapabilityPath": str(jvm_capability_file),
        "effectiveJvmArgumentsCapabilitySha256": (
            jvm_capability_snapshot.sha256
        ),
        "scalaCliPath": str(scala_cli_file),
        "scalaCliBinarySha256": scala_cli_snapshot.sha256,
        "javaExecutablePath": str(java_executable_file),
        "javaExecutableSha256": java_executable_snapshot.sha256,
    }
    receipt_root.mkdir(mode=0o700)
    receipt_snapshots: dict[
        Path,
        tuple[InspectedExecutable, Any],
    ] = {}
    native_contract_cases: list[dict[str, Any]] = []
    raw_snapshots: dict[Path, tuple[InspectedExecutable, Any]] = {}
    for index, (case_id, item) in enumerate(
        zip(expected_case_ids, case_evidence, strict=True),
        start=1,
    ):
        raw_relative = f"scala-jmh/case-{index:03d}/native.json"
        receipt_relative = f"receipts/case-{index:03d}.json"
        raw_snapshot = item["rawSnapshot"]
        raw_document = item["rawDocument"]
        receipt_document = {
            "schemaVersion": (
                "s1.4x-native-case-execution-receipt-v1"
            ),
            "boundaryId": "scala",
            "selectorId": selector_id,
            "caseId": case_id,
            "commandArgv": item["runtimeArgv"],
            "environment": {"S1_4X_BENCHMARK_CASE_ID": case_id},
            "exitCode": 0,
            "rawEvidencePath": raw_relative,
            "rawEvidenceSha256": raw_snapshot.sha256,
            "provenance": {
                "planPath": str(plan_file),
                "planSha256": plan_snapshot.sha256,
                "fixtureRootPath": str(fixture_root),
                "fixtureFreezeIdentitySha256": _canonical_sha256(
                    plan["fixtureFreezeIdentity"]
                ),
                "inputLedgerPath": str(ledger_file),
                "inputLedgerSha256": ledger_snapshot.sha256,
                "selectorId": selector_id,
                "caseIds": expected_case_ids,
                "benchmarkExecutablePath": str(scala_cli_file),
                "benchmarkExecutableSha256": scala_cli_snapshot.sha256,
                "effectiveRuntimeArgumentsSha256": (
                    runtime_arguments_sha256
                ),
                "candidateProvenance": candidate_provenance,
            },
            "status": "PASS",
        }
        receipt_path = block / receipt_relative
        exclusive_json_write(receipt_path, receipt_document)
        receipt_snapshot_and_value = _snapshot_json_file(
            receipt_path,
            role=f"scala-native-execution-receipt:{case_id}",
            error=f"SCALA_NATIVE_EXECUTION_RECEIPT_INVALID:{case_id}",
        )
        receipt_snapshots[receipt_path] = receipt_snapshot_and_value
        raw_path = block / raw_relative
        raw_snapshots[raw_path] = (raw_snapshot, raw_document)
        native_contract_cases.append(
            {
                "caseId": case_id,
                "nativeSampleCount": item["nativeCase"]["samples"],
                "rawEvidencePath": raw_relative,
                "rawEvidenceSha256": raw_snapshot.sha256,
                "executionReceiptPath": receipt_relative,
                "executionReceiptSha256": (
                    receipt_snapshot_and_value[0].sha256
                ),
                "status": "PASS",
            }
        )
    native_cases = [item["nativeCase"] for item in case_evidence]
    statistics_cases = [
        item["statisticsCase"] for item in case_evidence
    ]
    native_contract = {
        "schemaVersion": "s1.4x-native-contract-validation-v1",
        "boundaryId": "scala",
        "selectorId": selector_id,
        "framework": "JMH",
        "frameworkVersion": "1.37",
        "configuration": {
            "benchmarkMode": "AverageTime",
            "nativeTimeUnit": "ns",
            "threads": 1,
            "forks": 3,
            "warmupIterations": 5,
            "warmupSeconds": 1,
            "measurementIterations": 10,
            "measurementSeconds": 1,
        },
        "cases": native_contract_cases,
        "status": "PASS",
    }
    validate_native_contract_evidence(
        native_contract,
        boundary_id="scala",
        selector_id=selector_id,
        block_directory=block,
        native_cases=native_cases,
        native_statistics_cases=statistics_cases,
        plan_path=plan_file,
        fixture_root_path=fixture_root,
        input_ledger_path=ledger_file,
        effective_runtime_arguments_sha256=runtime_arguments_sha256,
        profile=profile_id,
        _raw_snapshots=raw_snapshots,
        _receipt_snapshots=receipt_snapshots,
        _plan_document=plan,
        _plan_sha256=plan_snapshot.sha256,
        _artifact_sha256=artifact_sha256,
        _source_tree_sha256=source_tree_sha256,
        _native_toolchain_lock_sha256=toolchain_lock_snapshot.sha256,
    )
    native_contract_sha256 = _canonical_sha256(native_contract)
    native_document = {
        "schemaVersion": "s1.4x-candidate-native-benchmark-v1",
        "boundaryId": "scala",
        "selectorId": selector_id,
        "nativeBenchmarkMode": "AverageTime",
        "nativeTimeUnit": "ns",
        "profile": profile_id,
        "artifactSha256": artifact_sha256,
        "sourceTreeSha256": source_tree_sha256,
        "toolchainLockSha256": toolchain_lock_snapshot.sha256,
        "effectiveRuntimeArgumentsSha256": runtime_arguments_sha256,
        "inputLedgerSha256": ledger_snapshot.sha256,
        "nativeContractValidationSha256": native_contract_sha256,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "cases": native_cases,
        "status": "PASS",
    }
    native_sha256 = _canonical_sha256(native_document)
    statistics_document = {
        "schemaVersion": "s1.4x-native-statistics-v1",
        "boundaryId": "scala",
        "selectorId": selector_id,
        "nativeReportSha256": native_sha256,
        "cases": statistics_cases,
        "status": "PASS",
    }
    exclusive_json_write(
        output_paths["nativeContractValidationSha256"],
        native_contract,
    )
    exclusive_json_write(output_paths["nativeReportSha256"], native_document)
    exclusive_json_write(
        output_paths["nativeStatisticsSha256"],
        statistics_document,
    )
    output_sha256 = {
        field: sha256_file(path) for field, path in output_paths.items()
    }
    if (
        output_sha256["nativeContractValidationSha256"]
        != native_contract_sha256
        or output_sha256["nativeReportSha256"] != native_sha256
    ):
        raise GateError("SCALA_NATIVE_OUTPUT_DIGEST_INVALID")
    return {
        "boundaryId": "scala",
        "selectorId": selector_id,
        "caseCount": len(native_cases),
        **output_sha256,
        "status": "PASS",
    }


def produce_haskell_native_evidence(
    *,
    repo_root: Path,
    plan_path: Path,
    block_directory: Path,
    selector_id: str,
    criterion_raw_path: Path,
    execution_receipt_path: Path,
    input_ledger_path: Path,
    fixture_root_path: Path,
    selected_profile_path: Path,
    source_input_manifest_path: Path,
    toolchain_lock_path: Path,
    merged_toolchain_provenance_path: Path,
    benchmark_artifact_path: Path,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    """Criterion family raw를 공통 parser로만 계산해 세 native evidence를 배타 발행한다."""

    repo = repo_root.resolve(strict=True)
    plan_file = plan_path.resolve(strict=True)
    block = block_directory.resolve(strict=True)
    raw_file = Path(os.path.abspath(criterion_raw_path))
    receipt_file = Path(os.path.abspath(execution_receipt_path))
    ledger_file = input_ledger_path.resolve(strict=True)
    fixture_root = fixture_root_path.resolve(strict=True)
    profile_file = selected_profile_path.resolve(strict=True)
    source_manifest_file = source_input_manifest_path.resolve(strict=True)
    toolchain_lock_file = toolchain_lock_path.resolve(strict=True)
    merged_provenance_file = merged_toolchain_provenance_path.resolve(strict=True)
    benchmark_artifact = Path(os.path.abspath(benchmark_artifact_path))
    started_timestamp = _parse_utc_timestamp(started_at)
    finished_timestamp = _parse_utc_timestamp(finished_at)
    if (
        not repo.is_dir()
        or not block.is_dir()
        or raw_file != block / "raw/criterion-family.json"
        or receipt_file != block / "receipts/criterion-family.json"
        or ledger_file != block / "input-ledger.json"
        or any(
            path.is_symlink() or not path.is_file()
            for path in (
                plan_file,
                raw_file,
                receipt_file,
                ledger_file,
                profile_file,
                source_manifest_file,
                toolchain_lock_file,
                merged_provenance_file,
                benchmark_artifact,
            )
        )
        or started_timestamp is None
        or finished_timestamp is None
        or started_timestamp >= finished_timestamp
    ):
        raise GateError("HASKELL_NATIVE_PRODUCER_INPUT_INVALID")
    s1_4x_root = plan_file.parent.parent
    haskell_root = s1_4x_root / "haskell"
    if (
        profile_file != haskell_root / "selected-profile.v1.json"
        or source_manifest_file != haskell_root / "source-inputs.v1.json"
        or toolchain_lock_file != haskell_root / "toolchain-lock.v1.json"
        or merged_provenance_file
        != s1_4x_root / "contract/toolchain-provenance.v1.json"
    ):
        raise GateError("HASKELL_NATIVE_PRODUCER_PROVENANCE_PATH_INVALID")
    output_paths = {
        "nativeContractValidationSha256": (
            block / "native-contract-validation.json"
        ),
        "nativeReportSha256": block / "native.json",
        "nativeStatisticsSha256": block / "native-statistics.json",
    }
    if any(path.exists() or path.is_symlink() for path in output_paths.values()):
        raise GateError("HASKELL_NATIVE_OUTPUT_ALREADY_EXISTS")
    plan = validate_plan(plan_file)
    selector = next(
        (
            item
            for item in plan["familySelectors"]
            if item.get("selectorId") == selector_id
        ),
        None,
    )
    if (
        not isinstance(selector, dict)
        or selector.get("boundaryId") != "haskell"
        or selector.get("criterionMatchMode") != "prefix"
        or not isinstance(selector.get("criterionPrefix"), str)
        or not selector["criterionPrefix"]
        or not isinstance(selector.get("expectedCaseIds"), list)
    ):
        raise GateError("HASKELL_NATIVE_SELECTOR_INVALID")
    expected_case_ids = selector["expectedCaseIds"]
    if (
        not 2 <= len(expected_case_ids) <= 45
        or any(not isinstance(case_id, str) or not case_id for case_id in expected_case_ids)
        or len(set(expected_case_ids)) != len(expected_case_ids)
    ):
        raise GateError("HASKELL_NATIVE_SELECTOR_INVALID")
    frozen_case_by_id = {
        case["caseId"]: case
        for case in plan["cases"]
        if isinstance(case, dict) and isinstance(case.get("caseId"), str)
    }
    logical_operations_by_case: dict[str, int] = {}
    for case_id in expected_case_ids:
        frozen_case = frozen_case_by_id.get(case_id)
        logical_operations = (
            frozen_case.get("logicalOperationsPerInvocation")
            if isinstance(frozen_case, dict)
            else None
        )
        if type(logical_operations) is not int or logical_operations < 1:
            raise GateError(f"HASKELL_NATIVE_FROZEN_CASE_INVALID:{case_id}")
        logical_operations_by_case[case_id] = logical_operations
    validate_input_ledger(
        strict_json_load(ledger_file),
        plan=plan,
        plan_path=plan_file,
        repo_root=repo,
        boundary_id="haskell",
        selector_id=selector_id,
    )
    profile = _validate_haskell_selected_profile(
        strict_json_load(profile_file),
        plan=plan,
        plan_path=plan_file,
        error="HASKELL_NATIVE_SELECTED_PROFILE_INVALID",
    )
    _validate_haskell_source_manifest(
        strict_json_load(source_manifest_file),
        haskell_root=haskell_root,
        error="HASKELL_NATIVE_SOURCE_MANIFEST_INVALID",
    )
    source_tree_sha256 = _haskell_benchmark_source_tree_sha256(
        haskell_root,
        error="HASKELL_NATIVE_SOURCE_TREE_INVALID",
    )
    if profile["sourceTreeSha256"] != source_tree_sha256:
        raise GateError("HASKELL_NATIVE_SOURCE_TREE_INVALID")
    _validate_haskell_toolchain_lock(
        strict_json_load(toolchain_lock_file),
        s1_4x_root=s1_4x_root,
        error="HASKELL_NATIVE_TOOLCHAIN_LOCK_INVALID",
    )
    if (
        merged_provenance_file.is_symlink()
        or sha256_file(merged_provenance_file)
        != FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256
    ):
        raise GateError("HASKELL_NATIVE_TOOLCHAIN_PROVENANCE_INVALID")
    raw_snapshot, raw_document = _snapshot_json_file(
        raw_file,
        role="haskell-criterion-family-raw",
        error="HASKELL_NATIVE_RAW_EVIDENCE_INVALID",
    )
    receipt_snapshot, receipt_document = _snapshot_json_file(
        receipt_file,
        role="haskell-execution-receipt",
        error="HASKELL_NATIVE_EXECUTION_RECEIPT_INVALID",
    )
    raw_sha256 = raw_snapshot.sha256
    receipt_sha256 = receipt_snapshot.sha256
    receipt = _exact_object(
        receipt_document,
        EXECUTION_RECEIPT_FIELDS,
        error="HASKELL_NATIVE_EXECUTION_RECEIPT_INVALID",
    )
    receipt_provenance = _exact_object(
        receipt["provenance"],
        EXECUTION_PROVENANCE_FIELDS,
        error="HASKELL_NATIVE_EXECUTION_RECEIPT_INVALID",
    )
    benchmark_artifact_snapshot = _snapshot_regular_file(
        benchmark_artifact,
        role="haskell-benchmark-artifact",
        error="HASKELL_NATIVE_EXECUTION_RECEIPT_INVALID",
        executable=True,
    )
    benchmark_artifact_sha256 = benchmark_artifact_snapshot.sha256
    if (
        receipt["rawEvidencePath"] != "raw/criterion-family.json"
        or receipt["rawEvidenceSha256"] != raw_sha256
        or receipt_provenance["benchmarkExecutablePath"]
        != str(benchmark_artifact)
        or receipt_provenance["benchmarkExecutableSha256"]
        != benchmark_artifact_sha256
    ):
        raise GateError("HASKELL_NATIVE_EXECUTION_RECEIPT_INVALID")
    native_cases, statistics_cases = _parse_criterion_family_raw(
        raw_document,
        expected_case_ids=expected_case_ids,
        logical_operations_by_case=logical_operations_by_case,
    )
    native_contract_cases = [
        {
            "caseId": case["caseId"],
            "nativeSampleCount": case["samples"],
            "rawEvidencePath": "raw/criterion-family.json",
            "rawEvidenceSha256": raw_sha256,
            "executionReceiptPath": "receipts/criterion-family.json",
            "executionReceiptSha256": receipt_sha256,
            "status": "PASS",
        }
        for case in native_cases
    ]
    native_contract = {
        "schemaVersion": "s1.4x-native-contract-validation-v1",
        "boundaryId": "haskell",
        "selectorId": selector_id,
        "framework": "Criterion",
        "frameworkVersion": "1.6.4.0",
        "configuration": {
            "benchmarkMode": "Criterion",
            "nativeTimeUnit": "s",
            "threads": 1,
            "timeLimitSeconds": 5,
            "rtsArguments": ["+RTS", "-N1", "-RTS"],
        },
        "cases": native_contract_cases,
        "status": "PASS",
    }
    validate_native_contract_evidence(
        native_contract,
        boundary_id="haskell",
        selector_id=selector_id,
        block_directory=block,
        native_cases=native_cases,
        native_statistics_cases=statistics_cases,
        plan_path=plan_file,
        fixture_root_path=fixture_root,
        input_ledger_path=ledger_file,
        effective_runtime_arguments_sha256=str(profile["optionsSha256"]),
        profile=str(profile["profileId"]),
        _raw_snapshots={raw_file: (raw_snapshot, raw_document)},
        _receipt_snapshots={receipt_file: (receipt_snapshot, receipt_document)},
    )
    native_contract_sha256 = _canonical_sha256(native_contract)
    native_document = {
        "schemaVersion": "s1.4x-candidate-native-benchmark-v1",
        "boundaryId": "haskell",
        "selectorId": selector_id,
        "nativeBenchmarkMode": "Criterion",
        "nativeTimeUnit": "s",
        "profile": profile["profileId"],
        "artifactSha256": benchmark_artifact_sha256,
        "sourceTreeSha256": source_tree_sha256,
        "toolchainLockSha256": sha256_file(toolchain_lock_file),
        "effectiveRuntimeArgumentsSha256": profile["optionsSha256"],
        "inputLedgerSha256": sha256_file(ledger_file),
        "nativeContractValidationSha256": native_contract_sha256,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "cases": native_cases,
        "status": "PASS",
    }
    native_sha256 = _canonical_sha256(native_document)
    statistics_document = {
        "schemaVersion": "s1.4x-native-statistics-v1",
        "boundaryId": "haskell",
        "selectorId": selector_id,
        "nativeReportSha256": native_sha256,
        "cases": statistics_cases,
        "status": "PASS",
    }
    exclusive_json_write(
        output_paths["nativeContractValidationSha256"],
        native_contract,
    )
    exclusive_json_write(output_paths["nativeReportSha256"], native_document)
    exclusive_json_write(
        output_paths["nativeStatisticsSha256"],
        statistics_document,
    )
    output_sha256 = {
        field: sha256_file(path) for field, path in output_paths.items()
    }
    if (
        output_sha256["nativeContractValidationSha256"]
        != native_contract_sha256
        or output_sha256["nativeReportSha256"] != native_sha256
    ):
        raise GateError("HASKELL_NATIVE_OUTPUT_DIGEST_INVALID")
    return {
        "boundaryId": "haskell",
        "selectorId": selector_id,
        "caseCount": len(native_cases),
        **output_sha256,
        "status": "PASS",
    }


def build_block_result(
    *,
    plan: dict[str, Any],
    native: Any,
    qualification: Any,
    family_id: str,
    rotation_id: str,
    outer_repetition: int,
    run_id: str,
    benchmark_subject_commit: str,
    native_report_sha256: str,
    toolchain_provenance_sha256: str,
    actual_affinity_cpu_set: list[int],
) -> dict[str, Any]:
    """Candidate-specific raw 집계를 exact common report로 투영한다."""

    document = _exact_object(
        native,
        NATIVE_FIELDS,
        error="CANDIDATE_NATIVE_DOCUMENT_INVALID",
    )
    boundary_id = document["boundaryId"]
    if boundary_id not in {"scala", "haskell"}:
        raise GateError("CANDIDATE_NATIVE_BOUNDARY_INVALID")
    selector = next(
        (
            item
            for item in plan.get("familySelectors", [])
            if item.get("selectorId") == document["selectorId"]
        ),
        None,
    )
    expected_mode = plan["execution"]["nativeBenchmarkMode"][boundary_id]
    expected_unit = plan["execution"]["nativeTimeUnit"][boundary_id]
    if (
        document["schemaVersion"] != "s1.4x-candidate-native-benchmark-v1"
        or document["status"] != "PASS"
        or selector is None
        or selector["boundaryId"] != boundary_id
        or selector["familyId"] != family_id
        or document["nativeBenchmarkMode"] != expected_mode
        or document["nativeTimeUnit"] != expected_unit
        or not isinstance(document["profile"], str)
        or not document["profile"]
        or any(
            SHA256.fullmatch(str(document[field])) is None
            for field in (
                "artifactSha256",
                "sourceTreeSha256",
                "toolchainLockSha256",
                "effectiveRuntimeArgumentsSha256",
                "inputLedgerSha256",
                "nativeContractValidationSha256",
            )
        )
    ):
        raise GateError("CANDIDATE_NATIVE_IDENTITY_INVALID")
    if (
        COMMIT.fullmatch(benchmark_subject_commit) is None
        or SHA256.fullmatch(native_report_sha256) is None
        or SHA256.fullmatch(toolchain_provenance_sha256) is None
        or actual_affinity_cpu_set != plan["execution"]["cpuSet"]
    ):
        raise GateError("CANDIDATE_NATIVE_RUN_IDENTITY_INVALID")
    qualification_document = qualification if isinstance(qualification, dict) else {}
    qualification_subject = qualification_document.get("subject")
    qualification_run = qualification_document.get("run")
    host_validity = qualification_document.get("hostValidity")
    if (
        qualification_document.get("schemaVersion") != "s1.4x-timeout-qualification-v1"
        or qualification_document.get("phase") != "MEASUREMENT"
        or qualification_document.get("measurementEntered") is not True
        or not isinstance(qualification_subject, dict)
        or qualification_subject.get("benchmarkSubjectCommit") != benchmark_subject_commit
        or not isinstance(qualification_run, dict)
        or qualification_run.get("runId") != run_id
        or qualification_run.get("rotationId") != rotation_id
        or qualification_run.get("outerRepetition") != outer_repetition
        or rotation_id != f"R{outer_repetition}"
        or not isinstance(host_validity, dict)
    ):
        raise GateError("CANDIDATE_NATIVE_QUALIFICATION_INVALID")
    host_artifact_sha = _sha256_value(
        host_validity.get("sha256"),
        error="CANDIDATE_NATIVE_HOST_IDENTITY_INVALID",
    )
    portable_host_id = _sha256_value(
        host_validity.get("portableHostIdSha256"),
        error="CANDIDATE_NATIVE_HOST_IDENTITY_INVALID",
    )
    raw_cases = document["cases"]
    if not isinstance(raw_cases, list):
        raise GateError("CANDIDATE_NATIVE_CASES_INVALID")
    expected_case_ids = selector["expectedCaseIds"]
    actual_case_ids: list[str] = []
    frozen_case_by_id = {case["caseId"]: case for case in plan["cases"]}
    measured_cases = []
    for raw in raw_cases:
        measured = _exact_object(
            raw,
            NATIVE_CASE_FIELDS,
            error="CANDIDATE_NATIVE_CASE_INVALID",
        )
        case_id = measured["caseId"]
        frozen = frozen_case_by_id.get(case_id)
        native_value = measured["nativeValue"]
        samples = measured["samples"]
        warmups = measured["warmupIterations"]
        iterations = measured["measurementIterations"]
        if (
            frozen is None
            or not isinstance(native_value, (int, float))
            or isinstance(native_value, bool)
            or not math.isfinite(float(native_value))
            or float(native_value) <= 0.0
            or type(samples) is not int
            or samples < 2
            or type(warmups) is not int
            or warmups < 0
            or type(iterations) is not int
            or iterations < 2
        ):
            raise GateError(f"CANDIDATE_NATIVE_CASE_INVALID:{case_id}")
        actual_case_ids.append(case_id)
        logical = frozen["logicalOperationsPerInvocation"]
        measured_cases.append(
            {
                "caseId": case_id,
                "functionId": frozen["functionId"],
                "fixtureId": frozen["fixtureId"],
                "nativeValue": float(native_value),
                "nativeUnit": expected_unit,
                "logicalOperationsPerInvocation": logical,
                "normalizedNsPerLogicalOperation": (
                    float(native_value) * UNIT_TO_NS[expected_unit] / logical
                ),
                "samples": samples,
                "warmupIterations": warmups,
                "measurementIterations": iterations,
                "status": "PASS",
            }
        )
    if actual_case_ids != expected_case_ids:
        raise GateError("CANDIDATE_NATIVE_CASE_ORDER_INVALID")
    expected_rotation = plan["execution"]["candidateOrderBlocks"][outer_repetition - 1]
    scheduling_group = "Scala" if boundary_id == "scala" else "Haskell"
    return {
        "schemaVersion": "s1.4x-benchmark-block-result-v1",
        "planId": plan["planId"],
        "runId": run_id,
        "benchmarkSubjectCommit": benchmark_subject_commit,
        "subject": {
            "candidate": boundary_id,
            "language": boundary_id,
            "profile": document["profile"],
            "artifactSha256": document["artifactSha256"],
            "sourceTreeSha256": document["sourceTreeSha256"],
            "toolchainLockSha256": document["toolchainLockSha256"],
        },
        "rotation": {
            "rotationId": rotation_id,
            "outerRepetition": outer_repetition,
            "candidateOrder": expected_rotation["schedulingGroups"],
            "schedulingGroup": scheduling_group,
            "pythonBoundaryOrder": expected_rotation["pythonBoundaries"],
        },
        "block": {
            "boundaryId": boundary_id,
            "familyId": family_id,
            "selectorId": document["selectorId"],
            "affinityCpuSet": plan["execution"]["cpuSet"],
            "actualAffinityCpuSet": actual_affinity_cpu_set,
            "threadCount": 1,
            "nativeBenchmarkMode": expected_mode,
            "startedAt": document["startedAt"],
            "finishedAt": document["finishedAt"],
            "status": "PASS",
            "nativeReportPath": (f"{run_id}/{rotation_id}/{boundary_id}/{family_id}/native.json"),
            "nativeReportSha256": native_report_sha256,
        },
        "environment": {
            "hostFingerprintSha256": portable_host_id,
            "hostValidityArtifactSha256": host_artifact_sha,
            "toolchainProvenanceSha256": toolchain_provenance_sha256,
            "fixtureFreezeIdentity": plan["fixtureFreezeIdentity"],
            "effectiveRuntimeArgumentsSha256": document["effectiveRuntimeArgumentsSha256"],
        },
        "cases": measured_cases,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--block-dir", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--boundary", choices=("scala", "haskell"), required=True)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--rotation", required=True)
    parser.add_argument("--outer-repetition", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--benchmark-subject-commit", required=True)
    return parser


def _scala_producer_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scala JMH case raw를 shared native evidence로 투영한다."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--block-dir", type=Path, required=True)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--scala-jmh-root", type=Path, required=True)
    parser.add_argument("--input-ledger", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument(
        "--selected-profile-result",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--selected-profile-source",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--source-input-manifest",
        type=Path,
        required=True,
    )
    parser.add_argument("--compiler-profiles", type=Path, required=True)
    parser.add_argument("--toolchain-lock", type=Path, required=True)
    parser.add_argument("--toolchain-provenance", type=Path, required=True)
    parser.add_argument(
        "--jvm-argument-capability",
        type=Path,
        required=True,
    )
    parser.add_argument("--scala-cli", type=Path, required=True)
    parser.add_argument("--java-executable", type=Path, required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--finished-at", required=True)
    return parser


def _scala_producer_main(argv: list[str]) -> int:
    arguments = _scala_producer_parser().parse_args(argv)
    try:
        result = produce_scala_native_evidence(
            repo_root=arguments.repo_root,
            plan_path=arguments.plan,
            block_directory=arguments.block_dir,
            selector_id=arguments.selector,
            scala_jmh_root=arguments.scala_jmh_root,
            input_ledger_path=arguments.input_ledger,
            fixture_root_path=arguments.fixture_root,
            selected_profile_result_path=(
                arguments.selected_profile_result
            ),
            selected_profile_source_path=(
                arguments.selected_profile_source
            ),
            source_input_manifest_path=arguments.source_input_manifest,
            compiler_profiles_path=arguments.compiler_profiles,
            toolchain_lock_path=arguments.toolchain_lock,
            merged_toolchain_provenance_path=(
                arguments.toolchain_provenance
            ),
            jvm_argument_capability_path=(
                arguments.jvm_argument_capability
            ),
            scala_cli_path=arguments.scala_cli,
            java_executable_path=arguments.java_executable,
            started_at=arguments.started_at,
            finished_at=arguments.finished_at,
        )
    except (
        ContractError,
        GateError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"SCALA_NATIVE_PRODUCER_FAIL:{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0


def _haskell_producer_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Criterion raw family를 shared native evidence로 투영한다."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--block-dir", type=Path, required=True)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--criterion-raw", type=Path, required=True)
    parser.add_argument("--execution-receipt", type=Path, required=True)
    parser.add_argument("--input-ledger", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--selected-profile", type=Path, required=True)
    parser.add_argument("--source-input-manifest", type=Path, required=True)
    parser.add_argument("--toolchain-lock", type=Path, required=True)
    parser.add_argument("--toolchain-provenance", type=Path, required=True)
    parser.add_argument("--benchmark-artifact", type=Path, required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--finished-at", required=True)
    return parser


def _haskell_producer_main(argv: list[str]) -> int:
    arguments = _haskell_producer_parser().parse_args(argv)
    try:
        result = produce_haskell_native_evidence(
            repo_root=arguments.repo_root,
            plan_path=arguments.plan,
            block_directory=arguments.block_dir,
            selector_id=arguments.selector,
            criterion_raw_path=arguments.criterion_raw,
            execution_receipt_path=arguments.execution_receipt,
            input_ledger_path=arguments.input_ledger,
            fixture_root_path=arguments.fixture_root,
            selected_profile_path=arguments.selected_profile,
            source_input_manifest_path=arguments.source_input_manifest,
            toolchain_lock_path=arguments.toolchain_lock,
            merged_toolchain_provenance_path=arguments.toolchain_provenance,
            benchmark_artifact_path=arguments.benchmark_artifact,
            started_at=arguments.started_at,
            finished_at=arguments.finished_at,
        )
    except (ContractError, GateError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"HASKELL_NATIVE_PRODUCER_FAIL:{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    command_arguments = list(sys.argv[1:] if argv is None else argv)
    if command_arguments and command_arguments[0] == "produce-scala-native":
        return _scala_producer_main(command_arguments[1:])
    if (
        command_arguments
        and command_arguments[0] == "produce-haskell-native"
    ):
        return _haskell_producer_main(command_arguments[1:])
    arguments = _parser().parse_args(command_arguments)
    try:
        repo = arguments.repo_root.resolve(strict=True)
        if not arguments.plan.is_absolute():
            raise GateError("CANDIDATE_NATIVE_PLAN_PATH_INVALID")
        plan_path = Path(os.path.abspath(arguments.plan))
        plan_snapshot, plan_value = _snapshot_json_file(
            plan_path,
            role="candidate-native-benchmark-plan",
            error="CANDIDATE_NATIVE_PLAN_INVALID",
        )
        plan = _validate_plan_snapshot(
            plan_snapshot,
            error="CANDIDATE_NATIVE_PLAN_INVALID",
        )
        if plan != plan_value:
            raise GateError("CANDIDATE_NATIVE_PLAN_INVALID")
        block_dir = arguments.block_dir.resolve(strict=True)
        qualification_path = arguments.qualification.resolve(strict=True)
        native_path = block_dir / "native.json"
        statistics_path = block_dir / "native-statistics.json"
        input_ledger_path = block_dir / "input-ledger.json"
        native_contract_path = block_dir / "native-contract-validation.json"
        if (
            not statistics_path.is_file()
            or statistics_path.is_symlink()
            or not input_ledger_path.is_file()
            or input_ledger_path.is_symlink()
            or not native_contract_path.is_file()
            or native_contract_path.is_symlink()
        ):
            raise GateError("CANDIDATE_NATIVE_STATISTICS_MISSING")
        native = strict_json_load(native_path)
        if (
            not isinstance(native, Mapping)
            or native.get("boundaryId") != arguments.boundary
            or native.get("selectorId") != arguments.selector
            or native.get("inputLedgerSha256") != sha256_file(input_ledger_path)
            or native.get("nativeContractValidationSha256") != sha256_file(native_contract_path)
        ):
            raise GateError("CANDIDATE_NATIVE_ARGV_MISMATCH")
        with _sealed_snapshot_path(
            plan_snapshot,
            error="CANDIDATE_NATIVE_INPUT_LEDGER_INVALID",
        ) as sealed_plan_path:
            validate_input_ledger(
                strict_json_load(input_ledger_path),
                plan=plan,
                plan_path=sealed_plan_path,
                repo_root=repo,
                boundary_id=arguments.boundary,
                selector_id=arguments.selector,
            )
        native_cases = native.get("cases")
        if not isinstance(native_cases, list):
            raise GateError("CANDIDATE_NATIVE_CASES_INVALID")
        statistics_document = _exact_object(
            strict_json_load(statistics_path),
            {
                "schemaVersion",
                "boundaryId",
                "selectorId",
                "nativeReportSha256",
                "cases",
                "status",
            },
            error="CANDIDATE_NATIVE_STATISTICS_INVALID",
        )
        statistics_cases = statistics_document["cases"]
        if (
            statistics_document["schemaVersion"] != "s1.4x-native-statistics-v1"
            or statistics_document["boundaryId"] != arguments.boundary
            or statistics_document["selectorId"] != arguments.selector
            or statistics_document["nativeReportSha256"] != sha256_file(native_path)
            or statistics_document["status"] != "PASS"
            or not isinstance(statistics_cases, list)
        ):
            raise GateError("CANDIDATE_NATIVE_STATISTICS_INVALID")
        validate_native_contract_evidence(
            strict_json_load(native_contract_path),
            boundary_id=arguments.boundary,
            selector_id=arguments.selector,
            block_directory=block_dir,
            native_cases=native_cases,
            native_statistics_cases=statistics_cases,
            plan_path=plan_path,
            fixture_root_path=(
                repo / "workspaces/decision-platform/research/"
                "s1-4x-numeric-parity/contract/fixtures"
            ),
            input_ledger_path=input_ledger_path,
            effective_runtime_arguments_sha256=str(native["effectiveRuntimeArgumentsSha256"]),
            profile=str(native["profile"]),
            _plan_document=plan,
            _plan_sha256=plan_snapshot.sha256,
            _artifact_sha256=str(native["artifactSha256"]),
            _source_tree_sha256=str(native["sourceTreeSha256"]),
            _native_toolchain_lock_sha256=str(
                native["toolchainLockSha256"]
            ),
        )
        report = build_block_result(
            plan=plan,
            native=native,
            qualification=strict_json_load(qualification_path),
            family_id=arguments.family,
            rotation_id=arguments.rotation,
            outer_repetition=arguments.outer_repetition,
            run_id=arguments.run_id,
            benchmark_subject_commit=arguments.benchmark_subject_commit,
            native_report_sha256=sha256_file(native_path),
            toolchain_provenance_sha256=sha256_file(
                repo / "workspaces/decision-platform/research/s1-4x-numeric-parity/"
                "contract/toolchain-provenance.v1.json"
            ),
            actual_affinity_cpu_set=sorted(os.sched_getaffinity(0)),
        )
        result_path = block_dir / "block-result.json"
        exclusive_json_write(result_path, report)
        with _sealed_snapshot_path(
            plan_snapshot,
            error="CANDIDATE_NATIVE_PLAN_INVALID",
        ) as sealed_plan_path:
            validate_block_result(
                result_path,
                plan_path=sealed_plan_path,
                native_report_path=native_path,
                expected_boundary_id=arguments.boundary,
                expected_selector_id=arguments.selector,
            )
    except (ContractError, GateError, OSError, KeyError, ValueError) as exc:
        print(f"NATIVE_BENCHMARK_BLOCK_FAIL:{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "boundaryId": arguments.boundary,
                "selectorId": arguments.selector,
                "blockResultSha256": sha256_file(result_path),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
