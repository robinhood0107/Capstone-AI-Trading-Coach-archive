#!/usr/bin/env python3
"""Typed PASS evidence만으로 S1.4X 최종 후보 audit ledger를 생성·검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from gate import exclusive_json_write, strict_json_load

CANDIDATES = ("scala", "haskell")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_SCHEMA = "s1.4x-final-candidate-audit-evidence-v1"
LEDGER_SCHEMA = "s1.4x-final-candidate-audit-v1"
FROZEN_SCALA_CLI_SHA256 = (
    "54b93b8401e333095526da5e4853780d5bf37494baa1ba5486e9e643084253d0"
)
FROZEN_SCALAFIX_SHA256 = (
    "9db6db7359e580de8f4b72cd7c104d70023cf32a278db0c30aefb79c939eb0f3"
)
FROZEN_GHCUP_SHA256 = (
    "9ed5da5449b48043a0d17e767c05d2ef585e25a639bb934329496c6d2fad9cf8"
)
FROZEN_STACK_SHA256 = (
    "923dbd137756652c67b376e2447c655b87fcc373f4d104b5073bca913471ecbe"
)
FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256 = (
    "cd9e29a22473fba6203daa4f3a0cbaa57b8b6e5c5fc22de05ca0801c404ffa98"
)
FROZEN_CONTRACT_MANIFEST_SHA256 = (
    "4dd4d688339805a1432f9da973dbda73621dd7ff5f1b423819292f8a29a72b2e"
)
FROZEN_REFERENCE_LOCK_SHA256 = (
    "b55b81b933ad4749c77e3baa313f900dab18d808bc34285232f7b603993a6cb7"
)
FROZEN_FIXTURE_SHA256 = {
    "canonicalInputs": (
        "367ca5b8589f9c5b16e8c2e5dd2cfe7cc560a7de19559551c487beeefcf62e63"
    ),
    "propertySeeds": (
        "4502fb577ea2e2283612059f755e5e77d01d1f2a46ac4ec50dae61c8789a78fb"
    ),
    "canonicalResults": (
        "59619337c415757a612bc923b32e2a174018b15dc67b2357b10ccb602fca1b6a"
    ),
}

PURITY_RUBRICS = {
    "coreShellBoundary": "purity-core-shell",
    "sideEffectAudit": "purity-side-effect",
    "validationTransparency": "purity-validation-transparency",
    "dependencySurface": "purity-dependency-surface",
}
MAINTAINABILITY_RUBRICS = {
    "moduleCohesion": "maintainability-module-cohesion",
    "duplicationInventory": "maintainability-duplication-inventory",
    "commentsAndDocs": "maintainability-comments-docs",
    "testReadability": "maintainability-test-readability",
    "warningFree": "maintainability-warning-free",
}
INTEGRATION_RUBRICS = {
    "processContract": "integration-process-contract",
    "productionIsolation": "integration-production-isolation",
    "ciCostAndRollback": "integration-ci-cost-rollback",
}

EXPECTED_EVIDENCE_CLAIMS: dict[str, dict[str, Any]] = {
    "correctness-contract": {
        "functionCount": 20,
        "errorTrackCounts": {"s1.4": 19, "s1.4r": 13},
    },
    "property-coverage": {
        "propertyCount": 25,
        "seedCount": 24,
        "minimumSuccessfulPerSeed": 42,
        "successfulTestsPerProperty": 1008,
    },
    "cross-language-parity": {
        "oracleVsScalaMismatchCount": 0,
        "oracleVsHaskellMismatchCount": 0,
        "scalaVsHaskellMismatchCount": 0,
    },
    "regressions": {
        "productionRegressionStatus": "PASS",
        "researchRegressionStatus": "PASS",
    },
    "oci-correctness": {
        "networkMode": "none",
        "resultStatus": "PASS",
    },
    "toolchain-reproducibility": {
        "toolchainLockStatus": "PASS",
        "selectedProfileStatus": "PASS",
    },
    "fixture-reproducibility": {
        "fixtureLockStatus": "PASS",
        "deterministicReplayStatus": "PASS",
    },
    "offline-runtime-reproducibility": {
        "runtimeNetworkMode": "none",
        "containerStatus": "PASS",
    },
    **{
        evidence_id: {"rubricStatus": "PASS"}
        for evidence_id in (
            *PURITY_RUBRICS.values(),
            *MAINTAINABILITY_RUBRICS.values(),
            *INTEGRATION_RUBRICS.values(),
        )
    },
}

CORRECTNESS_EVIDENCE = {
    "correctness-contract",
    "property-coverage",
    "cross-language-parity",
    "regressions",
    "oci-correctness",
}
REPRODUCIBILITY_EVIDENCE = {
    "toolchain-reproducibility",
    "fixture-reproducibility",
    "offline-runtime-reproducibility",
}
RUBRIC_EVIDENCE = {
    *PURITY_RUBRICS.values(),
    *MAINTAINABILITY_RUBRICS.values(),
    *INTEGRATION_RUBRICS.values(),
}

# 각 evidence는 이 역할과 schema의 실제 source closure를 정확히 모두 가져야 한다.
EXPECTED_SOURCE_CONTRACTS: dict[str, tuple[tuple[str, str], ...]] = {
    "correctness-contract": (
        ("integration-coverage", "s1.4x-integration-coverage-v1"),
    ),
    "property-coverage": (
        ("integration-coverage", "s1.4x-integration-coverage-v1"),
    ),
    "cross-language-parity": (
        ("canonical-comparison", "s1.4x-comparison-report-v1"),
        ("semantic-comparison", "s1.4x-comparison-report-v1"),
    ),
    "regressions": (
        ("production-regression", "s1.4x-regression-gate-v1"),
        ("research-regression", "s1.4x-regression-gate-v1"),
    ),
    "oci-correctness": (
        ("oci-correctness", "s1.4x-oci-correctness-receipt-v1"),
    ),
    "toolchain-reproducibility": (
        (
            "toolchain-reproducibility",
            "s1.4x-toolchain-reproducibility-v1",
        ),
    ),
    "fixture-reproducibility": (
        (
            "fixture-reproducibility",
            "s1.4x-fixture-reproducibility-v1",
        ),
    ),
    "offline-runtime-reproducibility": (
        (
            "offline-runtime-reproducibility",
            "s1.4x-offline-runtime-reproducibility-v1",
        ),
    ),
    **{
        evidence_id: (
            ("rubric-assessment", "s1.4x-candidate-rubric-assessment-v1"),
        )
        for evidence_id in RUBRIC_EVIDENCE
    },
}


class FinalAuditError(ValueError):
    """최종 audit evidence 또는 ledger가 fail-closed 계약을 위반했다."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _exact_object(
    value: Any,
    fields: set[str],
    *,
    error: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise FinalAuditError(error)
    return value


