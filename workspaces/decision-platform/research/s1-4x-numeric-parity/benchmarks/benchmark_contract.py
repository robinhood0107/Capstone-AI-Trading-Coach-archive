"""S1.4X 벤치마크 계획의 기계적으로 검증 가능한 고정 계약을 정의한다."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

FAMILIES: dict[str, tuple[str, ...]] = {
    "path-transform": ("simple_returns", "log_returns", "cumulative_return"),
    "classical-path-risk": (
        "cagr",
        "realized_volatility",
        "annualized_volatility",
        "max_drawdown",
        "sharpe_ratio",
        "sortino_ratio",
        "historical_var",
        "historical_cvar",
        "historical_expected_shortfall",
    ),
    "intraday-realized": ("realized_variance", "realized_volatility_intraday"),
    "serial-sharpe": ("lo_adjusted_sharpe_ratio",),
    "probabilistic-scalar": (
        "probabilistic_sharpe_ratio",
        "deflated_sharpe_ratio",
    ),
    "coverage-batch": (
        "kupiec_unconditional_coverage_test",
        "christoffersen_independence_test",
        "christoffersen_conditional_coverage_test",
    ),
}

VECTOR_LENGTHS = (32, 252, 1000, 10000, 100000)
COVERAGE_LENGTHS = (252, 1000, 10000, 100000)
BOUNDARY_ORDER = (
    "python-numpy-s1-4",
    "python-numpy-s1-4r",
    "python-jax-eager-s1-4r",
    "python-jax-jit-s1-4r",
    "scala",
    "haskell",
)
PYTHON_BOUNDARIES = BOUNDARY_ORDER[:4]
ROTATIONS = (
    {
        "schedulingGroups": ["PythonBaselines", "Scala", "Haskell"],
        "pythonBoundaries": list(PYTHON_BOUNDARIES),
    },
    {
        "schedulingGroups": ["Scala", "Haskell", "PythonBaselines"],
        "pythonBoundaries": [
            "python-numpy-s1-4r",
            "python-jax-eager-s1-4r",
            "python-jax-jit-s1-4r",
            "python-numpy-s1-4",
        ],
    },
    {
        "schedulingGroups": ["Haskell", "PythonBaselines", "Scala"],
        "pythonBoundaries": [
            "python-jax-eager-s1-4r",
            "python-jax-jit-s1-4r",
            "python-numpy-s1-4",
            "python-numpy-s1-4r",
        ],
    },
)
QUALIFICATION_CASE_IDS = (
    "path-transform/log_returns/n100000/b1",
    "classical-path-risk/historical_expected_shortfall/n100000/b1",
    "intraday-realized/realized_variance/n100000/b1",
    "serial-sharpe/lo_adjusted_sharpe_ratio/n100000/q5/b1",
    "probabilistic-scalar/probabilistic_sharpe_ratio/b16384",
    "coverage-batch/kupiec_pof/n100000/b32",
    "coverage-batch/christoffersen_conditional_coverage/n100000/b32",
)
UNIT_TO_NS = {"ns": 1.0, "us": 1_000.0, "ms": 1_000_000.0, "s": 1_000_000_000.0}


class ContractError(ValueError):
    """동결 계약 위반을 호출자에게 안정적인 한 종류의 오류로 전달한다."""


def sha256_file(path: Path) -> str:
    """파일 바이트의 SHA-256을 반환해 계획과 실행 산출물의 정체성을 묶는다."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strict_json_load(path: Path) -> Any:
    """중복 키와 NaN/Infinity를 거부하며 계약 JSON을 읽는다."""

    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContractError(f"DUPLICATE_JSON_KEY:{key}")
            result[key] = value
        return result

    def reject_nonfinite(token: str) -> None:
        raise ContractError(f"NONFINITE_JSON_NUMBER:{token}")

    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(
                stream,
                object_pairs_hook=reject_duplicate,
                parse_constant=reject_nonfinite,
            )
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"INVALID_JSON:{path}:{exc}") from exc


