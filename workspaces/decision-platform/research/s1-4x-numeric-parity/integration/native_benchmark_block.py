#!/usr/bin/env python3
"""Scala/Haskell native aggregate를 frozen 공통 block-result로 변환하고 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchmark_input_ledger import validate_input_ledger
from gate import GateError, exclusive_json_write, strict_json_load

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))

from benchmark_contract import ContractError, sha256_file  # type: ignore[import-not-found]  # noqa: E402
from validate_benchmark_report import (  # type: ignore[import-not-found]  # noqa: E402
    validate_block_result,
    validate_plan,
)

SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
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
    "status",
}


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


def validate_native_contract_evidence(
    value: Any,
    *,
    boundary_id: str,
    selector_id: str,
    block_directory: Path,
    native_cases: list[dict[str, Any]],
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
        or document["schemaVersion"]
        != "s1.4x-native-contract-validation-v1"
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
    actual_case_ids: list[str] = []
    for evidence, native_case in zip(
        document["cases"],
        native_cases,
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
            len(raw_samples)
            if isinstance(raw_samples, list)
            else native_case.get("samples")
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
                or not isinstance(raw_samples, list)
                or len(raw_samples) != 30
                or item["rawEvidenceSha256"] != _canonical_sha256(raw_samples)
            ):
                raise GateError(
                    f"NATIVE_CONTRACT_RAW_EVIDENCE_INVALID:{case_id}"
                )
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
            raw_path.resolve(strict=True).relative_to(
                block_directory.resolve(strict=True)
            )
        except (OSError, ValueError) as exc:
            raise GateError(f"NATIVE_CONTRACT_RAW_PATH_INVALID:{case_id}") from exc
        if (
            raw_path.is_symlink()
            or not raw_path.is_file()
            or SHA256.fullmatch(str(item["rawEvidenceSha256"])) is None
            or sha256_file(raw_path) != item["rawEvidenceSha256"]
        ):
            raise GateError(f"NATIVE_CONTRACT_RAW_EVIDENCE_INVALID:{case_id}")
        actual_case_ids.append(str(case_id))
    if (
        len(document["cases"]) != len(native_cases)
        or actual_case_ids != [str(case["caseId"]) for case in native_cases]
    ):
        raise GateError("NATIVE_CONTRACT_CASE_ORDER_INVALID")
    return document


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
    qualification_document = (
        qualification if isinstance(qualification, dict) else {}
    )
    qualification_subject = qualification_document.get("subject")
    qualification_run = qualification_document.get("run")
    host_validity = qualification_document.get("hostValidity")
    if (
        qualification_document.get("schemaVersion")
        != "s1.4x-timeout-qualification-v1"
        or qualification_document.get("phase") != "MEASUREMENT"
        or qualification_document.get("measurementEntered") is not True
        or not isinstance(qualification_subject, dict)
        or qualification_subject.get("benchmarkSubjectCommit")
        != benchmark_subject_commit
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
    expected_rotation = plan["execution"]["candidateOrderBlocks"][
        outer_repetition - 1
    ]
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
            "nativeReportPath": (
                f"{run_id}/{rotation_id}/{boundary_id}/{family_id}/native.json"
            ),
            "nativeReportSha256": native_report_sha256,
        },
        "environment": {
            "hostFingerprintSha256": portable_host_id,
            "hostValidityArtifactSha256": host_artifact_sha,
            "toolchainProvenanceSha256": toolchain_provenance_sha256,
            "fixtureFreezeIdentity": plan["fixtureFreezeIdentity"],
            "effectiveRuntimeArgumentsSha256": document[
                "effectiveRuntimeArgumentsSha256"
            ],
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


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
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
            or native.get("nativeContractValidationSha256")
            != sha256_file(native_contract_path)
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
        validate_native_contract_evidence(
            strict_json_load(native_contract_path),
            boundary_id=arguments.boundary,
            selector_id=arguments.selector,
            block_directory=block_dir,
            native_cases=native_cases,
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
                repo
                / "workspaces/decision-platform/research/s1-4x-numeric-parity/"
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
