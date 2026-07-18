module S14X.Core.AdvancedRisk
  ( christoffersenConditionalCoverageTest,
    christoffersenIndependenceTest,
    deflatedSharpeRatio,
    historicalExpectedShortfall,
    kupiecUnconditionalCoverageTest,
    loAdjustedSharpeRatio,
    probabilisticSharpeRatio,
    realizedVariance,
    realizedVolatilityIntraday,
  )
where

import           Data.Char (isHexDigit, isLower)

import qualified Data.Vector.Unboxed as U

import           S14X.Core.Error (StableError (AggregationPeriodsInvalid, LikelihoodInvalid, MomentInvalid, ResearchInputInvalid, ResearchInputTooShort, ResearchResultNonFinite, TrialProvenanceInvalid, TrialVarianceInvalid))
import           S14X.Core.Models (ConditionalCoverageResult (ConditionalCoverageResult),
                                   IndependenceResult (IndependenceResult),
                                   LikelihoodResult (LikelihoodResult),
                                   TransitionCounts (TransitionCounts),
                                   TrialProvenance (TrialProvenance))
import           S14X.Core.NumericPrimitives (chiSquareOneSurvival, chiSquareTwoSurvival,
                                              confidenceExceptionLogLikelihood,
                                              finiteResearchResult,
                                              independenceLikelihoodComponents,
                                              kupiecLikelihoodComponents, likelihoodRatio,
                                              likelihoodRoundoffTolerance, meanVector, normalCdf,
                                              normalInverseCdf, pureSort, stableWeightedMean,
                                              sumVector)
import           S14X.Core.ScalarValidation (validateConfidence, validateFiniteScalar,
                                             validateMomentPair, validateSampleSize,
                                             validateSignificance, validateTrialCount)
import           S14X.Core.Validation (BacktestInputs (BacktestInputs), transitionCounts,
                                       validateBacktestInputs, validateResearchVector,
                                       validateTransitionIdentifiability)

-- | 손실 벡터의 상위 꼬리를 confidence 경계에서 부분 가중해 historical expected shortfall을 계산한다.
-- research 입력·confidence·가중 결과가 유효하지 않으면 닫힌 'StableError'를 반환한다.
historicalExpectedShortfall ::
  U.Vector Double ->
  Double ->
  Either StableError Double
historicalExpectedShortfall rawLosses confidence = do
  losses <- validateResearchVector 1 rawLosses
  probability <- validateConfidence ResearchInputInvalid confidence
  let tailMass = fromIntegral (U.length losses) * (1.0 - probability)
      ordered = U.reverse (pureSort losses)
      weights =
        U.generate
          (U.length losses)
          (\index -> max 0.0 (min 1.0 (tailMass - fromIntegral index)) / tailMass)
  stableWeightedMean ordered weights

-- | intraday 수익률 제곱을 보상합으로 더해 realized variance를 반환한다.
-- 최소 길이와 유한성은 research 경계에서 검증하고 비유한 출력은 거부한다.
realizedVariance :: U.Vector Double -> Either StableError Double
realizedVariance rawReturns = do
  returns <- validateResearchVector 1 rawReturns
  finiteResearchResult (sumVector (U.map (\value -> value * value) returns))

-- | realized variance의 제곱근으로 intraday realized volatility를 계산한다.
-- variance 검증 오류와 비유한 제곱근 결과를 그대로 stable 오류로 전달한다.
realizedVolatilityIntraday :: U.Vector Double -> Either StableError Double
realizedVolatilityIntraday rawReturns = do
  variance <- realizedVariance rawReturns
  finiteResearchResult (sqrt variance)

-- | Lo의 자기상관 보정식을 수익률과 aggregation period에 적용한 Sharpe ratio를 반환한다.
-- period는 @0 < q < n@이어야 하며 비양수 분산·보정분모는 moment 오류가 된다.
loAdjustedSharpeRatio ::
  U.Vector Double ->
  Integer ->
  Double ->
  Either StableError Double
loAdjustedSharpeRatio rawReturns aggregationPeriods riskFreeRate = do
  returns <- validateResearchVector 2 rawReturns
  riskFree <- validateFiniteScalar ResearchInputInvalid riskFreeRate
  if aggregationPeriods <= 0
    then Left AggregationPeriodsInvalid
    else
      if aggregationPeriods >= toInteger (U.length returns)
        then Left ResearchInputTooShort
        else do
          let periods = fromInteger aggregationPeriods
              excess = U.map (\value -> value - riskFree) returns
              mean = meanVector excess
              centered = U.map (\value -> value - mean) excess
              observations = fromIntegral (U.length centered)
              gammaZero = sumVector (U.map (\value -> value * value) centered) / observations
          if isNaN gammaZero || isInfinite gammaZero || gammaZero <= 0.0
            then Left MomentInvalid
            else do
              weightedAutocorrelation <-
                weightedCorrelationSum centered gammaZero aggregationPeriods
              let denominator = 1.0 + 2.0 * weightedAutocorrelation
              if isNaN denominator || isInfinite denominator || denominator <= 0.0
                then Left MomentInvalid
                else
                  finiteResearchResult
                    (mean / sqrt gammaZero * sqrt (periods / denominator))