def _function_arguments(function_id: str) -> dict[str, Any]:
    if function_id in {"cagr", "annualized_volatility"}:
        return {"periods_per_year": 252}
    if function_id == "sharpe_ratio":
        return {"periods_per_year": 252, "risk_free_rate": 0.0}
    if function_id == "sortino_ratio":
        return {"periods_per_year": 252, "target_return": 0.0}
    if function_id in {
        "historical_var",
        "historical_cvar",
        "historical_expected_shortfall",
    }:
        return {"confidence": 0.95}
    if function_id == "lo_adjusted_sharpe_ratio":
        return {"aggregation_periods": 5, "risk_free_rate": 0.0}
    if function_id == "probabilistic_sharpe_ratio":
        return {
            "benchmark_sharpe": 0.0,
            "kurtosis": 3.0,
            "sample_size": 252,
            "skewness": 0.0,
        }
    if function_id == "deflated_sharpe_ratio":
        return {
            "kurtosis": 3.0,
            "sample_size": 252,
            "sharpe_estimate_variance": 1.0,
            "skewness": 0.0,
            "trial_count_mix": [
                {"evaluation_count": 5462, "trial_count": 2},
                {"evaluation_count": 5461, "trial_count": 10**20},
                {"evaluation_count": 5461, "trial_count": 10**308},
            ],
            "trial_count_provenance": "externally_estimated_effective_count",
        }
    if function_id == "kupiec_unconditional_coverage_test":
        return {"confidence": 0.95, "significance": 0.05}
    if function_id == "christoffersen_independence_test":
        return {"significance": 0.05}
    if function_id == "christoffersen_conditional_coverage_test":
        return {"confidence": 0.95, "significance": 0.05}
    return {}


def expected_cases() -> list[dict[str, Any]]:
    """동결된 6개 family의 정확한 89개 case를 순서까지 포함해 생성한다."""

    cases: list[dict[str, Any]] = []
    for family_id in ("path-transform", "classical-path-risk"):
        for function_id in FAMILIES[family_id]:
            for vector_length in VECTOR_LENGTHS:
                fixture_kind = (
                    "prices"
                    if function_id in {"simple_returns", "log_returns", "cagr", "max_drawdown"}
                    else "returns"
                )
                cases.append(
                    {
                        "caseId": f"{family_id}/{function_id}/n{vector_length}/b1",
                        "familyId": family_id,
                        "functionId": function_id,
                        "fixtureId": f"large-{fixture_kind}-n100000-prefix-n{vector_length}",
                        "logicalOperationsPerInvocation": 1,
                        "vectorLength": vector_length,
                        "batchSize": 1,
                        "functionArguments": _function_arguments(function_id),
                    }
                )
    for function_id in FAMILIES["intraday-realized"]:
        for vector_length in VECTOR_LENGTHS:
            cases.append(
                {
                    "caseId": f"intraday-realized/{function_id}/n{vector_length}/b1",
                    "familyId": "intraday-realized",
                    "functionId": function_id,
                    "fixtureId": f"large-returns-n100000-prefix-n{vector_length}",
                    "logicalOperationsPerInvocation": 1,
                    "vectorLength": vector_length,
                    "batchSize": 1,
                    "functionArguments": {},
                }
            )
    for vector_length in VECTOR_LENGTHS:
        cases.append(
            {
                "caseId": (
                    "serial-sharpe/lo_adjusted_sharpe_ratio/"
                    f"n{vector_length}/q5/b1"
                ),
                "familyId": "serial-sharpe",
                "functionId": "lo_adjusted_sharpe_ratio",
                "fixtureId": f"large-returns-n100000-prefix-n{vector_length}",
                "logicalOperationsPerInvocation": 1,
                "vectorLength": vector_length,
                "batchSize": 1,
                "functionArguments": _function_arguments("lo_adjusted_sharpe_ratio"),
            }
        )
    for function_id in FAMILIES["probabilistic-scalar"]:
        cases.append(
            {
                "caseId": f"probabilistic-scalar/{function_id}/b16384",
                "familyId": "probabilistic-scalar",
                "functionId": function_id,
                "fixtureId": f"precomputed-{function_id}-b16384",
                "logicalOperationsPerInvocation": 16384,
                "vectorLength": 16384,
                "batchSize": 16384,
                "functionArguments": _function_arguments(function_id),
            }
        )
    coverage_aliases = {
        "kupiec_unconditional_coverage_test": "kupiec_pof",
        "christoffersen_independence_test": "christoffersen_independence",
        "christoffersen_conditional_coverage_test": (
            "christoffersen_conditional_coverage"
        ),
    }
    for function_id in FAMILIES["coverage-batch"]:
        for vector_length in COVERAGE_LENGTHS:
            cases.append(
                {
                    "caseId": (
                        f"coverage-batch/{coverage_aliases[function_id]}/"
                        f"n{vector_length}/b32"
                    ),
                    "familyId": "coverage-batch",
                    "functionId": function_id,
                    "fixtureId": (
                        "large-coverage-pair-n3200000/"
                        f"prefix-n{vector_length}-sequences-b32"
                    ),
                    "logicalOperationsPerInvocation": 32,
                    "vectorLength": vector_length,
                    "batchSize": 32,
                    "functionArguments": _function_arguments(function_id),
                }
            )
    if len(cases) != 89:
        raise AssertionError(f"internal case construction error: {len(cases)}")
    return cases