def _validate_subject(repository_root: Path, commit: str) -> Path:
    if COMMIT.fullmatch(commit) is None:
        raise FinalAuditError("FINAL_AUDIT_SUBJECT_INVALID")
    repo = repository_root.resolve(strict=True)
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=repo,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0 or completed.stdout.strip() != commit or exists.returncode != 0:
        raise FinalAuditError("FINAL_AUDIT_SUBJECT_INVALID")
    return repo


def _has_symlink_component(root: Path, relative: Path) -> bool:
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _portable_directory(
    root: Path,
    path_value: Any,
    *,
    error: str,
) -> Path:
    if (
        not isinstance(path_value, str)
        or not path_value
        or Path(path_value).is_absolute()
        or ".." in Path(path_value).parts
    ):
        raise FinalAuditError(error)
    relative = Path(path_value)
    path = root / relative
    if _has_symlink_component(root, relative):
        raise FinalAuditError(error)
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise FinalAuditError(error) from exc
    if not path.is_dir():
        raise FinalAuditError(error)
    return path


def _portable_regular_file(
    root: Path,
    path_value: Any,
    *,
    expected_sha256: Any,
    error: str,
) -> Path:
    if (
        not isinstance(path_value, str)
        or not path_value
        or Path(path_value).is_absolute()
        or ".." in Path(path_value).parts
        or not isinstance(expected_sha256, str)
        or SHA256.fullmatch(expected_sha256) is None
    ):
        raise FinalAuditError(error)
    relative = Path(path_value)
    path = root / relative
    if _has_symlink_component(root, relative):
        raise FinalAuditError(error)
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise FinalAuditError(error) from exc
    if path.is_symlink() or not path.is_file() or _sha256(path) != expected_sha256:
        raise FinalAuditError(error)
    return path