-- | 관측 Sharpe가 기준 Sharpe를 넘을 확률을 표본크기·왜도·Pearson kurtosis로 계산한다.
-- Bailey와 López de Prado의 PSR 입력 제약과 Float64 확률 범위를 fail-closed로 검증한다.
probabilisticSharpeRatio ::
  Double ->
  Double ->
  Integer ->
  Double ->
  Double ->
  Either StableError Double
probabilisticSharpeRatio observedSharpe benchmarkSharpe sampleSize skewness kurtosis = do
  (observed, benchmark, observations, radicand) <-
    validatedPsrInputs observedSharpe benchmarkSharpe sampleSize skewness kurtosis
  zScore <-
    finiteResearchResult
      ((observed - benchmark) * sqrt (observations - 1.0) / sqrt radicand)
  finiteProbabilityResult (normalCdf zScore)

-- | frozen trial provenance와 Sharpe 추정분산으로 multiple-testing benchmark를 만든 뒤 DSR을 계산한다.
-- trial count·분산·registry provenance가 일치하지 않으면 확률 계산 전에 stable 오류로 중단한다.
deflatedSharpeRatio ::
  Double ->
  Integer ->
  Double ->
  Double ->
  Integer ->
  Double ->
  TrialProvenance ->
  Either StableError Double
deflatedSharpeRatio
  observedSharpe
  sampleSize
  skewness
  kurtosis
  trialCount
  sharpeEstimateVariance
  provenance = do
    (observed, _, observations, _) <-
      validatedPsrInputs observedSharpe 0.0 sampleSize skewness kurtosis
    trials <- validateTrialCount trialCount
    variance <- validateFiniteScalar TrialVarianceInvalid sharpeEstimateVariance
    if variance <= 0.0
      then Left TrialVarianceInvalid
      else do
        validateTrialProvenance trialCount provenance
        let reciprocalTrials = 1.0 / trials
            firstQuantile = -normalInverseCdf reciprocalTrials
            secondQuantile = -normalInverseCdf (reciprocalTrials / exp 1.0)
            eulerMascheroni = 0.5772156649015329
        benchmark <-
          finiteResearchResult
            ( sqrt variance
                * ( (1.0 - eulerMascheroni) * firstQuantile
                      + eulerMascheroni * secondQuantile
                  )
            )
        probabilisticSharpeRatio
          observed
          benchmark
          (round observations)
          skewness
          kurtosis

-- | realized loss가 forecast VaR를 초과한 횟수로 Kupiec unconditional coverage LR 검정을 수행한다.
-- 동일 길이·비음수 forecast·confidence·significance 계약을 검증해 typed likelihood 결과를 반환한다.
kupiecUnconditionalCoverageTest ::
  U.Vector Double ->
  U.Vector Double ->
  Double ->
  Double ->
  Either StableError LikelihoodResult
kupiecUnconditionalCoverageTest realizedLosses forecastVars confidence significance = do
  BacktestInputs _ _ exceptions <-
    validateBacktestInputs 1 realizedLosses forecastVars
  probability <- validateConfidence ResearchInputInvalid confidence
  alpha <- validateSignificance significance
  let observations = U.length exceptions
      exceptionCount = U.sum exceptions
  (statistic, _, _) <-
    kupiecLikelihoodComponents observations exceptionCount probability
  pValue <- chiSquareOneSurvival statistic
  Right
    ( LikelihoodResult
        statistic
        pValue
        (pValue < alpha)
        observations
        exceptionCount
        1
        alpha
    )

-- | 예외 indicator 전이를 사용해 Christoffersen independence LR 검정을 수행한다.
-- 두 transition row를 식별할 표본이 없으면 'InsufficientSample'로 거부한다.
christoffersenIndependenceTest ::
  U.Vector Double ->
  U.Vector Double ->
  Double ->
  Either StableError IndependenceResult
christoffersenIndependenceTest realizedLosses forecastVars significance = do
  BacktestInputs _ _ exceptions <-
    validateBacktestInputs 2 realizedLosses forecastVars
  alpha <- validateSignificance significance
  let counts@(TransitionCounts n00 n01 n10 n11) = transitionCounts exceptions
  validateTransitionIdentifiability counts
  (statistic, _, _) <- independenceLikelihoodComponents n00 n01 n10 n11
  pValue <- chiSquareOneSurvival statistic
  Right
    ( IndependenceResult
        statistic
        pValue
        (pValue < alpha)
        (U.length exceptions)
        (U.sum exceptions)
        1
        alpha
        counts
    )

-- | conditional coverage 통계가 unconditional과 independence 성분의 합과 일치하는지 함께 검증한다.
-- direct LR과 성분 합의 roundoff 차이가 허용치를 넘으면 likelihood 오류로 닫는다.
christoffersenConditionalCoverageTest ::
  U.Vector Double ->
  U.Vector Double ->
  Double ->
  Double ->
  Either StableError ConditionalCoverageResult
