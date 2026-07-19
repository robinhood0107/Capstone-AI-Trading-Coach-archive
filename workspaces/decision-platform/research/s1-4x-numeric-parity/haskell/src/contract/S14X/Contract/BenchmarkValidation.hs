module S14X.Contract.BenchmarkValidation
  ( BenchmarkResultShape (..),
    validateBenchmarkResults,
  )
where

import qualified Data.Vector.Unboxed as U

import           S14X.Core.Error (StableError)
import           S14X.Core.Models (ConditionalCoverageResult (ConditionalCoverageResult),
                                   IndependenceResult (IndependenceResult),
                                   LikelihoodResult (LikelihoodResult),
                                   NumericResult (ConditionalCoverageRecord, IndependenceRecord, LikelihoodRecord, ScalarResult, VectorResult))

-- | Criterion batch의 기대 result constructor, batch 수, vector 길이를 표현한다.
-- timing 전에 실제 kernel output과 exact 일치하는지 검증하는 setup 계약이다.
data BenchmarkResultShape
  = ScalarBatch Int
  | VectorBatch Int Int
  | LikelihoodBatch Int
  | IndependenceBatch Int
  | ConditionalCoverageBatch Int
  deriving stock (Eq, Show)

-- | Criterion setup에서 prepared output을 완전 평가해 Left·shape·비유한·negative-zero를 거부한다.
-- 성공한 동일 kernel과 input만 뒤의 @nf@ timing에 전달된다.
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
