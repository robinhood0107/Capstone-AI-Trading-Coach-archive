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

import Data.List (foldl')
import Numeric.SpecFunctions (erfc, log1p)

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
normalCdf value = 0.5 * erfc (-value / sqrt 2.0)

-- CPython 3.12 NormalDist와 같은 Wichura AS241 branch/coefficient를 직접 보존한다.
-- statistics package 결과는 구현 입력이 아니라 cross-check에만 사용할 수 있다.
normalInverseCdf :: Double -> Double
normalInverseCdf probability
  | probability <= 0.0 = negate (1.0 / 0.0)
  | probability >= 1.0 = 1.0 / 0.0
  | abs centered <= 0.425 =
      let argument = 0.180625 - centered * centered
       in centered
            * horner
              argument
              [ 2.5090809287301227e3,
                3.3430575583588128e4,
                6.72657709270087e4,
                4.592195393154987e4,
                1.373169376550946e4,
                1.9715909503065514e3,
                1.3314166789178438e2,
                3.3871328727963665
              ]
            / horner
              argument
              [ 5.226495278852855e3,
                2.872908573572194e4,
                3.930789580009271e4,
                2.1213794301586597e4,
                5.394196021424751e3,
                6.871870074920579e2,
                4.231333070160091e1,
                1.0
              ]
  | otherwise =
      if centered < 0.0 then negate positive else positive
  where
    centered = probability - 0.5
    tailProbability =
      if centered <= 0.0 then probability else 1.0 - probability
    root = sqrt (negate (log tailProbability))
    positive
      | root <= 5.0 =
          let argument = root - 1.6
           in horner
                argument
                [ 7.745450142783414e-4,
                  2.2723844989269185e-2,
                  2.417807251774506e-1,
                  1.2704582524523684,
                  3.6478483247632045,
                  5.769497221460691,
                  4.630337846156545,
                  1.4234371107496835
                ]
                / horner
                  argument
                  [ 1.0507500716444169e-9,
                    5.475938084995345e-4,
                    1.5198666563616457e-2,
                    1.4810397642748007e-1,
                    6.897673349851e-1,
                    1.6763848301838038,
                    2.0531916266377588,
                    1.0
                  ]
      | otherwise =
          let argument = root - 5.0
           in horner
                argument
                [ 2.010334399292288e-7,
                  2.7115555687434876e-5,
                  1.2426609473880784e-3,
                  2.6532189526576123e-2,
                  2.965605718285049e-1,
                  1.7848265399172913,
                  5.463784911164114,
                  6.657904643501103
                ]
                / horner
                  argument
                  [ 2.0442631033899397e-15,
                    1.4215117583164459e-7,
                    1.8463183175100548e-5,
                    7.868691311456133e-4,
                    1.4875361290850615e-2,
                    1.369298809227358e-1,
                    5.99832206555888e-1,
                    1.0
                  ]

horner :: Double -> [Double] -> Double
horner argument =
  foldl' (\accumulator coefficient -> accumulator * argument + coefficient) 0.0

chiSquareOneSurvival :: Double -> Either StableError Double
chiSquareOneSurvival statistic = finiteProbability (erfc (sqrt (statistic / 2.0)))

chiSquareTwoSurvival :: Double -> Either StableError Double
chiSquareTwoSurvival statistic = finiteProbability (exp (-statistic / 2.0))

anyNonFinite :: [Double] -> Bool
anyNonFinite = any (\value -> isNaN value || isInfinite value)