christoffersenConditionalCoverageTest realizedLosses forecastVars confidence significance = do
  BacktestInputs _ _ exceptions <-
    validateBacktestInputs 2 realizedLosses forecastVars
  probability <- validateConfidence ResearchInputInvalid confidence
  alpha <- validateSignificance significance
  let counts@(TransitionCounts n00 n01 n10 n11) = transitionCounts exceptions
  validateTransitionIdentifiability counts
  (_, independentLog, markovLog) <-
    independenceLikelihoodComponents n00 n01 n10 n11
  let conditionedObservations = U.length exceptions - 1
      conditionedExceptions = n01 + n11
      conditionalNullLog =
        confidenceExceptionLogLikelihood
          conditionedObservations
          conditionedExceptions
          probability
  unconditionalComponent <- likelihoodRatio conditionalNullLog independentLog
  independenceComponent <- likelihoodRatio independentLog markovLog
  directStatistic <- likelihoodRatio conditionalNullLog markovLog
  let statistic = unconditionalComponent + independenceComponent
      tolerance = likelihoodRoundoffTolerance conditionalNullLog markovLog
  if abs (directStatistic - statistic) > tolerance
    then Left LikelihoodInvalid
    else do
      finiteStatistic <- finiteResearchResult statistic
      pValue <- chiSquareTwoSurvival finiteStatistic
      Right
        ( ConditionalCoverageResult
            finiteStatistic
            pValue
            (pValue < alpha)
            (U.length exceptions)
            (U.sum exceptions)
            2
            alpha
            counts
            conditionedObservations
            conditionedExceptions
            unconditionalComponent
            independenceComponent
        )

weightedCorrelationSum ::
  U.Vector Double ->
  Double ->
  Integer ->
  Either StableError Double
weightedCorrelationSum centered gammaZero aggregationPeriods =
  finiteResearchResult (sumVector weightedTerms)
  where
    observations = fromIntegral (U.length centered)
    -- aggregationPeriods는 호출자가 vector 길이보다 작게 검증하므로 Int 변환이 안전하다.
    weightedTerms = U.generate (fromInteger aggregationPeriods - 1) weightedTerm
    weightedTerm index =
      let lag = index + 1
          gammaLag =
            sumVector
              (U.zipWith (*) (U.drop lag centered) centered)
              / observations
          weight = 1.0 - fromIntegral lag / fromInteger aggregationPeriods
       in weight * (gammaLag / gammaZero)

validatedPsrInputs ::
  Double ->
  Double ->
  Integer ->
  Double ->
  Double ->
  Either StableError (Double, Double, Double, Double)
validatedPsrInputs observedSharpe benchmarkSharpe sampleSize skewness kurtosis = do
  observed <- validateFiniteScalar ResearchInputInvalid observedSharpe
  benchmark <- validateFiniteScalar ResearchInputInvalid benchmarkSharpe
  observations <- validateSampleSize sampleSize
  skew <- validateFiniteScalar MomentInvalid skewness
  pearsonKurtosis <- validateFiniteScalar MomentInvalid kurtosis
  validateMomentPair skew pearsonKurtosis
  let radicand =
        1.0
          - skew * observed
          + ((pearsonKurtosis - 1.0) / 4.0) * observed * observed
  if isNaN radicand || isInfinite radicand || radicand <= 0.0
    then Left MomentInvalid
    else Right (observed, benchmark, observations, radicand)

validateTrialProvenance :: Integer -> TrialProvenance -> Either StableError ()
validateTrialProvenance
  trialCount
  (TrialProvenance schemaVersion method rawCount effectiveCount frequency digest varianceDof)
    | schemaVersion /= "s1.4r-effective-trials-v1" = Left TrialProvenanceInvalid
    | method `notElem` ["pre_registered_independent", "externally_estimated_effective_count"] =
        Left TrialProvenanceInvalid
    | rawCount < effectiveCount || effectiveCount < 2 = Left TrialProvenanceInvalid
    | effectiveCount /= trialCount = Left TrialProvenanceInvalid
    | all (`elem` [' ', '\t', '\n', '\r']) frequency = Left TrialProvenanceInvalid
    | length digest /= 64 || any invalidHex digest = Left TrialProvenanceInvalid
    | varianceDof /= 1 = Left TrialProvenanceInvalid
    | otherwise = Right ()
  where
    invalidHex character =
      not (isHexDigit character)
        || (character >= 'A' && character <= 'F')
        || (isLower character && character > 'f')

finiteProbabilityResult :: Double -> Either StableError Double
finiteProbabilityResult value
  | isNaN value || isInfinite value = Left ResearchResultNonFinite
  | value < -tolerance || value > 1.0 + tolerance = Left ResearchResultNonFinite
  | otherwise = Right (max 0.0 (min 1.0 value))
  where
    tolerance = 64.0 * encodeFloat 1 (-52)
