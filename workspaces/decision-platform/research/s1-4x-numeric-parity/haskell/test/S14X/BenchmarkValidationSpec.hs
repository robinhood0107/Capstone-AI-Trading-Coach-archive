module S14X.BenchmarkValidationSpec (tests) where

import           Test.Tasty (TestTree, testGroup)
import           Test.Tasty.HUnit (testCase, (@?=))

import qualified Data.Vector.Unboxed as U

import           S14X.Contract.BenchmarkValidation (BenchmarkResultShape (ScalarBatch, VectorBatch),
                                                    validateBenchmarkResults)
import           S14X.Core.Error (StableError (ResearchInputInvalid))
import           S14X.Core.Models (NumericResult (ScalarResult, VectorResult))

tests :: TestTree
tests =
  testGroup
    "benchmark-setup-validation"
    [ testCase "valid prepared results pass exact count and shape" validPreparedResults,
      testCase "Left or wrong result shape fails closed" invalidPreparedResults,
      testCase "non-finite and negative-zero results fail closed" invalidNumericResults
    ]

validPreparedResults :: IO ()
validPreparedResults = do
  validateBenchmarkResults
    (ScalarBatch 2)
    [Right (ScalarResult 1.0), Right (ScalarResult 2.0)]
    @?= Right ()
  validateBenchmarkResults
    (VectorBatch 1 2)
    [Right (VectorResult (U.fromList [1.0, 2.0]))]
    @?= Right ()

invalidPreparedResults :: IO ()
invalidPreparedResults = do
  validateBenchmarkResults
    (ScalarBatch 1)
    [Left ResearchInputInvalid]
    @?= Left "benchmark prepared result contains a stable error"
  validateBenchmarkResults
    (ScalarBatch 1)
    [Right (VectorResult (U.fromList [1.0]))]
    @?= Left "benchmark prepared result shape mismatch"
  validateBenchmarkResults
    (ScalarBatch 2)
    [Right (ScalarResult 1.0)]
    @?= Left "benchmark prepared result count mismatch"

invalidNumericResults :: IO ()
invalidNumericResults = do
  validateBenchmarkResults
    (ScalarBatch 1)
    [Right (ScalarResult (0.0 / 0.0))]
    @?= Left "benchmark prepared result is non-finite or negative zero"
  validateBenchmarkResults
    (ScalarBatch 1)
    [Right (ScalarResult (-0.0))]
    @?= Left "benchmark prepared result is non-finite or negative zero"
