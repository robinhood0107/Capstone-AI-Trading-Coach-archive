"""87-block benchmark completeness와 candidate performance 계산 회귀 테스트."""

from __future__ import annotations

import ast
import math
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

INTEGRATION = Path(__file__).resolve().parents[1]
BENCHMARKS = INTEGRATION.parent / "benchmarks"
sys.path.insert(0, str(INTEGRATION))
sys.path.insert(0, str(BENCHMARKS))

from benchmark_contract import (  # type: ignore[import-not-found]  # noqa: E402
    sha256_file,
    strict_json_load,
)
from finalize_benchmark_run import (  # noqa: E402
    BenchmarkSummaryError,
    _validate_performance_timeout,
    distribution,
    nearest_rank_p95,
    score_candidate_performance,
)
from run_rotated_blocks import build_schedule  # type: ignore[import-not-found]  # noqa: E402

PLAN = BENCHMARKS / "benchmark-plan.v1.json"


class BenchmarkSummaryTests(TestCase):
    def test_finalizer_marks_native_receipts_as_historical_fd_evidence(self) -> None:
        module = ast.parse(
            (INTEGRATION / "finalize_benchmark_run.py").read_text(encoding="utf-8")
        )
        finalize_run = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "finalize_run"
        )
        validation_calls = [
            node
            for node in ast.walk(finalize_run)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "validate_native_contract_evidence"
            )
        ]
        self.assertEqual(len(validation_calls), 1)
        keyword_values = {
            keyword.arg: keyword.value
            for keyword in validation_calls[0].keywords
            if keyword.arg is not None
        }
        historical_flag = keyword_values.get("_require_live_haskell_fds")
        self.assertIsInstance(historical_flag, ast.Constant)
        self.assertIs(historical_flag.value, False)

    def test_three_repetition_distribution_has_no_synthetic_p95(self) -> None:
        summary = distribution([3.0, 1.0, 2.0])
        self.assertEqual(
            summary,
            {"sampleCount": 3, "min": 1.0, "median": 2.0, "max": 3.0},
        )
        self.assertNotIn("p95", summary)

    def test_candidate_score_uses_case_then_family_geometric_means(self) -> None:
        candidate_case_medians = {
            "scala": {"family-a/case-1": 10.0, "family-b/case-2": 40.0},
            "haskell": {"family-a/case-1": 20.0, "family-b/case-2": 20.0},
        }
        family_by_case = {
            "family-a/case-1": "family-a",
            "family-b/case-2": "family-b",
        }
        scores = score_candidate_performance(
            candidate_case_medians,
            family_by_case,
        )
        expected_ratio = math.sqrt(0.5)
        self.assertAlmostEqual(scores["scala"]["aggregateRatio"], expected_ratio)
        self.assertAlmostEqual(scores["haskell"]["aggregateRatio"], expected_ratio)
        self.assertAlmostEqual(
            scores["scala"]["performancePoints"],
            15.0 * expected_ratio,
        )

    def test_native_nearest_rank_p95_uses_only_framework_samples(self) -> None:
        self.assertEqual(nearest_rank_p95([float(value) for value in range(1, 21)]), 19.0)

    def test_valid_candidate_family_timeout_scores_zero_without_not_measured(self) -> None:
        candidate_case_medians = {
            "scala": {"family-a/case-1": 10.0, "family-b/case-2": 40.0},
            "haskell": {"family-a/case-1": 20.0, "family-b/case-2": 20.0},
        }
        family_by_case = {
            "family-a/case-1": "family-a",
            "family-b/case-2": "family-b",
        }
        scores = score_candidate_performance(
            candidate_case_medians,
            family_by_case,
            timed_out_families={"scala": {"family-b"}, "haskell": set()},
        )
        self.assertEqual(scores["scala"]["familyRatios"]["family-b"], 0.0)
        self.assertEqual(scores["scala"]["aggregateRatio"], 0.0)
        self.assertEqual(scores["scala"]["performancePoints"], 0.0)
        self.assertEqual(scores["haskell"]["familyRatios"]["family-b"], 1.0)
        self.assertAlmostEqual(scores["haskell"]["aggregateRatio"], math.sqrt(0.5))

    def test_timeout_evidence_requires_exact_frozen_identity_and_artifacts(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        qualification_path = temporary / "timeout-qualification.json"
        qualification_path.write_text('{"qualified":true}\n', encoding="utf-8")
        qualification_sha256 = sha256_file(qualification_path)
        plan = strict_json_load(PLAN)
        block = build_schedule(plan)[0]
        qualification = {
            "run": {
                "runId": "run-001",
                "rotationId": block.rotation_id,
                "outerRepetition": block.outer_repetition,
                "timeoutSeconds": block.timeout_seconds,
            }
        }
        evidence = {
            "schemaVersion": "s1.4x-valid-performance-timeout-v1",
            "planId": plan["planId"],
            "runId": "run-001",
            "rotationId": block.rotation_id,
            "outerRepetition": block.outer_repetition,
            "boundaryId": block.boundary_id,
            "familyId": block.family_id,
            "selectorId": block.selector_id,
            "timeoutSeconds": block.timeout_seconds,
            "measurementEntered": True,
            "timeoutQualificationSha256": qualification_sha256,
            "terminationSequence": [
                "SIGTERM",
                "bounded-grace-5s",
                "SIGKILL-if-needed",
            ],
            "partialArtifactsUsedForScoring": False,
            "scoreDisposition": "candidate-family-ratio-zero",
            "continueRemainingPredeclaredMatrix": True,
            "artifacts": [
                {
                    "path": "timeout-qualification.json",
                    "sha256": qualification_sha256,
                    "sizeBytes": qualification_path.stat().st_size,
                }
            ],
        }
        _validate_performance_timeout(
            evidence,
            plan=plan,
            block=block,
            qualification=qualification,
            qualification_sha256=qualification_sha256,
            block_directory=temporary,
        )

        invalid_variants = {
            "extra-field": {**evidence, "benchmarkSubjectCommit": "a" * 40},
            "wrong-plan": {**evidence, "planId": "wrong"},
            "wrong-run": {**evidence, "runId": "wrong"},
            "wrong-repetition": {**evidence, "outerRepetition": 2},
            "wrong-timeout": {**evidence, "timeoutSeconds": 1},
            "wrong-qualification-hash": {
                **evidence,
                "timeoutQualificationSha256": "0" * 64,
            },
            "wrong-termination": {**evidence, "terminationSequence": ["SIGKILL"]},
            "stop-matrix": {
                **evidence,
                "continueRemainingPredeclaredMatrix": False,
            },
            "bad-artifact": {
                **evidence,
                "artifacts": [
                    {
                        "path": "../timeout-qualification.json",
                        "sha256": "not-a-hash",
                        "sizeBytes": -1,
                    }
                ],
            },
        }
        for label, invalid in invalid_variants.items():
            with self.subTest(label=label), self.assertRaises(
                BenchmarkSummaryError
            ):
                _validate_performance_timeout(
                    invalid,
                    plan=plan,
                    block=block,
                    qualification=qualification,
                    qualification_sha256=qualification_sha256,
                    block_directory=temporary,
                )

    def test_missing_candidate_case_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(BenchmarkSummaryError, "CANDIDATE_CASE_SET_MISMATCH"):
            score_candidate_performance(
                {
                    "scala": {"family-a/case-1": 1.0},
                    "haskell": {},
                },
                {"family-a/case-1": "family-a"},
            )