def _sha256_value(value: Any) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _exact_int(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


def _coverage_candidate(
    source: Mapping[str, Any],
    *,
    candidate: str,
    error: str,
) -> dict[str, Any]:
    document = _exact_object(
        source,
        {
            "schemaVersion",
            "candidateCount",
            "candidates",
            "propertyCountPerCandidate",
            "functionCountPerCandidate",
            "errorTrackCountsPerCandidate",
            "errorVerificationModeCountsPerCandidate",
            "status",
        },
        error=error,
    )
    expected_modes = {
        "processDynamic": 29,
        "referenceObjectModel": 1,
        "registryStatic": 2,
    }
    if (
        document["schemaVersion"] != "s1.4x-integration-coverage-v1"
        or not _exact_int(document["candidateCount"], 2)
        or not _exact_int(document["propertyCountPerCandidate"], 25)
        or not _exact_int(document["functionCountPerCandidate"], 20)
        or document["errorTrackCountsPerCandidate"] != {"s1.4": 19, "s1.4r": 13}
        or document["errorVerificationModeCountsPerCandidate"] != expected_modes
        or document["status"] != "PASS"
        or not isinstance(document["candidates"], list)
        or len(document["candidates"]) != 2
    ):
        raise FinalAuditError(error)
    indexed: dict[str, dict[str, Any]] = {}
    for raw_candidate in document["candidates"]:
        entry = _exact_object(
            raw_candidate,
            {
                "implementation",
                "reportedImplementation",
                "propertyPlanSha256",
                "propertyCount",
                "functionCount",
                "errorCount",
                "errorTrackCounts",
                "errorVerificationModeCounts",
                "processDynamicErrorCount",
                "staticErrorCount",
                "propertyExecution",
                "status",
            },
            error=error,
        )
        implementation = entry["implementation"]
        if not isinstance(implementation, str) or implementation in indexed:
            raise FinalAuditError(error)
        execution = _exact_object(
            entry["propertyExecution"],
            {
                "framework",
                "toolchainProfile",
                "seedCorpusSha256",
                "seedCount",
                "minimumSuccessfulPerSeed",
                "runnerSha256",
                "sourceClosureSha256",
                "startedAt",
                "finishedAt",
            },
            error=error,
        )
        if (
            not isinstance(entry["reportedImplementation"], str)
            or not entry["reportedImplementation"]
            or not _sha256_value(entry["propertyPlanSha256"])
            or not _exact_int(entry["propertyCount"], 25)
            or not _exact_int(entry["functionCount"], 20)
            or not _exact_int(entry["errorCount"], 32)
            or entry["errorTrackCounts"] != {"s1.4": 19, "s1.4r": 13}
            or entry["errorVerificationModeCounts"] != expected_modes
            or not _exact_int(entry["processDynamicErrorCount"], 29)
            or not _exact_int(entry["staticErrorCount"], 3)
            or entry["status"] != "PASS"
            or not isinstance(execution["framework"], str)
            or not execution["framework"]
            or not isinstance(execution["toolchainProfile"], str)
            or not execution["toolchainProfile"]
            or not _sha256_value(execution["seedCorpusSha256"])
            or not _exact_int(execution["seedCount"], 24)
            or not _exact_int(execution["minimumSuccessfulPerSeed"], 42)
            or not _sha256_value(execution["runnerSha256"])
            or not _sha256_value(execution["sourceClosureSha256"])
            or not isinstance(execution["startedAt"], str)
            or not execution["startedAt"].endswith("Z")
            or not isinstance(execution["finishedAt"], str)
            or not execution["finishedAt"].endswith("Z")
        ):
            raise FinalAuditError(error)
        indexed[implementation] = entry
    if tuple(indexed) != CANDIDATES or candidate not in indexed:
        raise FinalAuditError(error)
    return indexed[candidate]


def _validate_comparison_source(source: Mapping[str, Any], *, error: str) -> None:
    document = _exact_object(
        source,
        {
            "schemaVersion",
            "requestId",
            "implementationCount",
            "mismatchCount",
            "mismatches",
            "status",
        },
        error=error,
    )
    if (
        document["schemaVersion"] != "s1.4x-comparison-report-v1"
        or not isinstance(document["requestId"], str)
        or not document["requestId"]
        or not _exact_int(document["implementationCount"], 2)
        or not _exact_int(document["mismatchCount"], 0)
        or document["mismatches"] != []
        or document["status"] != "PASS"
    ):
        raise FinalAuditError(error)


def _validate_regression_source(
    source: Mapping[str, Any],
    *,
    candidate: str,
    benchmark_subject_commit: str,
    role: str,
    error: str,
) -> None:
    document = _exact_object(
        source,
        {
            "schemaVersion",
            "candidate",
            "benchmarkSubjectCommit",
            "project",
            "testCount",
            "exitCode",
            "reportSha256",
            "status",
        },
        error=error,
    )
    expected = {
        "production-regression": (
            "workspaces/decision-platform/python-services",
            1344,
        ),
        "research-regression": (
            "workspaces/decision-platform/research/s1-4r-jax-risk",
            263,
        ),
    }
    project, test_count = expected[role]
    if (
        document["schemaVersion"] != "s1.4x-regression-gate-v1"
        or document["candidate"] != candidate
        or document["benchmarkSubjectCommit"] != benchmark_subject_commit
        or document["project"] != project
        or not _exact_int(document["testCount"], test_count)
        or not _exact_int(document["exitCode"], 0)
        or not _sha256_value(document["reportSha256"])
        or document["status"] != "PASS"
    ):
        raise FinalAuditError(error)


def _validate_oci_source(
    source: Mapping[str, Any],
    *,
    candidate: str,
    benchmark_subject_commit: str,
    error: str,
) -> None:
    document = _exact_object(
        source,
        {
            "schemaVersion",
            "candidate",
            "benchmarkSubjectCommit",
            "networkMode",
            "containerExitCode",
            "comparisonMismatchCount",
            "resultSha256",
            "comparisonSha256",
            "status",
        },
        error=error,
    )
    if (
        document["schemaVersion"] != "s1.4x-oci-correctness-receipt-v1"
        or document["candidate"] != candidate
        or document["benchmarkSubjectCommit"] != benchmark_subject_commit
        or document["networkMode"] != "none"
        or not _exact_int(document["containerExitCode"], 0)
        or not _exact_int(document["comparisonMismatchCount"], 0)
        or not _sha256_value(document["resultSha256"])
        or not _sha256_value(document["comparisonSha256"])
        or document["status"] != "PASS"
    ):
        raise FinalAuditError(error)


def _validate_toolchain_source(
    source: Mapping[str, Any],
    *,
    candidate: str,
    benchmark_subject_commit: str,
    error: str,
) -> None:
    document = _exact_object(
        source,
        {
            "schemaVersion",
            "candidate",
            "benchmarkSubjectCommit",
            "toolchainLockSha256",
            "selectedProfileId",
            "selectedProfileSha256",
            "mergedToolchainProvenanceSha256",
            "binarySha256",
            "status",
        },
        error=error,
    )
    expected_binaries = (
        {
            "scalaCli": FROZEN_SCALA_CLI_SHA256,
            "scalafix": FROZEN_SCALAFIX_SHA256,
        }
        if candidate == "scala"
        else {
            "ghcup": FROZEN_GHCUP_SHA256,
            "stack": FROZEN_STACK_SHA256,
        }
    )
    if (
        document["schemaVersion"] != "s1.4x-toolchain-reproducibility-v1"
        or document["candidate"] != candidate
        or document["benchmarkSubjectCommit"] != benchmark_subject_commit
        or not _sha256_value(document["toolchainLockSha256"])
        or not isinstance(document["selectedProfileId"], str)
        or not document["selectedProfileId"]
        or not _sha256_value(document["selectedProfileSha256"])
        or document["mergedToolchainProvenanceSha256"]
        != FROZEN_MERGED_TOOLCHAIN_PROVENANCE_SHA256
        or document["binarySha256"] != expected_binaries
        or document["status"] != "PASS"
    ):
        raise FinalAuditError(error)


def _validate_fixture_source(
    source: Mapping[str, Any],
    *,
    candidate: str,
    benchmark_subject_commit: str,
    error: str,
) -> None:
    document = _exact_object(
        source,
        {
            "schemaVersion",
            "candidate",
            "benchmarkSubjectCommit",
            "contractManifestSha256",
            "referenceLockSha256",
            "fixtureSha256",
            "deterministicReplayCount",
            "mismatchCount",
            "status",
        },
        error=error,
    )
    if (
        document["schemaVersion"] != "s1.4x-fixture-reproducibility-v1"
        or document["candidate"] != candidate
        or document["benchmarkSubjectCommit"] != benchmark_subject_commit
        or document["contractManifestSha256"] != FROZEN_CONTRACT_MANIFEST_SHA256
        or document["referenceLockSha256"] != FROZEN_REFERENCE_LOCK_SHA256
        or document["fixtureSha256"] != FROZEN_FIXTURE_SHA256
        or type(document["deterministicReplayCount"]) is not int
        or document["deterministicReplayCount"] < 2
        or not _exact_int(document["mismatchCount"], 0)
        or document["status"] != "PASS"
    ):
        raise FinalAuditError(error)


def _validate_offline_source(
    source: Mapping[str, Any],
    *,
    candidate: str,
    benchmark_subject_commit: str,
    error: str,
) -> None:
    document = _exact_object(
        source,
        {
            "schemaVersion",
            "candidate",
            "benchmarkSubjectCommit",
            "networkMode",
            "dependencyResolveMode",
            "containerExitCode",
            "resultSha256",
            "toolchainLockSha256",
            "status",
        },
        error=error,
    )
    if (
        document["schemaVersion"] != "s1.4x-offline-runtime-reproducibility-v1"
        or document["candidate"] != candidate
        or document["benchmarkSubjectCommit"] != benchmark_subject_commit
        or document["networkMode"] != "none"
        or document["dependencyResolveMode"] != "offline"
        or not _exact_int(document["containerExitCode"], 0)
        or not _sha256_value(document["resultSha256"])
        or not _sha256_value(document["toolchainLockSha256"])
        or document["status"] != "PASS"
    ):
        raise FinalAuditError(error)


def _validate_rubric_source(
    source: Mapping[str, Any],
    *,
    candidate: str,
    evidence_id: str,
    benchmark_subject_commit: str,
    audit_root: Path,
    source_path: Path,
    error: str,
) -> None:
    document = _exact_object(
        source,
        {
            "schemaVersion",
            "candidate",
            "benchmarkSubjectCommit",
            "rubricId",
            "reviewedArtifacts",
            "findings",
            "status",
        },
        error=error,
    )
    if (
        document["schemaVersion"] != "s1.4x-candidate-rubric-assessment-v1"
        or document["candidate"] != candidate
        or document["benchmarkSubjectCommit"] != benchmark_subject_commit
        or document["rubricId"] != evidence_id
        or document["findings"] != []
        or document["status"] != "PASS"
        or not isinstance(document["reviewedArtifacts"], list)
        or not document["reviewedArtifacts"]
    ):
        raise FinalAuditError(error)
    reviewed_paths: set[str] = set()
    for raw_artifact in document["reviewedArtifacts"]:
        artifact = _exact_object(
            raw_artifact,
            {"path", "sha256"},
            error=error,
        )
        reviewed = _portable_regular_file(
            audit_root,
            artifact["path"],
            expected_sha256=artifact["sha256"],
            error=error,
        )
        if (
            str(artifact["path"]) in reviewed_paths
            or reviewed.resolve(strict=True) == source_path.resolve(strict=True)
        ):
            raise FinalAuditError(error)
        reviewed_paths.add(str(artifact["path"]))


def _validate_source_document(
    source: Mapping[str, Any],
    *,
    candidate: str,
    evidence_id: str,
    role: str,
    benchmark_subject_commit: str,
    audit_root: Path,
    source_path: Path,
    error: str,
) -> None:
    if evidence_id in {"correctness-contract", "property-coverage"}:
        _coverage_candidate(source, candidate=candidate, error=error)
    elif evidence_id == "cross-language-parity":
        _validate_comparison_source(source, error=error)
    elif evidence_id == "regressions":
        _validate_regression_source(
            source,
            candidate=candidate,
            benchmark_subject_commit=benchmark_subject_commit,
            role=role,
            error=error,
        )
    elif evidence_id == "oci-correctness":
        _validate_oci_source(
            source,
            candidate=candidate,
            benchmark_subject_commit=benchmark_subject_commit,
            error=error,
        )
    elif evidence_id == "toolchain-reproducibility":
        _validate_toolchain_source(
            source,
            candidate=candidate,
            benchmark_subject_commit=benchmark_subject_commit,
            error=error,
        )
    elif evidence_id == "fixture-reproducibility":
        _validate_fixture_source(
            source,
            candidate=candidate,
            benchmark_subject_commit=benchmark_subject_commit,
            error=error,
        )
    elif evidence_id == "offline-runtime-reproducibility":
        _validate_offline_source(
            source,
            candidate=candidate,
            benchmark_subject_commit=benchmark_subject_commit,
            error=error,
        )
    elif evidence_id in RUBRIC_EVIDENCE:
        _validate_rubric_source(
            source,
            candidate=candidate,
            evidence_id=evidence_id,
            benchmark_subject_commit=benchmark_subject_commit,
            audit_root=audit_root,
            source_path=source_path,
            error=error,
        )
    else:
        raise FinalAuditError(error)


def _validate_source_artifacts(
    value: Any,
    *,
    audit_root: Path,
    candidate: str,
    evidence_id: str,
    benchmark_subject_commit: str,
    envelope_path: Path,
    error: str,
) -> list[dict[str, Any]]:
    contracts = EXPECTED_SOURCE_CONTRACTS[evidence_id]
    if not isinstance(value, list) or len(value) != len(contracts):
        raise FinalAuditError(error)
    validated: list[dict[str, Any]] = []
    paths: set[str] = set()
    for raw_artifact, (expected_role, expected_schema) in zip(
        value,
        contracts,
        strict=True,
    ):
        artifact = _exact_object(
            raw_artifact,
            {"role", "path", "sha256", "schemaVersion", "status"},
            error=error,
        )
        if (
            artifact["role"] != expected_role
            or artifact["status"] != "PASS"
            or artifact["schemaVersion"] != expected_schema
            or artifact["path"] in paths
        ):
            raise FinalAuditError(error)
        source_path = _portable_regular_file(
            audit_root,
            artifact["path"],
            expected_sha256=artifact["sha256"],
            error=error,
        )
        if source_path.resolve(strict=True) == envelope_path.resolve(strict=True):
            raise FinalAuditError(error)
        source = strict_json_load(source_path)
        if (
            not isinstance(source, Mapping)
            or source.get("schemaVersion") != expected_schema
        ):
            raise FinalAuditError(error)
        _validate_source_document(
            source,
            candidate=candidate,
            evidence_id=evidence_id,
            role=expected_role,
            benchmark_subject_commit=benchmark_subject_commit,
            audit_root=audit_root,
            source_path=source_path,
            error=error,
        )
        paths.add(str(artifact["path"]))
        validated.append(artifact)
    return validated


def _validate_evidence_envelope(
    path: Path,
    *,
    audit_root: Path,
    candidate: str,
    evidence_id: str,
    benchmark_subject_commit: str,
) -> dict[str, Any]:
    error = f"FINAL_AUDIT_EVIDENCE_INVALID:{candidate}:{evidence_id}"
    envelope = _exact_object(
        strict_json_load(path),
        {
            "schemaVersion",
            "candidate",
            "benchmarkSubjectCommit",
            "evidenceId",
            "claims",
            "sourceArtifacts",
            "status",
        },
        error=error,
    )
    if (
        envelope["schemaVersion"] != EVIDENCE_SCHEMA
        or envelope["candidate"] != candidate
        or envelope["benchmarkSubjectCommit"] != benchmark_subject_commit
        or envelope["evidenceId"] != evidence_id
        or envelope["claims"] != EXPECTED_EVIDENCE_CLAIMS[evidence_id]
        or envelope["status"] != "PASS"
    ):
        raise FinalAuditError(error)
    _validate_source_artifacts(
        envelope["sourceArtifacts"],
        audit_root=audit_root,
        candidate=candidate,
        evidence_id=evidence_id,
        benchmark_subject_commit=benchmark_subject_commit,
        envelope_path=path,
        error=error,
    )
    return envelope


def _rubric_status() -> dict[str, dict[str, str]]:
    return {
        "purityAuditability": {rubric: "PASS" for rubric in PURITY_RUBRICS},
        "maintainability": {rubric: "PASS" for rubric in MAINTAINABILITY_RUBRICS},
        "integrationFit": {rubric: "PASS" for rubric in INTEGRATION_RUBRICS},
    }


def generate_final_candidate_audit(
    *,
    repository_root: Path,
    benchmark_subject_commit: str,
    evidence_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    """고정 evidence ID 전체를 검증하고 수기 점수 없는 ledger를 새로 쓴다."""

    _validate_subject(repository_root, benchmark_subject_commit)
    if output_path.exists() or output_path.is_symlink():
        raise FinalAuditError("FINAL_AUDIT_OUTPUT_ALREADY_EXISTS")
    output = output_path.resolve()
    audit_root = output.parent.resolve(strict=True)
    try:
        lexical_evidence = (
            evidence_root
            if evidence_root.is_absolute()
            else Path.cwd() / evidence_root
        )
        relative_evidence = lexical_evidence.relative_to(audit_root)
    except (OSError, ValueError) as exc:
        raise FinalAuditError("FINAL_AUDIT_EVIDENCE_ROOT_INVALID") from exc
    evidence = _portable_directory(
        audit_root,
        str(relative_evidence),
        error="FINAL_AUDIT_EVIDENCE_ROOT_INVALID",
    )
    candidates: dict[str, Any] = {}
    for candidate in CANDIDATES:
        entries = []
        for evidence_id in EXPECTED_EVIDENCE_CLAIMS:
            envelope_path = evidence / candidate / f"{evidence_id}.json"
            envelope_relative = envelope_path.relative_to(audit_root)
            if (
                _has_symlink_component(audit_root, envelope_relative)
                or not envelope_path.is_file()
            ):
                raise FinalAuditError(f"FINAL_AUDIT_EVIDENCE_MISSING:{candidate}:{evidence_id}")
            _validate_evidence_envelope(
                envelope_path,
                audit_root=audit_root,
                candidate=candidate,
                evidence_id=evidence_id,
                benchmark_subject_commit=benchmark_subject_commit,
            )
            entries.append(
                {
                    "evidenceId": evidence_id,
                    "path": str(envelope_path.relative_to(audit_root)),
                    "sha256": _sha256(envelope_path),
                    "schemaVersion": EVIDENCE_SCHEMA,
                    "status": "PASS",
                }
            )
        candidates[candidate] = {
            "evidence": entries,
            "correctnessStatus": "PASS",
            "reproducibilityStatus": "PASS",
            "rubrics": _rubric_status(),
            "status": "PASS",
        }
    document = {
        "schemaVersion": LEDGER_SCHEMA,
        "benchmarkSubjectCommit": benchmark_subject_commit,
        "evidenceRoot": str(evidence.relative_to(audit_root)),
        "candidates": candidates,
        "status": "PASS",
    }
    exclusive_json_write(output, document)
    return document


def _validate_rubrics(
    value: Any,
    *,
    candidate: str,
) -> None:
    error = f"FINAL_AUDIT_RUBRIC_INVALID:{candidate}"
    rubrics = _exact_object(
        value,
        {"purityAuditability", "maintainability", "integrationFit"},
        error=error,
    )
    expected = _rubric_status()
    if rubrics != expected:
        raise FinalAuditError(error)


def validate_final_candidate_audit(
    ledger_path: Path,
    *,
    repository_root: Path,
    benchmark_subject_commit: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
    """Ledger와 모든 portable source artifact를 다시 읽어 고정 점수만 도출한다."""

    _validate_subject(repository_root, benchmark_subject_commit)
    if ledger_path.is_symlink():
        raise FinalAuditError("FINAL_AUDIT_LEDGER_INVALID")
    ledger = ledger_path.resolve(strict=True)
    if not ledger.is_file():
        raise FinalAuditError("FINAL_AUDIT_LEDGER_INVALID")
    audit_root = ledger.parent.resolve(strict=True)
    document = _exact_object(
        strict_json_load(ledger),
        {
            "schemaVersion",
            "benchmarkSubjectCommit",
            "evidenceRoot",
            "candidates",
            "status",
        },
        error="FINAL_AUDIT_LEDGER_INVALID",
    )
    if (
        document["schemaVersion"] != LEDGER_SCHEMA
        or document["benchmarkSubjectCommit"] != benchmark_subject_commit
        or document["status"] != "PASS"
        or not isinstance(document["candidates"], dict)
        or set(document["candidates"]) != set(CANDIDATES)
        or not isinstance(document["evidenceRoot"], str)
        or Path(document["evidenceRoot"]).is_absolute()
        or ".." in Path(document["evidenceRoot"]).parts
    ):
        raise FinalAuditError("FINAL_AUDIT_LEDGER_INVALID")
    evidence_root = _portable_directory(
        audit_root,
        document["evidenceRoot"],
        error="FINAL_AUDIT_EVIDENCE_ROOT_INVALID",
    )
    derived: dict[str, dict[str, Any]] = {}
    expected_ids = list(EXPECTED_EVIDENCE_CLAIMS)
    for candidate in CANDIDATES:
        error = f"FINAL_AUDIT_CANDIDATE_INVALID:{candidate}"
        entry = _exact_object(
            document["candidates"][candidate],
            {
                "evidence",
                "correctnessStatus",
                "reproducibilityStatus",
                "rubrics",
                "status",
            },
            error=error,
        )
        if (
            entry["correctnessStatus"] != "PASS"
            or entry["reproducibilityStatus"] != "PASS"
            or entry["status"] != "PASS"
            or not isinstance(entry["evidence"], list)
            or len(entry["evidence"]) != len(expected_ids)
        ):
            raise FinalAuditError(error)
        _validate_rubrics(entry["rubrics"], candidate=candidate)
        actual_ids: list[str] = []
        evidence_sha256: list[str] = []
        for raw_evidence, expected_id in zip(
            entry["evidence"],
            expected_ids,
            strict=True,
        ):
            evidence_entry = _exact_object(
                raw_evidence,
                {
                    "evidenceId",
                    "path",
                    "sha256",
                    "schemaVersion",
                    "status",
                },
                error=error,
            )
            if (
                evidence_entry["evidenceId"] != expected_id
                or evidence_entry["schemaVersion"] != EVIDENCE_SCHEMA
                or evidence_entry["status"] != "PASS"
            ):
                raise FinalAuditError(error)
            envelope_path = _portable_regular_file(
                audit_root,
                evidence_entry["path"],
                expected_sha256=evidence_entry["sha256"],
                error=error,
            )
            try:
                envelope_path.resolve(strict=True).relative_to(evidence_root.resolve(strict=True))
            except ValueError as exc:
                raise FinalAuditError(error) from exc
            _validate_evidence_envelope(
                envelope_path,
                audit_root=audit_root,
                candidate=candidate,
                evidence_id=expected_id,
                benchmark_subject_commit=benchmark_subject_commit,
            )
            actual_ids.append(expected_id)
            evidence_sha256.append(str(evidence_entry["sha256"]))
        if (
            actual_ids != expected_ids
            or not CORRECTNESS_EVIDENCE.issubset(actual_ids)
            or not REPRODUCIBILITY_EVIDENCE.issubset(actual_ids)
        ):
            raise FinalAuditError(error)
        derived[candidate] = {
            "correctnessPoints": 35.0,
            "purityAuditabilityPoints": 20.0,
            "reproducibilityPoints": 15.0,
            "maintainabilityPoints": 10.0,
            "integrationFitPoints": 5.0,
            "evidenceSha256": evidence_sha256,
        }
    return document, derived, _sha256(ledger)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    generate = subcommands.add_parser("generate")
    generate.add_argument("--repository-root", type=Path, required=True)
    generate.add_argument("--benchmark-subject-commit", required=True)
    generate.add_argument("--evidence-root", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    validate = subcommands.add_parser("validate")
    validate.add_argument("--repository-root", type=Path, required=True)
    validate.add_argument("--benchmark-subject-commit", required=True)
    validate.add_argument("--ledger", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "generate":
            document = generate_final_candidate_audit(
                repository_root=arguments.repository_root,
                benchmark_subject_commit=arguments.benchmark_subject_commit,
                evidence_root=arguments.evidence_root,
                output_path=arguments.output,
            )
            result = {
                "schemaVersion": document["schemaVersion"],
                "candidateCount": len(document["candidates"]),
                "sha256": _sha256(arguments.output),
                "status": "PASS",
            }
        else:
            document, _, digest = validate_final_candidate_audit(
                arguments.ledger,
                repository_root=arguments.repository_root,
                benchmark_subject_commit=arguments.benchmark_subject_commit,
            )
            result = {
                "schemaVersion": document["schemaVersion"],
                "candidateCount": len(document["candidates"]),
                "sha256": digest,
                "status": "PASS",
            }
        print(json.dumps(result, allow_nan=False, sort_keys=True))
    except (FinalAuditError, OSError, subprocess.SubprocessError) as exc:
        print(f"FINAL_CANDIDATE_AUDIT_FAIL:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
