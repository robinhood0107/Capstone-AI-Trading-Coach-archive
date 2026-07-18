"""87-block benchmark completeness와 candidate performance 계산 회귀 테스트."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from unittest import TestCase

INTEGRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INTEGRATION))

from finalize_benchmark_run import (  # noqa: E402
    BenchmarkSummaryError,
    distribution,
    nearest_rank_p95,
    score_candidate_performance,
)


class BenchmarkSummaryTests(TestCase):
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

    def test_missing_candidate_case_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(BenchmarkSummaryError, "CANDIDATE_CASE_SET_MISMATCH"):
            score_candidate_performance(
                {
                    "scala": {"family-a/case-1": 1.0},
                    "haskell": {},
                },
                {"family-a/case-1": "family-a"},
            )