def _case_ids(
    cases: list[dict[str, Any]],
    *,
    families: set[str] | None = None,
    functions: set[str] | None = None,
) -> list[str]:
    return [
        case["caseId"]
        for case in cases
        if (families is None or case["familyId"] in families)
        and (functions is None or case["functionId"] in functions)
    ]


def expected_boundaries(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """언어/런타임별 실행 경계를 55/34/34/34/89/89로 고정한다."""

    production_functions = set(FAMILIES["path-transform"]) | (
        set(FAMILIES["classical-path-risk"]) - {"historical_expected_shortfall"}
    )
    research_functions = {
        "historical_expected_shortfall",
        *FAMILIES["intraday-realized"],
        *FAMILIES["serial-sharpe"],
        *FAMILIES["probabilistic-scalar"],
        *FAMILIES["coverage-batch"],
    }
    all_functions = {function_id for values in FAMILIES.values() for function_id in values}
    production_cases = _case_ids(cases, functions=production_functions)
    research_cases = _case_ids(cases, functions=research_functions)
    all_cases = _case_ids(cases)
    boundaries: list[dict[str, Any]] = []
    for boundary_id, scheduling_group, function_ids, case_ids in (
        ("python-numpy-s1-4", "PythonBaselines", production_functions, production_cases),
        ("python-numpy-s1-4r", "PythonBaselines", research_functions, research_cases),
        ("python-jax-eager-s1-4r", "PythonBaselines", research_functions, research_cases),
        ("python-jax-jit-s1-4r", "PythonBaselines", research_functions, research_cases),
        ("scala", "Scala", all_functions, all_cases),
        ("haskell", "Haskell", all_functions, all_cases),
    ):
        boundaries.append(
            {
                "boundaryId": boundary_id,
                "schedulingGroup": scheduling_group,
                "functionIds": sorted(function_ids),
                "expectedCaseIds": case_ids,
            }
        )
    return boundaries


def expected_selectors(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """한 repetition에서 정확히 29개가 되는 family selector를 생성한다."""

    selectors: list[dict[str, Any]] = []

    def add(
        boundary_id: str,
        family_id: str,
        case_ids: list[str],
        suffix: str = "",
    ) -> None:
        selector_id = f"{boundary_id}/{family_id}{suffix}"
        selectors.append(
            {
                "boundaryId": boundary_id,
                "familyId": family_id,
                "selectorId": selector_id,
                "expectedCaseIds": case_ids,
                "jmhIncludeRegex": (
                    f"^s1_4x\\.benchmarks\\.{family_id.replace('-', '_')}\\..*$"
                    if boundary_id == "scala"
                    else None
                ),
                "criterionMatchMode": "prefix" if boundary_id == "haskell" else "none",
                "criterionPrefix": f"{family_id}/" if boundary_id == "haskell" else None,
                "pythonFamilyId": family_id if boundary_id in PYTHON_BOUNDARIES else None,
            }
        )

    for boundary_id in ("scala", "haskell"):
        for family_id in FAMILIES:
            add(boundary_id, family_id, _case_ids(cases, families={family_id}))
    add(
        "python-numpy-s1-4",
        "path-transform",
        _case_ids(cases, families={"path-transform"}),
    )
    add(
        "python-numpy-s1-4",
        "classical-path-risk",
        _case_ids(
            cases,
            functions=set(FAMILIES["classical-path-risk"])
            - {"historical_expected_shortfall"},
        ),
        "-s1-4",
    )
    research_family_cases = (
        (
            "classical-path-risk",
            _case_ids(cases, functions={"historical_expected_shortfall"}),
            "-expected-shortfall",
        ),
        (
            "intraday-realized",
            _case_ids(cases, families={"intraday-realized"}),
            "",
        ),
        ("serial-sharpe", _case_ids(cases, families={"serial-sharpe"}), ""),
        (
            "probabilistic-scalar",
            _case_ids(cases, families={"probabilistic-scalar"}),
            "",
        ),
        ("coverage-batch", _case_ids(cases, families={"coverage-batch"}), ""),
    )
    for boundary_id in PYTHON_BOUNDARIES[1:]:
        for family_id, case_ids, suffix in research_family_cases:
            add(boundary_id, family_id, case_ids, suffix)
    if len(selectors) != 29:
        raise AssertionError(f"internal selector construction error: {len(selectors)}")
    return selectors


def expected_timeout_map(selectors: list[dict[str, Any]]) -> dict[str, int]:
    scala = {
        "path-transform": 900,
        "classical-path-risk": 2700,
        "intraday-realized": 720,
        "serial-sharpe": 420,
        "probabilistic-scalar": 240,
        "coverage-batch": 780,
    }
    haskell = {
        "path-transform": 300,
        "classical-path-risk": 600,
        "intraday-realized": 300,
        "serial-sharpe": 240,
        "probabilistic-scalar": 180,
        "coverage-batch": 300,
    }
    python = {
        "path-transform": 180,
        "classical-path-risk": 300,
        "classical-path-risk-expected-shortfall": 120,
        "intraday-realized": 180,
        "serial-sharpe": 120,
        "probabilistic-scalar": 120,
        "coverage-batch": 180,
    }
    result: dict[str, int] = {}
    for selector in selectors:
        boundary_id = selector["boundaryId"]
        selector_id = selector["selectorId"]
        family_id = selector["familyId"]
        if boundary_id == "scala":
            result[selector_id] = scala[family_id]
        elif boundary_id == "haskell":
            result[selector_id] = haskell[family_id]
        else:
            python_key = (
                "classical-path-risk-expected-shortfall"
                if selector_id.endswith("-expected-shortfall")
                else family_id
            )
            result[selector_id] = python[python_key]
    return result


def build_plan(
    *,
    reference_lock_sha256: str,
    canonical_inputs_sha256: str,
    canonical_results_sha256: str,
    scala_source_policy_sha256: str,
) -> dict[str, Any]:
    """외부 파일 digest만 주입해 나머지 계획을 전부 결정론적으로 만든다."""

    cases = expected_cases()
    boundaries = expected_boundaries(cases)
    selectors = expected_selectors(cases)
    return {
        "schemaVersion": "s1.4x-benchmark-plan-v1",
        "planId": "s1.4x-full-same-host-v1",
        "fixtureFreezeIdentity": {
            "referenceLockSha256": reference_lock_sha256,
            "canonicalInputsSha256": canonical_inputs_sha256,
            "canonicalResultsSha256": canonical_results_sha256,
        },
        "functionFamilyMap": {
            function_id: family_id
            for family_id, function_ids in FAMILIES.items()
            for function_id in function_ids
        },
        "executionBoundaries": boundaries,
        "familySelectors": selectors,
        "cases": cases,
        "execution": {
            "candidateOrderBlocks": list(ROTATIONS),
            "outerRepetitions": 3,
            "cpuSet": [0],
            "threadCount": 1,
            "nativeBenchmarkMode": {
                "python-numpy-s1-4": "precomputed-batch",
                "python-numpy-s1-4r": "precomputed-batch",
                "python-jax-eager-s1-4r": "precomputed-batch",
                "python-jax-jit-s1-4r": "precomputed-batch",
                "scala": "AverageTime",
                "haskell": "Criterion",
            },
            "nativeTimeUnit": {
                "python-numpy-s1-4": "ns",
                "python-numpy-s1-4r": "ns",
                "python-jax-eager-s1-4r": "ns",
                "python-jax-jit-s1-4r": "ns",
                "scala": "ns",
                "haskell": "s",
            },
            "normalizationFormula": (
                "nativeValueInNanoseconds/logicalOperationsPerInvocation"
            ),
            "warmupIterations": {"scala": 5},
            "warmupTimeSeconds": {"scala": 1},
            "measurementIterations": {"scala": 10},
            "measurementTimeSeconds": {"scala": 1},
            "forks": {"scala": 3},
            "criterionTimeLimitSeconds": 5,
            "familyBlockTimeoutSeconds": expected_timeout_map(selectors),
            "totalRunTimeoutSeconds": 32400,
            "blockOutputPathTemplate": (
                "<run>/<repetition>/<execution-boundary>/<family>/native.json"
            ),
            "timerBoundary": (
                "fixture decode, validation, allocation, JAX lowering/compile, and result "
                "serialization are outside the timed region"
            ),
            "setupBoundary": (
                "each selector loads frozen inputs once; JIT compilation and one forced "
                "evaluation complete before measurement"
            ),
            "forceEvaluationRule": (
                "every timed invocation consumes scalar outputs or fully materializes arrays "
                "before the timer stops"
            ),
        },
        "scalaProfileQualification": {
            "qualificationCaseIds": list(QUALIFICATION_CASE_IDS),
            "qualificationCaseOrder": list(QUALIFICATION_CASE_IDS),
            "profileOrderBlocks": [["A", "B", "C"], ["B", "C", "A"], ["C", "A", "B"]],
            "hostValidityBeforeEachProfileBlock": True,
            "mode": "AverageTime",
            "timeUnit": "ns",
            "threadCount": 1,
            "forks": 3,
            "warmupIterations": 5,
            "warmupTime": "500ms",
            "measurementIterations": 8,
            "measurementTime": "500ms",
            "outerQualificationRepetitions": 3,
            "perCaseMaxRegressionRatio": 1.05,
            "aggregateMaxRatio": 0.97,
            "minimumImprovingOuterRepetitions": 2,
            "cOverBMinimumImprovement": 0.01,
            "tieBreakOrder": ["B", "C", "A"],
            "fallbackProfile": "A",
        },
        "haskellProfileQualification": {
            "qualificationCaseIds": list(QUALIFICATION_CASE_IDS),
            "qualificationCaseOrder": list(QUALIFICATION_CASE_IDS),
            "profileOrderBlocks": [
                ["baseline-o0-fasm", "optimized-o2-fasm"],
                ["optimized-o2-fasm", "baseline-o0-fasm"],
                ["optimized-o2-fasm", "baseline-o0-fasm"],
                ["baseline-o0-fasm", "optimized-o2-fasm"],
            ],
            "hostValidityBeforeEachProfileBlock": True,
            "criterionTimeLimitSeconds": 3,
            "outerQualificationRepetitions": 4,
            "ratioPairing": "same-order-block-and-case",
            "perCaseCollapse": "max-of-four-paired-ratios",
            "aggregateFormula": "geometric-mean-of-all-28-paired-ratios",
            "improvingBlockFormula": "geometric-mean-of-seven-case-ratios",
            "perCaseMaxRegressionRatio": 1.05,
            "aggregateMaxRatio": 0.97,
            "minimumImprovingOuterRepetitions": 3,
            "optimizedProfile": "optimized-o2-fasm",
            "fallbackProfile": "baseline-o0-fasm",
        },
        "scalaJmhPolicy": {
            "sourceAnnotationPolicySha256": scala_source_policy_sha256,
            "allowedAnnotationsAndValues": {
                "org.openjdk.jmh.annotations.Benchmark": ["present-with-no-arguments"],
                "org.openjdk.jmh.annotations.State": ["Scope.Benchmark"],
                "org.openjdk.jmh.annotations.Setup": ["Level.Trial"],
                "org.openjdk.jmh.annotations.OperationsPerInvocation": (
                    "exact case logicalOperationsPerInvocation"
                ),
                "org.openjdk.jmh.annotations.Param": "exact frozen case IDs only",
            },
            "forbiddenPlanOverrideAnnotations": [
                "org.openjdk.jmh.annotations.Fork",
                "org.openjdk.jmh.annotations.Threads",
                "org.openjdk.jmh.annotations.Warmup",
                "org.openjdk.jmh.annotations.Measurement",
                "org.openjdk.jmh.annotations.BenchmarkMode",
                "org.openjdk.jmh.annotations.OutputTimeUnit",
                "org.openjdk.jmh.annotations.CompilerControl",
                "org.openjdk.jmh.annotations.Timeout",
            ],
            "allowedCliJvmArgs": [],
            "effectiveJvmArgsRequired": True,
            "effectiveJvmArgsPolicyId": "capability-smoke-effective-jvm-args-v1",
            "capabilitySmokeEffectiveJvmArgsSha256Required": True,
        },
        "allocationPolicy": {
            "capBytes": 536870912,
            "formulaVersion": "s1.4x-allocation-formula-v1",
            "maximalCommonChunkSearch": "deterministic_integer_binary_search",
            "fixedShapeLastChunk": True,
            "lastChunkMaskRequired": True,
            "forbidCartesianMaterialization": True,
        },
        "environmentValidity": {
            "maxNormalizedLoad1": 0.1,
            "loadSampleCount": 3,
            "loadSampleIntervalSeconds": 30,
            "maxQuietWaitSeconds": 600,
            "minAvailableMemoryGiB": 4,
            "runningContainerCount": 4,
            "externalProcessCpuPercentThreshold": 5,
            "temperatureMetadata": {
                "recordWhenAvailable": True,
                "wslUnavailableAllowedButRecorded": True,
            },
            "governor": {
                "recordWhenAvailable": True,
                "wslUnavailableAllowedButRecorded": True,
            },
            "noOtherBenchmarkProcess": True,
        },
        "failurePolicy": {
            "anyMissingDuplicateOrUnexpectedCase": "FAIL",
            "wrongNativeModeOrUnit": "FAIL",
            "nonfiniteOrNonpositivePassTiming": "FAIL",
            "blockTimeout": "PERFORMANCE_DEADLINE_EXCEEDED",
            "timedOutCandidateScore": 0,
            "continueOtherCandidateAfterTimeout": True,
            "partialBlockPublication": "FORBIDDEN",
            "declaredBlockCeilingsSeconds": 30960,
            "reserveSeconds": 1440,
        },
    }


def validate_plan_semantics(plan: Any) -> None:
    """스키마만으로 표현하기 어려운 exact ordering/count/digest 규칙을 검증한다."""

    if not isinstance(plan, dict):
        raise ContractError("PLAN_NOT_OBJECT")
    identity = plan.get("fixtureFreezeIdentity")
    scala_policy = plan.get("scalaJmhPolicy")
    if not isinstance(identity, dict) or not isinstance(scala_policy, dict):
        raise ContractError("PLAN_IDENTITY_MISSING")
    expected = build_plan(
        reference_lock_sha256=str(identity.get("referenceLockSha256", "")),
        canonical_inputs_sha256=str(identity.get("canonicalInputsSha256", "")),
        canonical_results_sha256=str(identity.get("canonicalResultsSha256", "")),
        scala_source_policy_sha256=str(scala_policy.get("sourceAnnotationPolicySha256", "")),
    )
    if plan != expected:
        raise ContractError("PLAN_NOT_EXACT_FROZEN_CONTRACT")
    if [len(item["expectedCaseIds"]) for item in plan["executionBoundaries"]] != [
        55,
        34,
        34,
        34,
        89,
        89,
    ]:
        raise ContractError("BOUNDARY_CASE_COUNT_MISMATCH")
    timeout_total = sum(plan["execution"]["familyBlockTimeoutSeconds"].values()) * 3
    if timeout_total != 30960:
        raise ContractError(f"BLOCK_TIMEOUT_TOTAL_MISMATCH:{timeout_total}")
    if timeout_total + 1440 != plan["execution"]["totalRunTimeoutSeconds"]:
        raise ContractError("TOTAL_TIMEOUT_RESERVE_MISMATCH")
    execution = plan["execution"]
    if execution["measurementIterations"]["scala"] * execution["forks"]["scala"] != 30:
        raise ContractError("SCALA_MEASUREMENT_SAMPLE_COUNT_MISMATCH")


def validate_block_result_semantics(
    report: Any,
    plan: dict[str, Any],
    *,
    expected_boundary_id: str | None = None,
    expected_selector_id: str | None = None,
) -> None:
    """한 block 보고서가 selector의 case/mode/unit/정규화 계약을 정확히 지키는지 본다."""

    if not isinstance(report, dict):
        raise ContractError("BLOCK_RESULT_NOT_OBJECT")
    block = report.get("block")
    if not isinstance(block, dict):
        raise ContractError("BLOCK_METADATA_MISSING")
    boundary_id = block.get("boundaryId")
    selector_id = block.get("selectorId")
    if expected_boundary_id is not None and boundary_id != expected_boundary_id:
        raise ContractError("WRONG_BOUNDARY")
    if expected_selector_id is not None and selector_id != expected_selector_id:
        raise ContractError("WRONG_SELECTOR")
    selector_by_id = {item["selectorId"]: item for item in plan["familySelectors"]}
    selector = selector_by_id.get(selector_id)
    if selector is None or selector["boundaryId"] != boundary_id:
        raise ContractError("UNKNOWN_BOUNDARY_SELECTOR")
    subject = report.get("subject")
    if not isinstance(subject, dict) or subject.get("candidate") != boundary_id:
        raise ContractError("WRONG_BENCHMARK_SUBJECT")
    environment = report.get("environment")
    if (
        not isinstance(environment, dict)
        or environment.get("fixtureFreezeIdentity") != plan["fixtureFreezeIdentity"]
    ):
        raise ContractError("WRONG_FIXTURE_FREEZE_IDENTITY")
    rotation = report.get("rotation")
    if not isinstance(rotation, dict):
        raise ContractError("ROTATION_METADATA_MISSING")
    round_number = rotation.get("outerRepetition")
    if not isinstance(round_number, int) or not 1 <= round_number <= 3:
        raise ContractError("WRONG_ROTATION")
    expected_rotation = plan["execution"]["candidateOrderBlocks"][round_number - 1]
    if rotation.get("rotationId") != f"R{round_number}":
        raise ContractError("WRONG_ROTATION_ID")
    if rotation.get("candidateOrder") != expected_rotation["schedulingGroups"]:
        raise ContractError("WRONG_CANDIDATE_ORDER")
    if rotation.get("pythonBoundaryOrder") != expected_rotation["pythonBoundaries"]:
        raise ContractError("WRONG_PYTHON_BOUNDARY_ORDER")
    expected_group = next(
        item["schedulingGroup"]
        for item in plan["executionBoundaries"]
        if item["boundaryId"] == boundary_id
    )
    if rotation.get("schedulingGroup") != expected_group:
        raise ContractError("WRONG_SCHEDULING_GROUP")
    if block.get("familyId") != selector["familyId"]:
        raise ContractError("WRONG_FAMILY")
    expected_mode = plan["execution"]["nativeBenchmarkMode"][boundary_id]
    if block.get("nativeBenchmarkMode") != expected_mode:
        raise ContractError("WRONG_NATIVE_MODE")
    if block.get("affinityCpuSet") != [0] or block.get("actualAffinityCpuSet") != [0]:
        raise ContractError("WRONG_CPU_AFFINITY")
    if block.get("threadCount") != 1:
        raise ContractError("WRONG_THREAD_COUNT")
    expected_native_report_path = (
        f"{report.get('runId')}/{rotation.get('rotationId')}/{boundary_id}/"
        f"{selector['familyId']}/native.json"
    )
    if block.get("nativeReportPath") != expected_native_report_path:
        raise ContractError("WRONG_NATIVE_REPORT_PATH")
    expected_unit = plan["execution"]["nativeTimeUnit"][boundary_id]
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise ContractError("CASES_NOT_ARRAY")
    case_by_id = {case["caseId"]: case for case in plan["cases"]}
    seen: set[str] = set()
    actual_ids: list[str] = []
    for measured in cases:
        if not isinstance(measured, dict):
            raise ContractError("CASE_RESULT_NOT_OBJECT")
        case_id = measured.get("caseId")
        if not isinstance(case_id, str):
            raise ContractError("CASE_ID_MISSING")
        if case_id in seen:
            raise ContractError(f"DUPLICATE_CASE:{case_id}")
        seen.add(case_id)
        actual_ids.append(case_id)
        frozen = case_by_id.get(case_id)
        if frozen is None:
            raise ContractError(f"UNEXPECTED_CASE:{case_id}")
        if measured.get("functionId") != frozen["functionId"]:
            raise ContractError(f"WRONG_FUNCTION:{case_id}")
        if measured.get("fixtureId") != frozen["fixtureId"]:
            raise ContractError(f"WRONG_FIXTURE:{case_id}")
        if measured.get("nativeUnit") != expected_unit:
            raise ContractError(f"WRONG_NATIVE_UNIT:{case_id}")
        logical_operations = measured.get("logicalOperationsPerInvocation")
        if logical_operations != frozen["logicalOperationsPerInvocation"]:
            raise ContractError(f"WRONG_LOGICAL_OPERATIONS:{case_id}")
        samples = measured.get("samples")
        if not isinstance(samples, int) or isinstance(samples, bool) or samples < 2:
            raise ContractError(f"WRONG_SAMPLE_COUNT:{case_id}")
        warmup_iterations = measured.get("warmupIterations")
        if (
            not isinstance(warmup_iterations, int)
            or isinstance(warmup_iterations, bool)
            or warmup_iterations < 0
        ):
            raise ContractError(f"WRONG_WARMUP_ITERATIONS:{case_id}")
        measurement_iterations = measured.get("measurementIterations")
        if (
            not isinstance(measurement_iterations, int)
            or isinstance(measurement_iterations, bool)
            or measurement_iterations < 2
        ):
            raise ContractError(f"WRONG_MEASUREMENT_ITERATIONS:{case_id}")
        if boundary_id == "scala":
            expected_warmup = plan["execution"]["warmupIterations"]["scala"]
            expected_measurement = plan["execution"]["measurementIterations"]["scala"]
            expected_forks = plan["execution"]["forks"]["scala"]
            if warmup_iterations != expected_warmup:
                raise ContractError(f"WRONG_WARMUP_ITERATIONS:{case_id}")
            if measurement_iterations != expected_measurement:
                raise ContractError(f"WRONG_MEASUREMENT_ITERATIONS:{case_id}")
            # JMH rawData의 행 수는 fork 수이고 각 행의 원소 수는 measurement iteration 수다.
            if samples != expected_forks * expected_measurement:
                raise ContractError(f"WRONG_SAMPLE_COUNT_OR_FORKS:{case_id}")
        for key in ("nativeValue", "normalizedNsPerLogicalOperation"):
            value = measured.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ContractError(f"TIMING_NOT_NUMBER:{case_id}:{key}")
            if not math.isfinite(float(value)):
                raise ContractError(f"NONFINITE_TIMING:{case_id}:{key}")
        status = measured.get("status")
        if status == "PASS":
            native_value = float(measured["nativeValue"])
            normalized = float(measured["normalizedNsPerLogicalOperation"])
            if native_value <= 0.0 or normalized <= 0.0:
                raise ContractError(f"NONPOSITIVE_PASS_TIMING:{case_id}")
            computed = native_value * UNIT_TO_NS[expected_unit] / logical_operations
            if not math.isclose(computed, normalized, rel_tol=1e-12, abs_tol=1e-9):
                raise ContractError(f"WRONG_NORMALIZATION:{case_id}")
    expected_ids = selector["expectedCaseIds"]
    if actual_ids != expected_ids:
        missing = sorted(set(expected_ids) - set(actual_ids))
        unexpected = sorted(set(actual_ids) - set(expected_ids))
        raise ContractError(f"CASE_SET_OR_ORDER_MISMATCH:missing={missing}:unexpected={unexpected}")
    block_status = block.get("status")
    if block_status == "PASS" and report.get("failure") is not None:
        raise ContractError("PASS_BLOCK_HAS_FAILURE")
    if block_status != "PASS" and not isinstance(report.get("failure"), dict):
        raise ContractError("FAILED_BLOCK_MISSING_FAILURE")
