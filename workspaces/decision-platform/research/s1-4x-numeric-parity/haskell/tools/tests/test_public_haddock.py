"""S1.4X Haskell public 경계의 짧은 한국어 Haddock 계약을 검사한다."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


HASKELL_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DECLARATIONS = {
    "src/core/S14X/Core/ProductionMetrics.hs": (
        "annualizedVolatility",
        "cagr",
        "cumulativeReturn",
        "historicalCvar",
        "historicalVar",
        "logReturns",
        "maxDrawdown",
        "realizedVolatility",
        "sharpeRatio",
        "simpleReturns",
        "sortinoRatio",
    ),
    "src/core/S14X/Core/AdvancedRisk.hs": (
        "christoffersenConditionalCoverageTest",
        "christoffersenIndependenceTest",
        "deflatedSharpeRatio",
        "historicalExpectedShortfall",
        "kupiecUnconditionalCoverageTest",
        "loAdjustedSharpeRatio",
        "probabilisticSharpeRatio",
        "realizedVariance",
        "realizedVolatilityIntraday",
    ),
    "src/core/S14X/Core/NumericPrimitives.hs": (
        "bernoulliLogLikelihood",
        "chiSquareOneSurvival",
        "chiSquareTwoSurvival",
        "confidenceExceptionLogLikelihood",
        "finiteProbability",
        "finiteResearchResult",
        "hf7Quantile",
        "independenceLikelihoodComponents",
        "kupiecLikelihoodComponents",
        "likelihoodRatio",
        "likelihoodRoundoffTolerance",
        "meanVector",
        "normalCdf",
        "normalInverseCdf",
        "pureSort",
        "sampleVariance",
        "stableWeightedMean",
        "sumVector",
        "xlogComplement",
        "xlogProbability",
    ),
    "src/core/S14X/Core/ScalarValidation.hs": (
        "ensureFinite",
        "maxFloatInteger",
        "validateConfidence",
        "validateFiniteScalar",
        "validateMomentPair",
        "validatePositiveInteger",
        "validateSampleSize",
        "validateSignificance",
        "validateTrialCount",
    ),
    "src/core/S14X/Core/Validation.hs": (
        "BacktestInputs",
        "transitionCounts",
        "validateBacktestInputs",
        "validateProductionVector",
        "validateResearchVector",
        "validateTransitionIdentifiability",
    ),
    "src/core/S14X/Core/Error.hs": (
        "StableError",
        "allStableErrors",
        "stableErrorCode",
    ),
    "src/core/S14X/Core/Models.hs": (
        "ConditionalCoverageResult",
        "IndependenceResult",
        "LikelihoodResult",
        "NumericResult",
        "TransitionCounts",
        "TrialProvenance",
    ),
    "src/contract/S14X/Contract/AtomicOutput.hs": (
        "PublishResult",
        "exclusiveAtomicWrite",
    ),
    "src/contract/S14X/Contract/BenchmarkValidation.hs": (
        "BenchmarkResultShape",
        "validateBenchmarkResults",
    ),
    "src/contract/S14X/Contract/StrictJson.hs": (
        "objectMap",
        "parseStrictJson",
        "rawDouble",
        "rawInteger",
    ),
    "src/contract/S14X/Contract/Process.hs": (
        "encodeResultBatch",
        "encodeTransportError",
        "implementationLabel",
        "parseRequest",
        "runRequest",
        "sha256Hex",
    ),
    "src/contract/S14X/Contract/Types.hs": (
        "CaseRequest",
        "CaseResult",
        "FunctionId",
        "RawJson",
        "RequestBatch",
        "ResultBatch",
        "TransportCode",
        "TransportError",
        "functionIdText",
    ),
    "app/Main.hs": ("main",),
    "benchmark/Main.hs": ("main",),
}


def declaration_index(lines: list[str], name: str) -> int:
    patterns = (
        re.compile(rf"^{re.escape(name)}\s*::"),
        re.compile(rf"^data\s+{re.escape(name)}(?:\s|$)"),
    )
    matches = [
        index
        for index, line in enumerate(lines)
        if any(pattern.search(line) for pattern in patterns)
    ]
    if len(matches) != 1:
        raise AssertionError(f"public declaration identity drift: {name}:{matches}")
    return matches[0]


class PublicHaddockTests(unittest.TestCase):
    def test_exact_twenty_metric_api_set_is_covered(self) -> None:
        metric_names = (
            PUBLIC_DECLARATIONS["src/core/S14X/Core/ProductionMetrics.hs"]
            + PUBLIC_DECLARATIONS["src/core/S14X/Core/AdvancedRisk.hs"]
        )
        self.assertEqual(len(metric_names), 20)
        self.assertEqual(len(metric_names), len(set(metric_names)))

    def test_public_boundaries_have_one_or_two_line_korean_haddock(self) -> None:
        violations: list[str] = []
        for relative, names in PUBLIC_DECLARATIONS.items():
            lines = (HASKELL_ROOT / relative).read_text(encoding="utf-8").splitlines()
            for name in names:
                index = declaration_index(lines, name)
                block: list[str] = []
                cursor = index - 1
                while cursor >= 0 and lines[cursor].startswith("--"):
                    block.append(lines[cursor])
                    cursor -= 1
                block.reverse()
                if (
                    not 1 <= len(block) <= 2
                    or not block[0].startswith("-- | ")
                    or not any(re.search(r"[가-힣]", line) for line in block)
                ):
                    violations.append(f"{relative}:{name}:{block}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
