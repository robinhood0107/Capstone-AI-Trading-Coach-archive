module S14X.CoreSpec (tests) where

import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit ((@?=), assertBool, assertFailure, testCase)

import qualified Data.Vector.Unboxed as U

import S14X.Core.AdvancedRisk
  ( christoffersenConditionalCoverageTest,
    christoffersenIndependenceTest,
    deflatedSharpeRatio,
    historicalExpectedShortfall,
    loAdjustedSharpeRatio,
    probabilisticSharpeRatio,
    realizedVariance,
    realizedVolatilityIntraday,
  )
import S14X.Core.Error
  ( StableError
      ( DenominatorZero,
        InputTooShort,
        MomentInvalid,
        PricesNonPositive,
        ResearchInputTooShort,
        SimpleReturnBelowMinusOne
      ),
  )
import S14X.Core.Models
  ( ConditionalCoverageResult (ConditionalCoverageResult),
    IndependenceResult (IndependenceResult),
    TrialProvenance (TrialProvenance),
  )
import S14X.Core.ProductionMetrics
  ( annualizedVolatility,
    cagr,
    cumulativeReturn,
    historicalCvar,
    historicalVar,
    logReturns,
    maxDrawdown,
    realizedVolatility,
    sharpeRatio,
    simpleReturns,
    sortinoRatio,
  )

tests :: TestTree
tests =
  testGroup
    "pure-core"
    [ testCase "production hand fixtures" productionHandFixtures,
      testCase "advanced hand fixtures" advancedHandFixtures,
      testCase "stable validation precedence" stableErrors,
      testCase "backtest records preserve exact integer fields" backtestRecords
    ]

productionHandFixtures :: IO ()
productionHandFixtures = do
  assertVectorClose [1.0, -0.5] (simpleReturns (U.fromList [100.0, 200.0, 100.0]))
  assertVectorClose [0.0, 0.0] (logReturns (U.fromList [100.0, 100.0, 100.0]))
  assertScalarClose (-0.01) (cumulativeReturn (U.fromList [0.1, -0.1]))
  assertScalarClose 0.21 (cagr (U.fromList [100.0, 110.0, 121.0]) 2)
  assertScalarClose 0.1 (realizedVolatility (U.fromList [0.0, 0.1, -0.1]))
  assertScalarClose 0.2 (annualizedVolatility (U.fromList [0.0, 0.1, -0.1]) 4)
  assertScalarClose (-0.5) (maxDrawdown (U.fromList [100.0, 120.0, 90.0, 108.0, 60.0]))
  assertScalarClose (1.0 / sqrt 3.0) (sharpeRatio (U.fromList [-0.01, 0.02, 0.02]) 0.0 1)
  assertScalarClose (sqrt 3.0) (sortinoRatio (U.fromList [-0.01, 0.02, 0.02]) 0.0 1)
  assertScalarClose (-0.06) (historicalVar (U.fromList [-0.1, -0.05, 0.0, 0.05, 0.1]) 0.8)
  assertScalarClose
    (-0.0625)
    (historicalCvar (U.fromList [-0.1, -0.05, -0.05, -0.05, 0.1]) 0.6)

advancedHandFixtures :: IO ()
advancedHandFixtures = do
  assertScalarClose
    (11.0 / 3.0)
    (historicalExpectedShortfall (U.fromList [1.0, 2.0, 3.0, 4.0]) 0.625)
  assertScalarClose 9.0 (realizedVariance (U.fromList [1.0, 2.0, 2.0]))
  assertScalarClose 3.0 (realizedVolatilityIntraday (U.fromList [1.0, 2.0, 2.0]))
  assertScalarClose
    0.565685424949238
    (loAdjustedSharpeRatio (U.fromList [-1.0, 0.0, 1.0, 2.0]) 2 0.0)
  assertScalarClose
    0.4472135954999579
    (loAdjustedSharpeRatio (U.fromList [-1.0, 0.0, 1.0, 2.0]) 1 0.0)
  assertScalarClose
    1.0690449676496974
    (loAdjustedSharpeRatio (U.fromList [-1.0, 2.0, 0.0, 1.0]) 2 0.0)
  assertScalarClose
    0.5
    (probabilisticSharpeRatio 1.0 1.0 6 0.0 3.0)
  let provenance =
        TrialProvenance
          "s1.4r-effective-trials-v1"
          "pre_registered_independent"
          2
          2
          "daily"
          "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
          1
  assertScalarClose
    0.8097031129023626
    (deflatedSharpeRatio 1.0 6 0.0 3.0 2 1.0 provenance)

stableErrors :: IO ()
stableErrors = do
  simpleReturns (U.fromList [100.0]) @?= Left InputTooShort
  logReturns (U.fromList [100.0, 0.0]) @?= Left PricesNonPositive
  cumulativeReturn (U.fromList [-1.0000000000000002]) @?= Left SimpleReturnBelowMinusOne
  cumulativeReturn (U.fromList [-1.0, 1.0e308]) @?= Right (-1.0)
  sharpeRatio (U.fromList [0.01, 0.01]) 0.0 252 @?= Left DenominatorZero
  historicalExpectedShortfall U.empty 0.95 @?= Left ResearchInputTooShort
  loAdjustedSharpeRatio (U.fromList [0.0, 1.0]) 2 0.0 @?= Left ResearchInputTooShort
  probabilisticSharpeRatio 1.0 0.0 6 2.0 1.0 @?= Left MomentInvalid

backtestRecords :: IO ()
backtestRecords = do
  let losses = U.fromList [0.0, 2.0, 0.0, 2.0, 0.0]
      forecasts = U.replicate 5 1.0
  case christoffersenIndependenceTest losses forecasts 0.05 of
    Left stableError -> assertFailure ("unexpected stable error: " <> show stableError)
    Right (IndependenceResult _ _ _ observations exceptions _ _ _) -> do
      observations @?= 5
      exceptions @?= 2
  case christoffersenConditionalCoverageTest losses forecasts 0.75 0.05 of
    Left stableError -> assertFailure ("unexpected stable error: " <> show stableError)
    Right (ConditionalCoverageResult _ _ _ observations exceptions _ _ _ conditioned _ _ _) -> do
      observations @?= 5
      exceptions @?= 2
      conditioned @?= 4

assertScalarClose :: Double -> Either StableError Double -> IO ()
assertScalarClose expected actual =
  case actual of
    Left stableError -> assertFailure ("unexpected stable error: " <> show stableError)
    Right value ->
      assertBool
        ("expected " <> show expected <> ", got " <> show value)
        (abs (value - expected) <= 1.0e-12 * max 1.0 (max (abs value) (abs expected)))

assertVectorClose :: [Double] -> Either StableError (U.Vector Double) -> IO ()
assertVectorClose expected actual =
  case actual of
    Left stableError -> assertFailure ("unexpected stable error: " <> show stableError)
    Right values -> do
      U.length values @?= length expected
      assertBool
        ("vector mismatch: " <> show (U.toList values))
        (and (zipWith close expected (U.toList values)))
  where
    close left right = abs (left - right) <= 1.0e-12 * max 1.0 (max (abs left) (abs right))
