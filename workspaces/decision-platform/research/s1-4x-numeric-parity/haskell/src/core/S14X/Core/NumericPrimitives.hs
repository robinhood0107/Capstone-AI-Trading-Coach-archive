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

import           Data.List (foldl')
import           Numeric.SpecFunctions (erfc, log1p)

import qualified Data.Vector.Unboxed as U

import           S14X.Core.Error (StableError (LikelihoodInvalid, ResearchResultNonFinite, ResultNonFinite))
import           S14X.Core.ScalarValidation (ensureFinite)

-- | immutable vector를 Neumaier 보상합으로 축약해 cancellation residual을 보존한다.
-- 호출자가 입력 유한성과 최종 결과 오류 매핑을 소유하는 내부 수치 primitive다.
sumVector :: U.Vector Double -> Double
sumVector values =
  let (total, compensation) =
        U.foldl'
          compensatedStep
          (0.0, 0.0)
          values
   in total + compensation

compensatedStep :: (Double, Double) -> Double -> (Double, Double)
compensatedStep (total, compensation) value =
  let candidate = total + value
      correction =
        if abs total >= abs value
          then (total - candidate) + value
          else (value - candidate) + total
   in (candidate, compensation + correction)

-- | 비어 있지 않은 검증 완료 벡터의 보상합 산술평균을 반환한다.
-- 빈 입력의 분모 0을 자체 복구하지 않으므로 public validation 경계 뒤에서만 호출한다.
meanVector :: U.Vector Double -> Double
meanVector values = sumVector values / fromIntegral (U.length values)

-- | 길이 2 이상인 검증 완료 벡터의 불편 표본분산을 계산한다.
-- 입력 길이와 비유한 결과의 stable 오류 변환은 상위 public API가 담당한다.
sampleVariance :: U.Vector Double -> Double
sampleVariance values =
  let mean = meanVector values
      squared = U.map (\value -> (value - mean) * (value - mean)) values
   in sumVector squared / fromIntegral (U.length values - 1)

-- | 검증 완료 벡터를 정렬해 Hyndman-Fan type 7 quantile을 보간한다.
-- 확률·길이 전제 위반이나 비유한 보간 결과는 'ResultNonFinite'로 닫는다.
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

-- | 변경 가능한 vector나 ST 없이 중앙 pivot과 immutable filter로 정렬한다.
-- 조건부 mutable optimization을 qualification 전에 도입하지 않는 baseline primitive다.
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

-- | 값과 정규화 가중치의 scale-aware 보상합으로 가중평균을 계산한다.
-- 정규화 범위가 roundoff 허용치를 벗어나거나 결과가 비유한 경우 research 오류를 반환한다.
stableWeightedMean :: U.Vector Double -> U.Vector Double -> Either StableError Double
stableWeightedMean values normalizedWeights =
  let scale = U.foldl' (\current value -> max current (abs value)) 0.0 values
   in if scale == 0.0
        then Right 0.0
        else
          let normalized =
                sumVector
                  (U.zipWith (\value weight -> weight * (value / scale)) values normalizedWeights)
              tolerance = 64.0 * encodeFloat 1 (-52)
           in if normalized < -1.0 - tolerance || normalized > 1.0 + tolerance
                then Left ResearchResultNonFinite
                else finiteResearchResult (max (-1.0) (min 1.0 normalized) * scale)

-- | 성공 횟수와 확률의 @count * log(p)@ 항을 경계값 @count == 0@까지 안전하게 계산한다.
-- 양의 count에서 확률이 @(0,1]@ 밖이면 likelihood 오류를 반환한다.
xlogProbability :: Int -> Double -> Either StableError Double
xlogProbability count probability
  | count == 0 = Right 0.0
  | probability <= 0.0 || probability > 1.0 = Left LikelihoodInvalid
  | otherwise = Right (fromIntegral count * log probability)

-- | 비성공 횟수의 @count * log(1-p)@ 항을 'log1p'로 안정적으로 계산한다.
-- 양의 count에서 확률이 @[0,1)@ 밖이면 likelihood 오류를 반환한다.
xlogComplement :: Int -> Double -> Either StableError Double
xlogComplement count probability
  | count == 0 = Right 0.0
  | probability < 0.0 || probability >= 1.0 = Left LikelihoodInvalid
  | otherwise = Right (fromIntegral count * log1p (-probability))

-- | Bernoulli 관측 수·성공 수·확률로 두 로그항의 보상된 합을 반환한다.
-- 입력 count의 조합 유효성은 호출자 검정 경계가 보장하고 확률 경계는 typed 오류로 검증한다.
bernoulliLogLikelihood :: Int -> Int -> Double -> Either StableError Double
bernoulliLogLikelihood observations successes probability = do
  complement <- xlogComplement (observations - successes) probability
  success <- xlogProbability successes probability
  Right (complement + success)

-- | VaR confidence 관례에 맞춰 예외·비예외 binomial log likelihood를 계산한다.
-- 입력은 검증된 관측 수와 열린 구간 confidence여야 하며 0-count 항은 정확히 0으로 둔다.
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

-- | 두 log likelihood 크기에 비례한 Float64 roundoff 허용치를 반환한다.
-- LR 성분 일치와 작은 음수 clamp에 동일 공식을 사용한다.
likelihoodRoundoffTolerance :: Double -> Double -> Double
likelihoodRoundoffTolerance nullLog alternativeLog =
  128.0 * encodeFloat 1 (-52) * max 1.0 (max (abs nullLog) (abs alternativeLog))

-- | alternative와 null log likelihood의 두 배 차이를 LR 통계량으로 변환한다.
-- 허용치 밖 음수와 비유한 입력은 각각 likelihood/research 오류로 거부한다.
likelihoodRatio :: Double -> Double -> Either StableError Double
likelihoodRatio nullLog alternativeLog
  | anyNonFinite [nullLog, alternativeLog] = Left ResearchResultNonFinite
  | statistic < -tolerance = Left LikelihoodInvalid
  | statistic < 0.0 = Right 0.0
  | otherwise = finiteResearchResult statistic
  where
    statistic = 2.0 * (alternativeLog - nullLog)
    tolerance = likelihoodRoundoffTolerance nullLog alternativeLog

-- | 네 Markov transition count에서 independent/Markov log likelihood와 LR을 함께 반환한다.
-- 두 row와 전체 transition의 식별 가능성은 public caller가 먼저 검증해야 한다.
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

-- | 관측·예외 수와 confidence로 Kupiec null/maximum-likelihood log와 LR을 반환한다.
-- 관측 수 양수와 confidence 범위는 public backtest 경계가 선행 보장한다.
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

-- | roundoff 허용치를 포함해 Float64 값을 닫힌 확률 @[0,1]@로 검증·clamp한다.
-- 범위 밖 또는 비유한 값은 'ResearchResultNonFinite'가 된다.
finiteProbability :: Double -> Either StableError Double
finiteProbability value
  | anyNonFinite [value] = Left ResearchResultNonFinite
  | value < -tolerance || value > 1.0 + tolerance = Left ResearchResultNonFinite
  | otherwise = Right (max 0.0 (min 1.0 value))
  where
    tolerance = 64.0 * encodeFloat 1 (-52)

-- | research kernel 결과가 유한한지 검사해 동일 값을 반환한다.
-- NaN과 infinity는 'ResearchResultNonFinite'로만 노출한다.
finiteResearchResult :: Double -> Either StableError Double
finiteResearchResult = ensureFinite ResearchResultNonFinite

-- | 'erfc' 기반 표준정규 누적분포함수를 계산해 큰 음수 tail의 cancellation을 줄인다.
-- 유한성·확률 clamp는 확률을 공개하는 상위 API가 수행한다.
normalCdf :: Double -> Double
normalCdf value = 0.5 * erfc (-(value / sqrt 2.0))

-- | CPython 3.12 'NormalDist'와 같은 Wichura AS241 branch/coefficient로 역정규 CDF를 계산한다.
-- statistics package는 구현 입력이 아닌 cross-check이며 @p <= 0@과 @p >= 1@은 무한 tail을 반환한다.
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

-- | 자유도 1인 chi-square 통계량의 survival probability를 'erfc'로 계산한다.
-- 최종 확률은 finite/range gate를 통과해야 한다.
chiSquareOneSurvival :: Double -> Either StableError Double
chiSquareOneSurvival statistic = finiteProbability (erfc (sqrt (statistic / 2.0)))

-- | 자유도 2인 chi-square 통계량의 survival probability를 지수식으로 계산한다.
-- 최종 확률은 finite/range gate를 통과해야 한다.
chiSquareTwoSurvival :: Double -> Either StableError Double
chiSquareTwoSurvival statistic = finiteProbability (exp (-(statistic / 2.0)))

anyNonFinite :: [Double] -> Bool
anyNonFinite = any (\value -> isNaN value || isInfinite value)
