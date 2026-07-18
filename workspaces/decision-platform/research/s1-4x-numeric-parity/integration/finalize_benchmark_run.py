#!/usr/bin/env python3
"""완료된 87-block run을 검증하고 portable ledger, aggregate, scorecard를 만든다."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from benchmark_input_ledger import validate_input_ledger
from final_candidate_audit import validate_final_candidate_audit
from gate import exclusive_json_write, strict_json_load
from native_benchmark_block import validate_native_contract_evidence

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))

from benchmark_contract import (  # type: ignore[import-not-found]  # noqa: E402
    ContractError,
    sha256_file,
)
from run_rotated_blocks import (  # type: ignore[import-not-found]  # noqa: E402
    ScheduledBlock,
    build_schedule,
)
from validate_benchmark_report import (  # type: ignore[import-not-found]  # noqa: E402
    validate_block_result,
    validate_plan,
)

SHA256_LENGTH = 64
CANDIDATES = ("scala", "haskell")
UNIT_TO_NS = {"ns": 1.0, "us": 1_000.0, "ms": 1_000_000.0, "s": 1_000_000_000.0}


class BenchmarkSummaryError(ValueError):
    """Full benchmark 산출물이 frozen completeness/score 규칙을 만족하지 않는다."""


def _geometric_mean(values: Sequence[float]) -> float:
    if not values or any(not math.isfinite(value) or value <= 0.0 for value in values):
        raise BenchmarkSummaryError("GEOMETRIC_MEAN_INPUT_INVALID")
    return math.exp(math.fsum(math.log(value) for value in values) / len(values))


def distribution(values: Sequence[float]) -> dict[str, float | int]:
    """세 outer repetition의 min/median/max만 계산하며 synthetic p95를 만들지 않는다."""

    if len(values) != 3 or any(
        not math.isfinite(value) or value <= 0.0 for value in values
    ):
        raise BenchmarkSummaryError("THREE_REPETITION_DISTRIBUTION_INVALID")
    ordered = sorted(values)
    return {
        "sampleCount": 3,
        "min": ordered[0],
        "median": statistics.median(ordered),
        "max": ordered[2],
    }


def nearest_rank_p95(values: Sequence[float]) -> float:
    """Native framework sample에만 nearest-rank p95를 적용한다."""

    if not values or any(not math.isfinite(value) or value < 0.0 for value in values):
        raise BenchmarkSummaryError("NATIVE_SAMPLE_INVALID")
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def score_candidate_performance(
    candidate_case_medians: Mapping[str, Mapping[str, float]],
    family_by_case: Mapping[str, str],
    *,
    timed_out_families: Mapping[str, set[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """각 case fastest ratio→family GM→6-family GM으로 15점을 계산한다."""

    if set(candidate_case_medians) != set(CANDIDATES):
        raise BenchmarkSummaryError("CANDIDATE_SET_MISMATCH")
    expected_cases = set(family_by_case)
    timeout_map = timed_out_families or {candidate: set() for candidate in CANDIDATES}
    if set(timeout_map) != set(CANDIDATES):
        raise BenchmarkSummaryError("TIMEOUT_CANDIDATE_SET_MISMATCH")
    expected_families = set(family_by_case.values())
    for candidate, values in candidate_case_medians.items():
        timed_cases = {
            case_id
            for case_id, family_id in family_by_case.items()
            if family_id in timeout_map[candidate]
        }
        if (
            not timeout_map[candidate].issubset(expected_families)
            or set(values) - expected_cases
            or expected_cases - timed_cases - set(values)
        ):
            raise BenchmarkSummaryError("CANDIDATE_CASE_SET_MISMATCH")
    family_ratios: dict[str, dict[str, list[float]]] = {
        candidate: defaultdict(list) for candidate in CANDIDATES
    }
    for case_id, family_id in family_by_case.items():
        active_candidates = [
            candidate
            for candidate in CANDIDATES
            if family_id not in timeout_map[candidate]
        ]
        timings = {
            candidate: candidate_case_medians[candidate][case_id]
            for candidate in active_candidates
        }
        if any(
            not math.isfinite(timing) or timing <= 0.0
            for timing in timings.values()
        ):
            raise BenchmarkSummaryError(f"CANDIDATE_TIMING_INVALID:{case_id}")
        if not timings:
            continue
        fastest = min(timings.values())
        for candidate, timing in timings.items():
            family_ratios[candidate][family_id].append(fastest / timing)
    scores: dict[str, dict[str, Any]] = {}
    for candidate in CANDIDATES:
        non_timeout_families = expected_families - timeout_map[candidate]
        if set(family_ratios[candidate]) != non_timeout_families:
            raise BenchmarkSummaryError("CANDIDATE_FAMILY_SET_MISMATCH")
        per_family = {
            family: (
                0.0
                if family in timeout_map[candidate]
                else _geometric_mean(family_ratios[candidate][family])
            )
            for family in sorted(expected_families)
        }
        aggregate = (
            0.0
            if any(value == 0.0 for value in per_family.values())
            else _geometric_mean(list(per_family.values()))
        )
        scores[candidate] = {
            "familyRatios": per_family,
            "aggregateRatio": aggregate,
            "performancePoints": 15.0 * aggregate,
        }
    return scores


def _raw_hash_manifest(run_directory: Path) -> list[dict[str, Any]]:
    artifacts = []
    for path in sorted(
        run_directory.rglob("*"),
        key=lambda item: item.relative_to(run_directory).as_posix().encode("utf-8"),
    ):
        if path.is_symlink():
            raise BenchmarkSummaryError(
                f"BENCHMARK_ARTIFACT_SYMLINK:{path.relative_to(run_directory)}"
            )
        if path.is_file():
            artifacts.append(
                {
                    "path": path.relative_to(run_directory).as_posix(),
                    "sha256": sha256_file(path),
                    "sizeBytes": path.stat().st_size,
                }
            )
    if not artifacts:
        raise BenchmarkSummaryError("BENCHMARK_ARTIFACTS_EMPTY")
    return artifacts


def _validate_performance_timeout(
    value: Any,
    *,
    plan: Mapping[str, Any],
    block: ScheduledBlock,
    qualification: Mapping[str, Any],
    qualification_sha256: str,
    block_directory: Path,
) -> dict[str, Any]:
    """Frozen timeout evidence와 timeout 직전 artifact bytes를 exact 결합한다."""

    expected_fields = {
        "schemaVersion",
        "planId",
        "runId",
        "rotationId",
        "outerRepetition",
        "boundaryId",
        "familyId",
        "selectorId",
        "timeoutSeconds",
        "measurementEntered",
        "timeoutQualificationSha256",
        "terminationSequence",
        "partialArtifactsUsedForScoring",
        "scoreDisposition",
        "continueRemainingPredeclaredMatrix",
        "artifacts",
    }
    run = qualification.get("run")
    if (
        not isinstance(value, dict)
        or set(value) != expected_fields
        or value["schemaVersion"] != "s1.4x-valid-performance-timeout-v1"
        or value["planId"] != plan["planId"]
        or not isinstance(run, dict)
        or value["runId"] != run.get("runId")
        or value["rotationId"] != block.rotation_id
        or value["outerRepetition"] != block.outer_repetition
        or value["boundaryId"] != block.boundary_id
        or value["familyId"] != block.family_id
        or value["selectorId"] != block.selector_id
        or value["timeoutSeconds"] != block.timeout_seconds
        or value["measurementEntered"] is not True
        or value["timeoutQualificationSha256"] != qualification_sha256
        or value["terminationSequence"]
        != ["SIGTERM", "bounded-grace-5s", "SIGKILL-if-needed"]
        or value["partialArtifactsUsedForScoring"] is not False
        or value["scoreDisposition"] != "candidate-family-ratio-zero"
        or value["continueRemainingPredeclaredMatrix"] is not True
        or not isinstance(value["artifacts"], list)
        or not value["artifacts"]
    ):
        raise BenchmarkSummaryError(
            f"VALID_PERFORMANCE_TIMEOUT_INVALID:{block.selector_id}"
        )
    artifacts: list[dict[str, Any]] = value["artifacts"]
    actual_names = sorted(
        path.name
        for path in block_directory.iterdir()
        if path.name != "valid-performance-timeout.json"
        and path.is_file()
        and not path.is_symlink()
    )
    evidence_names: list[str] = []
    for artifact in artifacts:
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"path", "sha256", "sizeBytes"}
            or not isinstance(artifact["path"], str)
            or not artifact["path"]
            or Path(artifact["path"]).name != artifact["path"]
            or len(str(artifact["sha256"])) != SHA256_LENGTH
            or any(
                character not in "0123456789abcdef"
                for character in str(artifact["sha256"])
            )
            or type(artifact["sizeBytes"]) is not int
            or artifact["sizeBytes"] < 0
        ):
            raise BenchmarkSummaryError(
                f"VALID_PERFORMANCE_TIMEOUT_ARTIFACT_INVALID:{block.selector_id}"
            )
        artifact_path = block_directory / artifact["path"]
        if (
            artifact_path.is_symlink()
            or not artifact_path.is_file()
            or artifact_path.stat().st_size != artifact["sizeBytes"]
            or sha256_file(artifact_path) != artifact["sha256"]
        ):
            raise BenchmarkSummaryError(
                f"VALID_PERFORMANCE_TIMEOUT_ARTIFACT_MISMATCH:{block.selector_id}"
            )
        evidence_names.append(artifact["path"])
    if (
        evidence_names != sorted(evidence_names)
        or len(evidence_names) != len(set(evidence_names))
        or evidence_names != actual_names
        or "timeout-qualification.json" not in evidence_names
        or next(
            item["sha256"]
            for item in artifacts
            if item["path"] == "timeout-qualification.json"
        )
        != qualification_sha256
    ):
        raise BenchmarkSummaryError(
            f"VALID_PERFORMANCE_TIMEOUT_ARTIFACT_CLOSURE_INVALID:{block.selector_id}"
        )
    return value


def _nullable_number(value: Any, *, field: str, case_id: str) -> float | None:
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise BenchmarkSummaryError(f"NATIVE_STATISTIC_INVALID:{case_id}:{field}")
    return float(value)


def _native_statistics(
    path: Path,
    *,
    boundary_id: str,
    selector_id: str,
    native_report_sha256: str,
    expected_cases: Sequence[dict[str, Any]],
    expected_unit: str,
) -> list[dict[str, Any]]:
    document = strict_json_load(path)
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "schemaVersion",
            "boundaryId",
            "selectorId",
            "nativeReportSha256",
            "cases",
            "status",
        }
        or document["schemaVersion"] != "s1.4x-native-statistics-v1"
        or document["boundaryId"] != boundary_id
        or document["selectorId"] != selector_id
        or document["nativeReportSha256"] != native_report_sha256
        or document["status"] != "PASS"
        or not isinstance(document["cases"], list)
    ):
        raise BenchmarkSummaryError(f"NATIVE_STATISTICS_DOCUMENT_INVALID:{selector_id}")
    expected_fields = {
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
    expected_by_id = {case["caseId"]: case for case in expected_cases}
    actual_ids = []
    validated = []
    for item in document["cases"]:
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise BenchmarkSummaryError(
                f"NATIVE_STATISTICS_ENTRY_INVALID:{selector_id}"
            )
        case_id = item["caseId"]
        frozen = expected_by_id.get(case_id)
        if frozen is None:
            raise BenchmarkSummaryError(f"NATIVE_STATISTICS_CASE_INVALID:{case_id}")
        actual_ids.append(case_id)
        if (
            type(item["nativeSampleCount"]) is not int
            or item["nativeSampleCount"] < 2
            or item["nativeUnit"] != expected_unit
            or item["logicalOperationsPerInvocation"]
            != frozen["logicalOperationsPerInvocation"]
            or not isinstance(item["dispersionMetric"], str)
            or not item["dispersionMetric"]
        ):
            raise BenchmarkSummaryError(
                f"NATIVE_STATISTICS_METADATA_INVALID:{case_id}"
            )
        p95 = _nullable_number(item["nativeP95"], field="nativeP95", case_id=case_id)
        confidence_level = item["confidenceLevel"]
        confidence_low = _nullable_number(
            item["confidenceLow"],
            field="confidenceLow",
            case_id=case_id,
        )
        confidence_high = _nullable_number(
            item["confidenceHigh"],
            field="confidenceHigh",
            case_id=case_id,
        )
        dispersion = _nullable_number(
            item["dispersionValue"],
            field="dispersionValue",
            case_id=case_id,
        )
        normalized_p95 = _nullable_number(
            item["normalizedP95NsPerLogicalOperation"],
            field="normalizedP95NsPerLogicalOperation",
            case_id=case_id,
        )
        normalized_low = _nullable_number(
            item["normalizedConfidenceLowNsPerLogicalOperation"],
            field="normalizedConfidenceLowNsPerLogicalOperation",
            case_id=case_id,
        )
        normalized_high = _nullable_number(
            item["normalizedConfidenceHighNsPerLogicalOperation"],
            field="normalizedConfidenceHighNsPerLogicalOperation",
            case_id=case_id,
        )
        normalized_dispersion = _nullable_number(
            item["normalizedDispersionNsPerLogicalOperation"],
            field="normalizedDispersionNsPerLogicalOperation",
            case_id=case_id,
        )
        if (
            dispersion is None
            or normalized_dispersion is None
            or (p95 is None) != (normalized_p95 is None)
            or (confidence_low is None) != (confidence_high is None)
            or (confidence_low is None) != (normalized_low is None)
            or (confidence_high is None) != (normalized_high is None)
            or (p95 is None and confidence_low is None)
            or (
                confidence_low is not None
                and confidence_high is not None
                and confidence_low > confidence_high
            )
            or (
                confidence_level is not None
                and (
                    not isinstance(confidence_level, (int, float))
                    or isinstance(confidence_level, bool)
                    or not 0.0 < float(confidence_level) < 1.0
                )
            )
            or (confidence_low is None and confidence_level is not None)
        ):
            raise BenchmarkSummaryError(f"NATIVE_STATISTICS_SHAPE_INVALID:{case_id}")
        scale = UNIT_TO_NS[expected_unit] / frozen["logicalOperationsPerInvocation"]
        pairs = (
            (p95, normalized_p95),
            (confidence_low, normalized_low),
            (confidence_high, normalized_high),
            (dispersion, normalized_dispersion),
        )
        if any(
            native is not None
            and normalized is not None
            and not math.isclose(
                native * scale,
                normalized,
                rel_tol=1e-12,
                abs_tol=1e-9,
            )
            for native, normalized in pairs
        ):
            raise BenchmarkSummaryError(
                f"NATIVE_STATISTICS_NORMALIZATION_INVALID:{case_id}"
            )
        validated.append(item)
    if actual_ids != [case["caseId"] for case in expected_cases]:
        raise BenchmarkSummaryError(
            f"NATIVE_STATISTICS_CASE_ORDER_INVALID:{selector_id}"
        )
    return validated


def finalize_run(
    *,
    plan_path: Path,
    run_directory: Path,
    output_directory: Path,
    benchmark_subject_commit: str,
    audit_ledger_path: Path,
) -> dict[str, Any]:
    """87 PASS blocks와 3회 case sample을 검증한 뒤 네 portable report를 기록한다."""

    run = run_directory.resolve(strict=True)
    output = output_directory.resolve()
    if output.exists() or output.is_symlink():
        raise BenchmarkSummaryError("BENCHMARK_SUMMARY_OUTPUT_ALREADY_EXISTS")
    resolved_plan_path = plan_path.resolve(strict=True)
    plan = validate_plan(resolved_plan_path)
    repo_root = resolved_plan_path.parents[5]
    schedule = build_schedule(plan)
    if len(schedule) != 87:
        raise BenchmarkSummaryError("BENCHMARK_SCHEDULE_COUNT_INVALID")
    _, audit_points, audit_sha256 = validate_final_candidate_audit(
        audit_ledger_path,
        repository_root=repo_root,
        benchmark_subject_commit=benchmark_subject_commit,
    )
    reports = []
    host_ledger = []
    portable_host_ids: set[str] = set()
    measurements: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    native_disclosures: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    timed_out_blocks: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    candidate_timed_out_families: dict[str, set[str]] = {
        candidate: set() for candidate in CANDIDATES
    }
    case_by_id = {case["caseId"]: case for case in plan["cases"]}
    selector_by_id = {
        selector["selectorId"]: selector for selector in plan["familySelectors"]
    }
    for block in schedule:
        block_directory = (
            run
            / block.rotation_id
            / block.boundary_id
            / block.family_id
        )
        host_path = block_directory / "host-validity.json"
        qualification_path = block_directory / "timeout-qualification.json"
        host = strict_json_load(host_path)
        qualification = strict_json_load(qualification_path)
        if (
            not isinstance(host, dict)
            or host.get("status") != "PASS"
            or host.get("failureCount") != 0
            or not isinstance(qualification, dict)
            or qualification.get("phase") != "MEASUREMENT"
            or qualification.get("measurementEntered") is not True
            or qualification.get("subject", {}).get("benchmarkSubjectCommit")
            != benchmark_subject_commit
        ):
            raise BenchmarkSummaryError(
                f"HOST_OR_QUALIFICATION_INVALID:{block.selector_id}"
            )
        portable_host_id = host.get("portableHostIdSha256")
        if not isinstance(portable_host_id, str) or len(portable_host_id) != 64:
            raise BenchmarkSummaryError("PORTABLE_HOST_ID_INVALID")
        portable_host_ids.add(portable_host_id)
        input_ledger_path = block_directory / "input-ledger.json"
        validate_input_ledger(
            strict_json_load(input_ledger_path),
            plan=plan,
            plan_path=resolved_plan_path,
            repo_root=repo_root,
            boundary_id=block.boundary_id,
            selector_id=block.selector_id,
        )
        input_ledger_sha256 = sha256_file(input_ledger_path)
        timeout_path = block_directory / "valid-performance-timeout.json"
        completion_status = (
            "VALID_PERFORMANCE_TIMEOUT" if timeout_path.exists() else "PASS"
        )
        host_ledger.append(
            {
                "rotationId": block.rotation_id,
                "boundaryId": block.boundary_id,
                "familyId": block.family_id,
                "portableHostIdSha256": portable_host_id,
                "hostValiditySha256": sha256_file(host_path),
                "qualificationSha256": sha256_file(qualification_path),
                "metadata": host.get("metadata"),
                "status": completion_status,
            }
        )
        if timeout_path.exists():
            _validate_performance_timeout(
                strict_json_load(timeout_path),
                plan=plan,
                block=block,
                qualification=qualification,
                qualification_sha256=sha256_file(qualification_path),
                block_directory=block_directory,
            )
            timed_out_blocks[block.boundary_id][block.family_id].append(
                block.rotation_id
            )
            if block.boundary_id in CANDIDATES:
                candidate_timed_out_families[block.boundary_id].add(
                    block.family_id
                )
            continue
        result_path = block_directory / "block-result.json"
        native_path = block_directory / "native.json"
        native_document = strict_json_load(native_path)
        native_contract_path = block_directory / "native-contract-validation.json"
        if (
            not isinstance(native_document, dict)
            or native_document.get("inputLedgerSha256")
            != input_ledger_sha256
            or native_document.get("nativeContractValidationSha256")
            != sha256_file(native_contract_path)
            or not isinstance(native_document.get("cases"), list)
        ):
            raise BenchmarkSummaryError(
                f"BENCHMARK_NATIVE_EVIDENCE_BINDING_INVALID:{block.selector_id}"
            )
        selector = selector_by_id[block.selector_id]
        expected_cases = [
            case_by_id[case_id] for case_id in selector["expectedCaseIds"]
        ]
        statistics_entries = _native_statistics(
            block_directory / "native-statistics.json",
            boundary_id=block.boundary_id,
            selector_id=block.selector_id,
            native_report_sha256=sha256_file(native_path),
            expected_cases=expected_cases,
            expected_unit=plan["execution"]["nativeTimeUnit"][block.boundary_id],
        )
        validate_native_contract_evidence(
            strict_json_load(native_contract_path),
            boundary_id=block.boundary_id,
            selector_id=block.selector_id,
            block_directory=block_directory,
            native_cases=native_document["cases"],
            native_statistics_cases=statistics_entries,
            plan_path=resolved_plan_path,
            fixture_root_path=(
                repo_root
                / "workspaces/decision-platform/research/"
                "s1-4x-numeric-parity/contract/fixtures"
            ),
            input_ledger_path=input_ledger_path,
            effective_runtime_arguments_sha256=str(
                native_document["effectiveRuntimeArgumentsSha256"]
            ),
            profile=str(native_document["profile"]),
        )
        report = validate_block_result(
            result_path,
            plan_path=plan_path,
            native_report_path=native_path,
            expected_boundary_id=block.boundary_id,
            expected_selector_id=block.selector_id,
        )
        if (
            report["benchmarkSubjectCommit"] != benchmark_subject_commit
            or report["block"]["status"] != "PASS"
        ):
            raise BenchmarkSummaryError(
                f"BENCHMARK_SUBJECT_OR_STATUS_INVALID:{block.selector_id}"
            )
        for case in report["cases"]:
            measurements[block.boundary_id][case["caseId"]].append(
                float(case["normalizedNsPerLogicalOperation"])
            )
        for entry in statistics_entries:
            native_disclosures[block.boundary_id][entry["caseId"]].append(
                {"rotationId": block.rotation_id, **entry}
            )
        reports.append(report)
    timeout_count = sum(
        len(rotations)
        for families in timed_out_blocks.values()
        for rotations in families.values()
    )
    if len(reports) + timeout_count != 87:
        raise BenchmarkSummaryError("BENCHMARK_COMPLETED_BLOCK_COUNT_INVALID")
    if len(portable_host_ids) != 1:
        raise BenchmarkSummaryError("CROSS_BLOCK_HOST_ID_MISMATCH")
    expected_boundaries = {
        item["boundaryId"]: item["expectedCaseIds"]
        for item in plan["executionBoundaries"]
    }
    boundary_summaries: dict[str, Any] = {}
    for boundary_id, expected_case_ids in expected_boundaries.items():
        actual = measurements[boundary_id]
        expected_set = set(expected_case_ids)
        timed_families = set(timed_out_blocks[boundary_id])
        missing = expected_set - set(actual)
        if any(family_by_case_id not in timed_families for family_by_case_id in (
            case_by_id[case_id]["familyId"] for case_id in missing
        )) or set(actual) - expected_set:
            raise BenchmarkSummaryError(
                f"BOUNDARY_CASE_SET_MISMATCH:{boundary_id}"
            )
        case_summaries = {}
        for case_id in expected_case_ids:
            family_id = case_by_id[case_id]["familyId"]
            samples = actual.get(case_id, [])
            disclosures = native_disclosures[boundary_id].get(case_id, [])
            if family_id in timed_families:
                expected_successful = 3 - len(
                    timed_out_blocks[boundary_id][family_id]
                )
                if (
                    len(samples) != expected_successful
                    or len(disclosures) != expected_successful
                ):
                    raise BenchmarkSummaryError(
                        f"TIMEOUT_REPETITION_ACCOUNTING_INVALID:{boundary_id}:{case_id}"
                    )
                case_summaries[case_id] = {
                    "status": "VALID_PERFORMANCE_TIMEOUT",
                    "completedRepetitionCount": 3,
                    "successfulRepetitionCount": len(samples),
                    "timedOutRepetitions": timed_out_blocks[boundary_id][
                        family_id
                    ],
                    "successfulCrossBlockDistribution": (
                        {
                            "sampleCount": len(samples),
                            "min": min(samples),
                            "median": statistics.median(samples),
                            "max": max(samples),
                        }
                        if samples
                        else None
                    ),
                    "nativeFrameworkStatistics": disclosures,
                }
            else:
                if len(disclosures) != 3:
                    raise BenchmarkSummaryError(
                        f"NATIVE_STATISTICS_REPETITION_COUNT_INVALID:{boundary_id}:{case_id}"
                    )
                case_summaries[case_id] = {
                    "status": "PASS",
                    **distribution(samples),
                    "nativeFrameworkStatistics": disclosures,
                }
        boundary_summaries[boundary_id] = {
            "caseCount": len(expected_case_ids),
            "validPerformanceTimeoutFamilyCount": len(timed_families),
            "cases": case_summaries,
        }
    family_by_case = {
        case["caseId"]: case["familyId"] for case in plan["cases"]
    }
    candidate_case_medians = {
        candidate: {
            case_id: float(statistics.median(samples))
            for case_id, samples in measurements[candidate].items()
            if samples
        }
        for candidate in CANDIDATES
    }
    performance = score_candidate_performance(
        candidate_case_medians,
        family_by_case,
        timed_out_families=candidate_timed_out_families,
    )
    score_candidates = {}
    for candidate in CANDIDATES:
        audit_candidate = audit_points[candidate]
        categories = {
            "correctness": {
                "maxPoints": 35.0,
                "points": float(audit_candidate["correctnessPoints"]),
            },
            "purityAuditability": {
                "maxPoints": 20.0,
                "points": float(audit_candidate["purityAuditabilityPoints"]),
            },
            "reproducibility": {
                "maxPoints": 15.0,
                "points": float(audit_candidate["reproducibilityPoints"]),
            },
            "performance": {
                "maxPoints": 15.0,
                "points": performance[candidate]["performancePoints"],
            },
            "maintainability": {
                "maxPoints": 10.0,
                "points": float(audit_candidate["maintainabilityPoints"]),
            },
            "integrationFit": {
                "maxPoints": 5.0,
                "points": float(audit_candidate["integrationFitPoints"]),
            },
        }
        score_candidates[candidate] = {
            "eligibility": "QUALIFIED",
            "categories": categories,
            "totalPoints": math.fsum(
                category["points"] for category in categories.values()
            ),
            "performance": performance[candidate],
            "status": "PASS",
        }
    raw_artifacts = _raw_hash_manifest(run)
    summary = {
        "schemaVersion": "s1.4x-full-benchmark-summary-v1",
        "planId": plan["planId"],
        "benchmarkSubjectCommit": benchmark_subject_commit,
        "portableHostIdSha256": next(iter(portable_host_ids)),
        "outerRepetitions": 3,
        "scheduledBlockCount": 87,
        "completedBlockCount": len(reports) + timeout_count,
        "passBlockCount": len(reports),
        "validPerformanceTimeoutCount": timeout_count,
        "familyCount": 6,
        "candidateCaseCountPerRepetition": 89,
        "boundarySummaries": boundary_summaries,
        "partialBlockCount": 0,
        "notMeasuredCount": 0,
        "status": (
            "PASS"
            if timeout_count == 0
            else "PASS_WITH_VALID_PERFORMANCE_TIMEOUTS"
        ),
    }
    host_document = {
        "schemaVersion": "s1.4x-full-benchmark-host-ledger-v1",
        "benchmarkSubjectCommit": benchmark_subject_commit,
        "portableHostIdSha256": next(iter(portable_host_ids)),
        "blockCount": len(host_ledger),
        "blocks": host_ledger,
        "status": "PASS",
    }
    hash_document = {
        "schemaVersion": "s1.4x-full-benchmark-raw-hash-manifest-v1",
        "benchmarkSubjectCommit": benchmark_subject_commit,
        "artifactCount": len(raw_artifacts),
        "artifacts": raw_artifacts,
        "status": "PASS",
    }
    scorecard = {
        "schemaVersion": "s1.4x-scorecard-v1",
        "benchmarkSubjectCommit": benchmark_subject_commit,
        "auditLedgerSha256": audit_sha256,
        "scoringRule": (
            "case fastest-candidate ratio, family geometric mean, then "
            "six-family geometric mean"
        ),
        "candidates": score_candidates,
        "status": "PASS",
    }
    output.mkdir(parents=True)
    documents = {
        "benchmark-summary.v1.json": summary,
        "benchmark-host-ledger.v1.json": host_document,
        "benchmark-raw-hash-manifest.v1.json": hash_document,
        "scorecard.v1.json": scorecard,
    }
    for name, document in documents.items():
        exclusive_json_write(output / name, document)
    return {
        "schemaVersion": "s1.4x-benchmark-finalization-v1",
        "benchmarkSubjectCommit": benchmark_subject_commit,
        "documents": {
            name: {
                "sha256": sha256_file(output / name),
                "sizeBytes": (output / name).stat().st_size,
            }
            for name in documents
        },
        "completedBlockCount": 87,
        "validPerformanceTimeoutCount": timeout_count,
        "partialBlockCount": 0,
        "notMeasuredCount": 0,
        "status": summary["status"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--benchmark-subject-commit", required=True)
    parser.add_argument("--audit-ledger", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = finalize_run(
            plan_path=arguments.plan,
            run_directory=arguments.run_directory,
            output_directory=arguments.output_directory,
            benchmark_subject_commit=arguments.benchmark_subject_commit,
            audit_ledger_path=arguments.audit_ledger,
        )
        print(json.dumps(result, allow_nan=False, sort_keys=True))
    except (BenchmarkSummaryError, ContractError, OSError, ValueError) as exc:
        print(f"S1_4X_BENCHMARK_FINALIZATION_FAIL:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
