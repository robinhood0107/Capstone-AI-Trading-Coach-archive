module S14X.Core.NumericPrimitives
  ( bernoulliLogLikelihood,
    chiSquareOneSurvival,
    chiSquareTwoSurvival,
    confidenceExceptionLogLikelihood,
    finiteProbability,
    finiteResearchResult,
    hf7Quantile,
    independenceLikelihoodComponents,
    kupiecLikelihoodComponents,
    likelihoodRatio,
    likelihoodRoundoffTolerance,
    meanVector,
    normalCdf,
    normalInverseCdf,
    pureSort,
    sampleVariance,
    stableWeightedMean,
    sumVector,
    xlogComplement,
    xlogProbability,
  )
where

import Numeric.SpecFunctions (erfc, log1p)
import Statistics.Distribution (cumulative, quantile)
import Statistics.Distribution.Normal (standard)

import qualified Data.Vector.Unboxed as U

import S14X.Core.Error
  ( StableError
      ( LikelihoodInvalid,
        ResearchResultNonFinite,
        ResultNonFinite
      ),
  )
import S14X.Core.ScalarValidation (ensureFinite)

sumVector :: U.Vector Double -> Double
sumVector = U.foldl' (+) 0.0

meanVector :: U.Vector Double -> Double
meanVector values = sumVector values / fromIntegral (U.length values)

sampleVariance :: U.Vector Double -> Double
sampleVariance values =
  let mean = meanVector values
      squared = U.foldl' (\total value -> total + (value - mean) * (value - mean)) 0.0 values
   in squared / fromIntegral (U.length values - 1)

hf7Quantile :: U.Vector Double -> Double -> Either StableError Double
hf7Quantile values probability =
  let ordered = pureSort values
      index = fromIntegral (U.length ordered - 1) * probability
      lowerIndex = floor index
      upperIndex = ceiling index
      weight = index - fromIntegral lowerIndex
   in case (ordered U.!? lowerIndex, ordered U.!? upperIndex) of
        (Just lower, Just upper) ->
          ensureFinite ResultNonFinite (lower + weight * (upper - lower))
        _ -> Left ResultNonFinite

-- 변경 가능한 vector나 ST를 쓰지 않는 baseline 정렬이다. 중앙 pivot과 immutable filter를
-- 사용해 조건부 mutable optimization을 qualification 전에 도입하지 않는다.
pureSort :: U.Vector Double -> U.Vector Double
pureSort values
  | U.length values <= 1 = values
  | otherwise =
      case values U.!? (U.length values `div` 2) of
        Nothing -> values
        Just pivot ->
          U.concat
            [ pureSort (U.filter (< pivot) values),
              U.filter (== pivot) values,
              pureSort (U.filter (> pivot) values)
            ]

stableWeightedMean :: U.Vector Double -> U.Vector Double -> Either StableError Double
stableWeightedMean values normalizedWeights =
  let scale = U.foldl' (\current value -> max current (abs value)) 0.0 values
   in if scale == 0.0
        then Right 0.0
        else
          let normalized =
                U.sum
                  (U.zipWith (\value weight -> weight * (value / scale)) values normalizedWeights)
              tolerance = 64.0 * encodeFloat 1 (-52)
           in if normalized < -1.0 - tolerance || normalized > 1.0 + tolerance
                then Left ResearchResultNonFinite
                else finiteResearchResult (max (-1.0) (min 1.0 normalized) * scale)

xlogProbability :: Int -> Double -> Either StableError Double
xlogProbability count probability
  | count == 0 = Right 0.0
  | probability <= 0.0 || probability > 1.0 = Left LikelihoodInvalid
  | otherwise = Right (fromIntegral count * log probability)

xlogComplement :: Int -> Double -> Either StableError Double
xlogComplement count probability
  | count == 0 = Right 0.0
  | probability < 0.0 || probability >= 1.0 = Left LikelihoodInvalid
  | otherwise = Right (fromIntegral count * log1p (-probability))

