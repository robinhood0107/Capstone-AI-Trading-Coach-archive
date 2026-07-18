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
from collections.abc import Mapping
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
    merged_provenance = strict_json_load(merged_provenance_path)
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
        or scala["projectSha256"] != sha256_file(project_path)
        or scalafmt["version"] != "3.11.4"
        or scalafmt["configPath"] != f"{expected_prefix}/scala/.scalafmt.conf"
        or scalafmt["configSha256"] != sha256_file(scalafmt_path)
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
        or profile["qualificationPlanSha256"] != sha256_file(plan_path)
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
    plan = strict_json_load(resolved_plan)
    if not isinstance(plan, dict):
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
        or provenance["planSha256"] != sha256_file(resolved_plan)
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
        plan_document = strict_json_load(plan_path)
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
    if (
        command_arguments
        and command_arguments[0] == "produce-haskell-native"
    ):
        return _haskell_producer_main(command_arguments[1:])
    arguments = _parser().parse_args(command_arguments)
    try:
        repo = arguments.repo_root.resolve(strict=True)
        plan_path = arguments.plan.resolve(strict=True)
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
        plan = validate_plan(plan_path)
        native = strict_json_load(native_path)
        if (
            not isinstance(native, Mapping)
            or native.get("boundaryId") != arguments.boundary
            or native.get("selectorId") != arguments.selector
            or native.get("inputLedgerSha256") != sha256_file(input_ledger_path)
            or native.get("nativeContractValidationSha256") != sha256_file(native_contract_path)
        ):
            raise GateError("CANDIDATE_NATIVE_ARGV_MISMATCH")
        validate_input_ledger(
            strict_json_load(input_ledger_path),
            plan=plan,
            plan_path=plan_path,
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
        validate_block_result(
            result_path,
            plan_path=plan_path,
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
