module S14X.Contract.BenchmarkValidation
  ( BenchmarkResultShape (..),
    validateBenchmarkResults,
  )
where

import qualified Data.Vector.Unboxed as U

import S14X.Core.Error (StableError)
import S14X.Core.Models
  ( ConditionalCoverageResult (ConditionalCoverageResult),
    IndependenceResult (IndependenceResult),
    LikelihoodResult (LikelihoodResult),
    NumericResult
      ( ConditionalCoverageRecord,
        IndependenceRecord,
        LikelihoodRecord,
        ScalarResult,
        VectorResult
      ),
  )

data BenchmarkResultShape
  = ScalarBatch Int
  | VectorBatch Int Int
  | LikelihoodBatch Int
  | IndependenceBatch Int
  | ConditionalCoverageBatch Int
  deriving stock (Eq, Show)

-- Criterion setup은 같은 prepared input을 한 번 완전 평가해 빠른 Left/shape 오류가 timing에
-- 섞이지 않게 한다. 성공한 동일 kernel과 input만 뒤의 nf 측정에 전달된다.
validateBenchmarkResults ::
  BenchmarkResultShape ->
  [Either StableError NumericResult] ->
  Either String ()
validateBenchmarkResults expected results
  | length results /= expectedBatchSize expected =
      Left "benchmark prepared result count mismatch"
  | otherwise = validateAll (validateOne expected) results

expectedBatchSize :: BenchmarkResultShape -> Int
expectedBatchSize expected =
  case expected of
    ScalarBatch count -> count
    VectorBatch count _ -> count
    LikelihoodBatch count -> count
    IndependenceBatch count -> count
    ConditionalCoverageBatch count -> count

validateAll ::
  (value -> Either String ()) ->
  [value] ->
  Either String ()
validateAll validateValue =
  foldr
    (\value remaining -> validateValue value >> remaining)
    (Right ())

validateOne ::
  BenchmarkResultShape ->
  Either StableError NumericResult ->
  Either String ()
validateOne _ (Left _) =
  Left "benchmark prepared result contains a stable error"
validateOne expected (Right numericResult)
  | not (matchesShape expected numericResult) =
      Left "benchmark prepared result shape mismatch"
  | not (validNumericResult numericResult) =
      Left "benchmark prepared result is non-finite or negative zero"
  | otherwise = Right ()

matchesShape :: BenchmarkResultShape -> NumericResult -> Bool
matchesShape expected numericResult =
  case (expected, numericResult) of
    (ScalarBatch _, ScalarResult _) -> True
    (VectorBatch _ expectedLength, VectorResult values) ->
      U.length values == expectedLength
    (LikelihoodBatch _, LikelihoodRecord _) -> True
    (IndependenceBatch _, IndependenceRecord _) -> True
    (ConditionalCoverageBatch _, ConditionalCoverageRecord _) -> True
    _ -> False

validNumericResult :: NumericResult -> Bool
validNumericResult numericResult =
  case numericResult of
    ScalarResult value -> validDouble value
    VectorResult values -> U.all validDouble values
    LikelihoodRecord
      (LikelihoodResult statistic pValue _ _ _ _ significance) ->
        all validDouble [statistic, pValue, significance]
    IndependenceRecord
      (IndependenceResult statistic pValue _ _ _ _ significance _) ->
        all validDouble [statistic, pValue, significance]
    ConditionalCoverageRecord
      ( ConditionalCoverageResult
          statistic
          pValue
          _
          _
          _
          _
          significance
          _
          _
          _
          unconditionalComponent
          independenceComponent
        ) ->
        all
          validDouble
          [ statistic,
            pValue,
            significance,
            unconditionalComponent,
            independenceComponent
          ]

validDouble :: Double -> Bool
validDouble value =
  not (isNaN value || isInfinite value || negativeZero value)

negativeZero :: Double -> Bool
negativeZero value =
  value == 0.0 && 1.0 / value < 0.0