bernoulliLogLikelihood :: Int -> Int -> Double -> Either StableError Double
bernoulliLogLikelihood observations successes probability = do
  complement <- xlogComplement (observations - successes) probability
  success <- xlogProbability successes probability
  Right (complement + success)

confidenceExceptionLogLikelihood :: Int -> Int -> Double -> Double
confidenceExceptionLogLikelihood observations exceptions confidence =
  let nonExceptions = observations - exceptions
      normalTerm =
        if nonExceptions == 0
          then 0.0
          else fromIntegral nonExceptions * log confidence
      exceptionTerm =
        if exceptions == 0
          then 0.0
          else fromIntegral exceptions * log1p (-confidence)
   in normalTerm + exceptionTerm

likelihoodRoundoffTolerance :: Double -> Double -> Double
likelihoodRoundoffTolerance nullLog alternativeLog =
  128.0 * encodeFloat 1 (-52) * max 1.0 (max (abs nullLog) (abs alternativeLog))

likelihoodRatio :: Double -> Double -> Either StableError Double
likelihoodRatio nullLog alternativeLog
  | anyNonFinite [nullLog, alternativeLog] = Left ResearchResultNonFinite
  | statistic < -tolerance = Left LikelihoodInvalid
  | statistic < 0.0 = Right 0.0
  | otherwise = finiteResearchResult statistic
  where
    statistic = 2.0 * (alternativeLog - nullLog)
    tolerance = likelihoodRoundoffTolerance nullLog alternativeLog

independenceLikelihoodComponents ::
  Int ->
  Int ->
  Int ->
  Int ->
  Either StableError (Double, Double, Double)
independenceLikelihoodComponents n00 n01 n10 n11 = do
  let rowZero = n00 + n01
      rowOne = n10 + n11
      transitions = rowZero + rowOne
      piZeroOne = fromIntegral n01 / fromIntegral rowZero
      piOneOne = fromIntegral n11 / fromIntegral rowOne
      transitionProbability = fromIntegral (n01 + n11) / fromIntegral transitions
  independentLog <- bernoulliLogLikelihood transitions (n01 + n11) transitionProbability
  markov00 <- xlogComplement n00 piZeroOne
  markov01 <- xlogProbability n01 piZeroOne
  markov10 <- xlogComplement n10 piOneOne
  markov11 <- xlogProbability n11 piOneOne
  let markovLog = markov00 + markov01 + markov10 + markov11
  statistic <- likelihoodRatio independentLog markovLog
  Right (statistic, independentLog, markovLog)

kupiecLikelihoodComponents ::
  Int ->
  Int ->
  Double ->
  Either StableError (Double, Double, Double)
kupiecLikelihoodComponents observations exceptions confidence = do
  let maximumLikelihoodProbability = fromIntegral exceptions / fromIntegral observations
      nullLog = confidenceExceptionLogLikelihood observations exceptions confidence
  alternativeLog <-
    bernoulliLogLikelihood observations exceptions maximumLikelihoodProbability
  statistic <- likelihoodRatio nullLog alternativeLog
  Right (statistic, nullLog, alternativeLog)

finiteProbability :: Double -> Either StableError Double
finiteProbability value
  | anyNonFinite [value] = Left ResearchResultNonFinite
  | value < -tolerance || value > 1.0 + tolerance = Left ResearchResultNonFinite
  | otherwise = Right (max 0.0 (min 1.0 value))
  where
    tolerance = 64.0 * encodeFloat 1 (-52)

finiteResearchResult :: Double -> Either StableError Double
finiteResearchResult = ensureFinite ResearchResultNonFinite

normalCdf :: Double -> Double
normalCdf = cumulative standard

normalInverseCdf :: Double -> Double
normalInverseCdf = quantile standard

chiSquareOneSurvival :: Double -> Either StableError Double
chiSquareOneSurvival statistic = finiteProbability (erfc (sqrt (statistic / 2.0)))

chiSquareTwoSurvival :: Double -> Either StableError Double
chiSquareTwoSurvival statistic = finiteProbability (exp (-statistic / 2.0))

anyNonFinite :: [Double] -> Bool
anyNonFinite = any (\value -> isNaN value || isInfinite value)
