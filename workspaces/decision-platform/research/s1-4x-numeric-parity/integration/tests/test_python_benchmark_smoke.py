"""Python benchmark all-selector smoke가 timing 없이 frozen closure를 강제하는지 검증한다."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest import TestCase

INTEGRATION = Path(__file__).resolve().parents[1]
BENCHMARKS = INTEGRATION.parent / "benchmarks"
sys.path.insert(0, str(INTEGRATION))
sys.path.insert(0, str(BENCHMARKS))

from benchmark_contract import strict_json_load  # noqa: E402
from gate import GateError  # noqa: E402
from python_benchmark_block import _prepare_operations_for_measurement  # noqa: E402
from python_benchmark_smoke import (  # noqa: E402
    _select_smoke_cases,
    _validate_dsr_case,
)

PLAN = BENCHMARKS / "benchmark-plan.v1.json"


class PythonBenchmarkSmokeTests(TestCase):
    def test_each_boundary_covers_every_nonempty_selector_and_function(self) -> None:
        plan = strict_json_load(PLAN)
        expected = {
            "python-numpy-s1-4": (2, 11),
            "python-numpy-s1-4r": (5, 9),
            "python-jax-eager-s1-4r": (5, 9),
            "python-jax-jit-s1-4r": (5, 9),
        }
        for boundary, (selector_count, function_count) in expected.items():
            selected = _select_smoke_cases(plan, boundary)
            self.assertEqual(len(selected.selectors), selector_count)
            self.assertEqual(len(selected.cases), function_count)
            self.assertEqual(
                {case["functionId"] for case in selected.cases},
                set(selected.function_ids),
            )
            self.assertTrue(
                all(
                    set(selector["expectedCaseIds"])
                    & set(selected.executed_case_ids)
                    for selector in selected.selectors
                )
            )

    def test_dsr_smoke_keeps_full_16384_batch_and_extreme_trial_mix(self) -> None:
        plan = strict_json_load(PLAN)
        selected = _select_smoke_cases(plan, "python-jax-jit-s1-4r")
        dsr = next(
            case
            for case in selected.cases
            if case["functionId"] == "deflated_sharpe_ratio"
        )
        evidence = _validate_dsr_case(dsr)
        self.assertEqual(evidence["batchSize"], 16384)
        self.assertEqual(
            evidence["trialCountMix"],
            [
                {"trialCount": 2, "evaluationCount": 5462},
                {"trialCount": 10**20, "evaluationCount": 5461},
                {"trialCount": 10**308, "evaluationCount": 5461},
            ],
        )

        modified = copy.deepcopy(dsr)
        modified["functionArguments"]["trial_count_mix"][2]["evaluation_count"] -= 1
        with self.assertRaisesRegex(GateError, "DSR_SMOKE_CASE_INVALID"):
            _validate_dsr_case(modified)

    def test_measurement_marker_follows_compile_force_and_all_warmups(self) -> None:
        events: list[str] = []
        operations = [
            ({"caseId": "case-a"}, lambda: events.append("case-a") or 1.0),
            ({"caseId": "case-b"}, lambda: events.append("case-b") or 2.0),
        ]

        _prepare_operations_for_measurement(
            operations,
            mark_measurement=lambda: events.append("measurement-entered"),
        )

        self.assertEqual(
            events,
            ["case-a", "case-b"] * 6 + ["measurement-entered"],
        )
