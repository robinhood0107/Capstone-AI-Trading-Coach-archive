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
from pathlib import Path
from typing import Any

from benchmark_input_ledger import validate_input_ledger
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


def _argv_pair(arguments: list[str], option: str, expected: str) -> bool:
    return any(
        arguments[index] == option and arguments[index + 1] == expected
        for index in range(len(arguments) - 1)
    )


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
    ):
        raise GateError(error)


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
) -> None:
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
    if (
        receipt_path.is_symlink()
        or not receipt_path.is_file()
        or sha256_file(receipt_path) != item["executionReceiptSha256"]
    ):
        raise GateError(f"NATIVE_EXECUTION_RECEIPT_DIGEST_INVALID:{case_id}")
    receipt = _exact_object(
        strict_json_load(receipt_path),
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
                "effectiveJvmArgumentsCapabilityPath",
                "effectiveJvmArgumentsCapabilitySha256",
            },
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        capability_path_text = scala_provenance["effectiveJvmArgumentsCapabilityPath"]
        if (
            scala_provenance["kind"] != "scala"
            or not isinstance(capability_path_text, str)
            or not Path(capability_path_text).is_absolute()
            or SHA256.fullmatch(str(scala_provenance["effectiveJvmArgumentsCapabilitySha256"]))
            is None
            or Path(capability_path_text).is_symlink()
            or not Path(capability_path_text).is_file()
            or sha256_file(Path(capability_path_text))
            != scala_provenance["effectiveJvmArgumentsCapabilitySha256"]
        ):
            raise GateError(f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}")
        required_pairs = {
            "-bm": "avgt",
            "-tu": "ns",
            "-t": "1",
            "-f": "3",
            "-wi": "5",
            "-i": "10",
            "-w": "1s",
            "-r": "1s",
            "-rf": "json",
            "-rff": str(block_directory / item["rawEvidencePath"]),
        }
        if any(
            not _argv_pair(arguments, option, expected)
            for option, expected in required_pairs.items()
        ):
            raise GateError(f"NATIVE_EXECUTION_ARGV_INVALID:{case_id}")
    else:
        haskell_provenance = _exact_object(
            candidate_provenance,
            {
                "kind",
                "selectedProfilePath",
                "selectedProfileSha256",
                "selectedProfileId",
                "effectiveCompilerFlagsSha256",
                "ghcupPath",
                "ghcupSha256",
                "stackPath",
                "stackSha256",
                "stackYamlPath",
                "stackYamlSha256",
                "selectedGhcOptions",
            },
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        selected_profile_text = haskell_provenance["selectedProfilePath"]
        ghcup_path_text = haskell_provenance["ghcupPath"]
        stack_path_text = haskell_provenance["stackPath"]
        stack_yaml_path_text = haskell_provenance["stackYamlPath"]
        selected_options = haskell_provenance["selectedGhcOptions"]
        if (
            haskell_provenance["kind"] != "haskell"
            or not isinstance(selected_profile_text, str)
            or not Path(selected_profile_text).is_absolute()
            or not isinstance(ghcup_path_text, str)
            or not isinstance(stack_path_text, str)
            or not isinstance(stack_yaml_path_text, str)
            or not Path(ghcup_path_text).is_absolute()
            or not Path(stack_path_text).is_absolute()
            or not Path(stack_yaml_path_text).is_absolute()
            or SHA256.fullmatch(str(haskell_provenance["selectedProfileSha256"])) is None
            or Path(selected_profile_text).is_symlink()
            or not Path(selected_profile_text).is_file()
            or sha256_file(Path(selected_profile_text))
            != haskell_provenance["selectedProfileSha256"]
            or haskell_provenance["selectedProfileId"] != profile
            or haskell_provenance["effectiveCompilerFlagsSha256"]
            != effective_runtime_arguments_sha256
            or selected_options not in (["-O0", "-fasm"], ["-O2", "-fasm"])
            or _canonical_sha256(selected_options) != effective_runtime_arguments_sha256
            or SHA256.fullmatch(str(haskell_provenance["ghcupSha256"])) is None
            or SHA256.fullmatch(str(haskell_provenance["stackSha256"])) is None
            or SHA256.fullmatch(str(haskell_provenance["stackYamlSha256"])) is None
        ):
            raise GateError(f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}")
        ghcup_path = Path(ghcup_path_text)
        stack_path = Path(stack_path_text)
        stack_yaml_path = Path(stack_yaml_path_text)
        if (
            any(
                path.is_symlink() or not path.is_file()
                for path in (ghcup_path, stack_path, stack_yaml_path)
            )
            or sha256_file(ghcup_path) != haskell_provenance["ghcupSha256"]
            or sha256_file(stack_path) != haskell_provenance["stackSha256"]
            or sha256_file(stack_yaml_path) != haskell_provenance["stackYamlSha256"]
        ):
            raise GateError(f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}")
        selected_profile = _exact_object(
            strict_json_load(Path(selected_profile_text)),
            {"schemaVersion", "profileId", "ghcOptions", "optionsSha256"},
            error=f"NATIVE_EXECUTION_PROVENANCE_INVALID:{case_id}",
        )
        if (
            selected_profile["schemaVersion"] != "s1.4x-haskell-selected-profile-v1"
            or selected_profile["profileId"] != profile
            or selected_profile["optionsSha256"] != effective_runtime_arguments_sha256
            or selected_profile["ghcOptions"] != selected_options
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
        selector_case_ids = (
            selector.get("expectedCaseIds") if isinstance(selector, dict) else None
        )
        raw_path = str(block_directory / item["rawEvidencePath"])
        expected_arguments = [
            str(ghcup_path),
            "run",
            "--ghc",
            "9.10.3",
            "--stack",
            "3.11.1",
            "--",
            str(stack_path),
            "--stack-yaml",
            str(stack_yaml_path),
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


def _parse_jmh_raw(
    value: Any,
    *,
    case_id: str,
    native_case: Mapping[str, Any],
    native_statistics_case: Mapping[str, Any],
) -> None:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise GateError(f"JMH_RAW_DOCUMENT_INVALID:{case_id}")
    result = value[0]
    metric = result.get("primaryMetric")
    raw_data = metric.get("rawData") if isinstance(metric, dict) else None
    score = metric.get("score") if isinstance(metric, dict) else None
    score_confidence = metric.get("scoreConfidence") if isinstance(metric, dict) else None
    if (
        result.get("jmhVersion") != "1.37"
        or not isinstance(result.get("benchmark"), str)
        or not result["benchmark"]
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
    native_case: Mapping[str, Any],
    native_statistics_case: Mapping[str, Any],
) -> None:
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
        or native_case.get("samples") != len(measurements)
        or native_case.get("warmupIterations") != 0
        or native_case.get("measurementIterations") != len(measurements)
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
        or not _same_number(
            native_case.get("nativeValue"),
            regression_time["point"],
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
    _validate_native_statistics_case(
        native_statistics_case,
        case_id=case_id,
        expected={
            "nativeSampleCount": len(samples),
            "nativeP95": _nearest_rank_p95(samples),
            "confidenceLevel": regression_time["confidenceLevel"],
            "confidenceLow": regression_time["confidenceLow"],
            "confidenceHigh": regression_time["confidenceHigh"],
            "dispersionMetric": ("criterion-bootstrap-standard-deviation-seconds-per-invocation"),
            "dispersionValue": standard_deviation["point"],
            "nativeUnit": "s",
        },
        error=f"CRITERION_NATIVE_STATISTICS_MISMATCH:{case_id}",
    )


def _parse_criterion_family_raw(
    value: Any,
    *,
    native_cases: list[dict[str, Any]],
    native_statistics_cases: list[dict[str, Any]],
) -> None:
    expected_case_ids = [str(case["caseId"]) for case in native_cases]
    if (
        not isinstance(value, list)
        or len(value) != 3
        or value[0] != "criterion"
        or value[1] != "1.6.4.0"
        or not isinstance(value[2], list)
        or not 2 <= len(native_cases) <= 45
        or len(value[2]) != len(native_cases)
    ):
        raise GateError("CRITERION_RAW_DOCUMENT_INVALID")
    actual_names = [
        report.get("reportName") if isinstance(report, dict) else None for report in value[2]
    ]
    if actual_names != expected_case_ids:
        raise GateError("CRITERION_RAW_CASE_ORDER_INVALID")
    for report_number, (report, native_case, native_statistics_case) in enumerate(
        zip(
            value[2],
            native_cases,
            native_statistics_cases,
            strict=True,
        )
    ):
        _parse_criterion_report(
            report,
            report_number=report_number,
            case_id=expected_case_ids[report_number],
            native_case=native_case,
            native_statistics_case=native_statistics_case,
        )


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
    haskell_raw_path: Path | None = None
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
        if (
            raw_path.is_symlink()
            or not raw_path.is_file()
            or SHA256.fullmatch(str(item["rawEvidenceSha256"])) is None
            or sha256_file(raw_path) != item["rawEvidenceSha256"]
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
        _validate_execution_receipt(
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
        )
        if boundary_id == "scala":
            if native_statistics_case is None:
                raise GateError(f"NATIVE_STATISTICS_CASE_INVALID:{case_id}")
            _parse_jmh_raw(
                strict_json_load(raw_path),
                case_id=str(case_id),
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
            haskell_raw_path = raw_path
        actual_case_ids.append(str(case_id))
    if actual_case_ids != expected_case_ids:
        raise GateError("NATIVE_CONTRACT_CASE_ORDER_INVALID")
    if boundary_id == "haskell":
        if (
            len(haskell_raw_identities) != 1
            or len(haskell_receipt_identities) != 1
            or haskell_raw_path is None
            or native_statistics_cases is None
        ):
            raise GateError("CRITERION_FAMILY_EVIDENCE_NOT_SHARED")
        _parse_criterion_family_raw(
            strict_json_load(haskell_raw_path),
            native_cases=native_cases,
            native_statistics_cases=native_statistics_cases,
        )
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
