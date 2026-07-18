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

from gate import exclusive_json_write, strict_json_load

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))

from benchmark_contract import ContractError, sha256_file  # type: ignore[import-not-found]  # noqa: E402
from run_rotated_blocks import build_schedule  # type: ignore[import-not-found]  # noqa: E402
from validate_benchmark_report import (  # type: ignore[import-not-found]  # noqa: E402
    validate_block_result,
    validate_plan,
)

SHA256_LENGTH = 64
CANDIDATES = ("scala", "haskell")


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


def score_candidate_performance(
    candidate_case_medians: Mapping[str, Mapping[str, float]],
    family_by_case: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    """각 case fastest ratio→family GM→6-family GM으로 15점을 계산한다."""

    if set(candidate_case_medians) != set(CANDIDATES):
        raise BenchmarkSummaryError("CANDIDATE_SET_MISMATCH")
    expected_cases = set(family_by_case)
    if any(set(values) != expected_cases for values in candidate_case_medians.values()):
        raise BenchmarkSummaryError("CANDIDATE_CASE_SET_MISMATCH")
    family_ratios: dict[str, dict[str, list[float]]] = {
        candidate: defaultdict(list) for candidate in CANDIDATES
    }
    for case_id in family_by_case:
        timings = {
            candidate: candidate_case_medians[candidate][case_id]
            for candidate in CANDIDATES
        }
        if any(
            not math.isfinite(timing) or timing <= 0.0
            for timing in timings.values()
        ):
            raise BenchmarkSummaryError(f"CANDIDATE_TIMING_INVALID:{case_id}")
        fastest = min(timings.values())
        for candidate, timing in timings.items():
            family_ratios[candidate][family_by_case[case_id]].append(
                fastest / timing
            )
    expected_families = set(family_by_case.values())
    scores: dict[str, dict[str, Any]] = {}
    for candidate in CANDIDATES:
        if set(family_ratios[candidate]) != expected_families:
            raise BenchmarkSummaryError("CANDIDATE_FAMILY_SET_MISMATCH")
        per_family = {
            family: _geometric_mean(ratios)
            for family, ratios in sorted(family_ratios[candidate].items())
        }
        aggregate = _geometric_mean(list(per_family.values()))
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


def _audit_points(
    path: Path,
    *,
    benchmark_subject_commit: str,
) -> tuple[dict[str, Any], str]:
    ledger = strict_json_load(path.resolve(strict=True))
    if (
        not isinstance(ledger, dict)
        or set(ledger)
        != {
            "schemaVersion",
            "benchmarkSubjectCommit",
            "candidates",
            "status",
        }
        or ledger["schemaVersion"] != "s1.4x-final-candidate-audit-v1"
        or ledger["benchmarkSubjectCommit"] != benchmark_subject_commit
        or ledger["status"] != "PASS"
        or not isinstance(ledger["candidates"], dict)
        or set(ledger["candidates"]) != set(CANDIDATES)
    ):
        raise BenchmarkSummaryError("FINAL_AUDIT_LEDGER_INVALID")
    limits = {
        "purityAuditabilityPoints": 20.0,
        "maintainabilityPoints": 10.0,
        "integrationFitPoints": 5.0,
    }
    for candidate in CANDIDATES:
        entry = ledger["candidates"][candidate]
        if (
            not isinstance(entry, dict)
            or set(entry)
            != {
                "correctnessMismatchCount",
                "regressionStatus",
                *limits,
                "evidenceSha256",
            }
            or entry["correctnessMismatchCount"] != 0
            or entry["regressionStatus"] != "PASS"
            or not isinstance(entry["evidenceSha256"], list)
            or not entry["evidenceSha256"]
            or any(
                not isinstance(digest, str)
                or len(digest) != SHA256_LENGTH
                or any(character not in "0123456789abcdef" for character in digest)
                for digest in entry["evidenceSha256"]
            )
        ):
            raise BenchmarkSummaryError(f"FINAL_AUDIT_CANDIDATE_INVALID:{candidate}")
        for field, limit in limits.items():
            value = entry[field]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= limit
            ):
                raise BenchmarkSummaryError(
                    f"FINAL_AUDIT_POINTS_INVALID:{candidate}:{field}"
                )
    return ledger, sha256_file(path)


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
    plan = validate_plan(plan_path.resolve(strict=True))
    schedule = build_schedule(plan)
    if len(schedule) != 87:
        raise BenchmarkSummaryError("BENCHMARK_SCHEDULE_COUNT_INVALID")
    audit, audit_sha256 = _audit_points(
        audit_ledger_path,
        benchmark_subject_commit=benchmark_subject_commit,
    )
    reports = []
    host_ledger = []
    portable_host_ids: set[str] = set()
    measurements: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for block in schedule:
        block_directory = (
            run
            / block.rotation_id
            / block.boundary_id
            / block.family_id
        )
        if (block_directory / "valid-performance-timeout.json").exists():
            raise BenchmarkSummaryError(
                f"PARTIAL_OR_TIMEOUT_BLOCK:{block.selector_id}"
            )
        result_path = block_directory / "block-result.json"
        native_path = block_directory / "native.json"
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
        host_ledger.append(
            {
                "rotationId": block.rotation_id,
                "boundaryId": block.boundary_id,
                "familyId": block.family_id,
                "portableHostIdSha256": portable_host_id,
                "hostValiditySha256": sha256_file(host_path),
                "qualificationSha256": sha256_file(qualification_path),
                "metadata": host.get("metadata"),
                "status": "PASS",
            }
        )
        for case in report["cases"]:
            measurements[block.boundary_id][case["caseId"]].append(
                float(case["normalizedNsPerLogicalOperation"])
            )
        reports.append(report)
    if len(portable_host_ids) != 1:
        raise BenchmarkSummaryError("CROSS_BLOCK_HOST_ID_MISMATCH")
    expected_boundaries = {
        item["boundaryId"]: item["expectedCaseIds"]
        for item in plan["executionBoundaries"]
    }
    boundary_summaries: dict[str, Any] = {}
    for boundary_id, expected_case_ids in expected_boundaries.items():
        actual = measurements[boundary_id]
        if set(actual) != set(expected_case_ids):
            raise BenchmarkSummaryError(
                f"BOUNDARY_CASE_SET_MISMATCH:{boundary_id}"
            )
        boundary_summaries[boundary_id] = {
            "caseCount": len(expected_case_ids),
            "cases": {
                case_id: distribution(actual[case_id])
                for case_id in expected_case_ids
            },
        }
    family_by_case = {
        case["caseId"]: case["familyId"] for case in plan["cases"]
    }
    candidate_case_medians = {
        candidate: {
            case_id: float(boundary_summaries[candidate]["cases"][case_id]["median"])
            for case_id in family_by_case
        }
        for candidate in CANDIDATES
    }
    performance = score_candidate_performance(
        candidate_case_medians,
        family_by_case,
    )
    score_candidates = {}
    for candidate in CANDIDATES:
        audit_candidate = audit["candidates"][candidate]
        categories = {
            "correctness": {"maxPoints": 35.0, "points": 35.0},
            "purityAuditability": {
                "maxPoints": 20.0,
                "points": float(audit_candidate["purityAuditabilityPoints"]),
            },
            "reproducibility": {"maxPoints": 15.0, "points": 15.0},
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
        "completedBlockCount": len(reports),
        "familyCount": 6,
        "candidateCaseCountPerRepetition": 89,
        "boundarySummaries": boundary_summaries,
        "partialBlockCount": 0,
        "notMeasuredCount": 0,
        "status": "PASS",
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
        "partialBlockCount": 0,
        "notMeasuredCount": 0,
        "status": "PASS",
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
