#!/usr/bin/env python3
"""네 Python boundary의 모든 selector/function을 timing 없이 강제 평가한다."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from benchmark_input_ledger import generated_fixture_evidence
from gate import GateError, exclusive_json_write
from python_benchmark_block import (
    PYTHON_BOUNDARIES,
    _consume,
    _generated_array,
    _operation,
    _sha256_file,
    _sha256_json,
    _utc_now,
)

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))

from benchmark_contract import ContractError  # type: ignore[import-not-found]  # noqa: E402
from validate_benchmark_report import validate_plan  # type: ignore[import-not-found]  # noqa: E402


@dataclass(frozen=True)
class SmokeSelection:
    """한 Python execution boundary에서 timing 전 강제할 최소 완전 함수 집합이다."""

    selectors: tuple[dict[str, Any], ...]
    cases: tuple[dict[str, Any], ...]
    function_ids: tuple[str, ...]
    executed_case_ids: tuple[str, ...]


def _select_smoke_cases(plan: Any, boundary: str) -> SmokeSelection:
    """모든 nonempty selector를 덮는 함수별 최소 case를 frozen 순서로 선택한다."""

    if not isinstance(plan, dict) or boundary not in PYTHON_BOUNDARIES:
        raise GateError("SMOKE_PLAN_OR_BOUNDARY_INVALID")
    selectors = tuple(
        selector
        for selector in plan.get("familySelectors", [])
        if isinstance(selector, dict) and selector.get("boundaryId") == boundary
    )
    boundary_entry = next(
        (
            entry
            for entry in plan.get("executionBoundaries", [])
            if isinstance(entry, dict) and entry.get("boundaryId") == boundary
        ),
        None,
    )
    if (
        not selectors
        or boundary_entry is None
        or any(
            not isinstance(selector.get("expectedCaseIds"), list)
            or not selector["expectedCaseIds"]
            for selector in selectors
        )
    ):
        raise GateError(f"SMOKE_SELECTOR_CLOSURE_INVALID:{boundary}")
    selector_case_ids = [
        case_id
        for selector in selectors
        for case_id in selector["expectedCaseIds"]
    ]
    expected_case_ids = boundary_entry.get("expectedCaseIds")
    if (
        selector_case_ids != expected_case_ids
        or len(selector_case_ids) != len(set(selector_case_ids))
    ):
        raise GateError(f"SMOKE_SELECTOR_CLOSURE_INVALID:{boundary}")
    case_by_id = {
        case["caseId"]: case
        for case in plan.get("cases", [])
        if isinstance(case, dict) and isinstance(case.get("caseId"), str)
    }
    if any(case_id not in case_by_id for case_id in selector_case_ids):
        raise GateError(f"SMOKE_CASE_CLOSURE_INVALID:{boundary}")
    cases_by_function: dict[str, list[dict[str, Any]]] = {}
    function_order: list[str] = []
    for case_id in selector_case_ids:
        case = case_by_id[case_id]
        function_id = case.get("functionId")
        if not isinstance(function_id, str) or not function_id:
            raise GateError(f"SMOKE_FUNCTION_ID_INVALID:{case_id}")
        if function_id not in cases_by_function:
            cases_by_function[function_id] = []
            function_order.append(function_id)
        cases_by_function[function_id].append(case)
    selected = tuple(
        min(
            cases_by_function[function_id],
            key=lambda case: (
                int(case["vectorLength"]),
                int(case["batchSize"]),
                str(case["caseId"]).encode("utf-8"),
            ),
        )
        for function_id in function_order
    )
    executed_case_ids = tuple(str(case["caseId"]) for case in selected)
    if any(
        not set(selector["expectedCaseIds"]).intersection(executed_case_ids)
        for selector in selectors
    ):
        raise GateError(f"SMOKE_SELECTOR_NOT_EXECUTED:{boundary}")
    return SmokeSelection(
        selectors=selectors,
        cases=selected,
        function_ids=tuple(function_order),
        executed_case_ids=executed_case_ids,
    )


def _validate_dsr_case(case: Any) -> dict[str, Any]:
    """DSR smoke가 2, 10^20, 10^308의 exact 16384 batch를 축소하지 못하게 한다."""

    expected_mix = [
        {"evaluation_count": 5462, "trial_count": 2},
        {"evaluation_count": 5461, "trial_count": 10**20},
        {"evaluation_count": 5461, "trial_count": 10**308},
    ]
    if (
        not isinstance(case, dict)
        or case.get("caseId")
        != "probabilistic-scalar/deflated_sharpe_ratio/b16384"
        or case.get("functionId") != "deflated_sharpe_ratio"
        or case.get("vectorLength") != 16384
        or case.get("batchSize") != 16384
        or case.get("logicalOperationsPerInvocation") != 16384
        or case.get("functionArguments", {}).get("trial_count_mix") != expected_mix
        or sum(item["evaluation_count"] for item in expected_mix) != 16384
    ):
        raise GateError("DSR_SMOKE_CASE_INVALID")
    return {
        "caseId": case["caseId"],
        "batchSize": 16384,
        "trialCountMix": [
            {
                "trialCount": item["trial_count"],
                "evaluationCount": item["evaluation_count"],
            }
            for item in expected_mix
        ],
        "status": "PASS",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--boundary", choices=sorted(PYTHON_BOUNDARIES), required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repo = arguments.repo_root.resolve(strict=True)
        plan_path = arguments.plan.resolve(strict=True)
        plan = validate_plan(plan_path)
        selection = _select_smoke_cases(plan, arguments.boundary)
        large = (
            repo
            / "workspaces/decision-platform/research/s1-4x-numeric-parity/"
            "contract/fixtures/large"
        )
        empty = np.empty(0, dtype=np.float64)
        if arguments.boundary == "python-numpy-s1-4":
            fixture_specs = [
                ("large-prices-n100000.f64le", 100000),
                ("large-returns-n100000.f64le", 100000),
            ]
            prices = _generated_array(large, "large-prices-n100000.f64le", 100000)
            returns = _generated_array(large, "large-returns-n100000.f64le", 100000)
            realized_losses = empty
            forecast_vars = empty
        else:
            fixture_specs = [
                ("large-returns-n100000.f64le", 100000),
                ("large-coverage-realized-losses-n3200000.f64le", 3200000),
                ("large-coverage-forecast-var-n3200000.f64le", 3200000),
            ]
            prices = empty
            returns = _generated_array(large, "large-returns-n100000.f64le", 100000)
            realized_losses = _generated_array(
                large,
                "large-coverage-realized-losses-n3200000.f64le",
                3200000,
            )
            forecast_vars = _generated_array(
                large,
                "large-coverage-forecast-var-n3200000.f64le",
                3200000,
            )
        production = repo / "workspaces/decision-platform/python-services"
        research = repo / "workspaces/decision-platform/research/s1-4r-jax-risk"
        sys.path.insert(0, str(production))
        sys.path.insert(0, str(research / "src"))
        operations = [
            (
                case,
                _operation(
                    arguments.boundary,
                    case,
                    prices=prices,
                    returns=returns,
                    realized_losses=realized_losses,
                    forecast_vars=forecast_vars,
                ),
            )
            for case in selection.cases
        ]
        started = _utc_now()
        forced_results = []
        for case, operation in operations:
            consumed = _consume(operation())
            if not math.isfinite(consumed):
                raise GateError(f"SMOKE_RESULT_NON_FINITE:{case['caseId']}")
            forced_results.append({"caseId": case["caseId"], "consumed": consumed})
        finished = _utc_now()
        dsr_case = next(
            (
                case
                for case in selection.cases
                if case["functionId"] == "deflated_sharpe_ratio"
            ),
            None,
        )
        selector_evidence = [
            {
                "selectorId": selector["selectorId"],
                "familyId": selector["familyId"],
                "expectedCaseCount": len(selector["expectedCaseIds"]),
                "expectedCaseIdsSha256": _sha256_json(
                    selector["expectedCaseIds"]
                ),
                "smokeCaseIds": [
                    case_id
                    for case_id in selection.executed_case_ids
                    if case_id in selector["expectedCaseIds"]
                ],
                "status": "PASS",
            }
            for selector in selection.selectors
        ]
        exclusive_json_write(
            arguments.output,
            {
                "schemaVersion": "s1.4x-python-all-selector-smoke-v1",
                "planId": plan["planId"],
                "planSha256": _sha256_file(plan_path),
                "boundaryId": arguments.boundary,
                "selectorCount": len(selection.selectors),
                "selectors": selector_evidence,
                "functionCount": len(selection.function_ids),
                "functionIds": list(selection.function_ids),
                "executedCaseIds": list(selection.executed_case_ids),
                "validatedFixtures": [
                    generated_fixture_evidence(large, file_name, count)
                    for file_name, count in fixture_specs
                ],
                "compileAndSetupForced": True,
                "forcedResultSha256": _sha256_json(forced_results),
                "measurementEntered": False,
                "timingSampleCount": 0,
                "dsrExtremeTrialMix": (
                    _validate_dsr_case(dsr_case) if dsr_case is not None else None
                ),
                "startedAt": started,
                "finishedAt": finished,
                "status": "PASS",
            },
        )
    except (ContractError, GateError, ImportError, OSError, KeyError, ValueError) as exc:
        print(f"PYTHON_BENCHMARK_SMOKE_FAIL:{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "boundaryId": arguments.boundary,
                "output": arguments.output.name,
                "status": "PASS",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
