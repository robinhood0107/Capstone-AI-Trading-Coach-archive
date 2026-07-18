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

import Data.Char (isHexDigit, isLower)

import qualified Data.Vector.Unboxed as U

import S14X.Core.Error
  ( StableError
      ( AggregationPeriodsInvalid,
        LikelihoodInvalid,
        MomentInvalid,
        ResearchInputInvalid,
        ResearchInputTooShort,
        ResearchResultNonFinite,
        TrialProvenanceInvalid,
        TrialVarianceInvalid
      ),
  )
import S14X.Core.Models
  ( ConditionalCoverageResult (ConditionalCoverageResult),
    IndependenceResult (IndependenceResult),
    LikelihoodResult (LikelihoodResult),
    TransitionCounts (TransitionCounts),
    TrialProvenance (TrialProvenance),
  )
import S14X.Core.NumericPrimitives
  ( chiSquareOneSurvival,
    chiSquareTwoSurvival,
    confidenceExceptionLogLikelihood,
    finiteResearchResult,
    independenceLikelihoodComponents,
    kupiecLikelihoodComponents,
    likelihoodRatio,
    likelihoodRoundoffTolerance,
    meanVector,
    normalCdf,
    normalInverseCdf,
    pureSort,
    stableWeightedMean,
    sumVector,
  )
import S14X.Core.ScalarValidation
  ( validateConfidence,
    validateFiniteScalar,
    validateMomentPair,
    validateSampleSize,
    validateSignificance,
    validateTrialCount,
  )
import S14X.Core.Validation
  ( BacktestInputs (BacktestInputs),
    transitionCounts,
    validateBacktestInputs,
    validateResearchVector,
    validateTransitionIdentifiability,
  )

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

realizedVariance :: U.Vector Double -> Either StableError Double
realizedVariance rawReturns = do
  returns <- validateResearchVector 1 rawReturns
  finiteResearchResult (sumVector (U.map (\value -> value * value) returns))

realizedVolatilityIntraday :: U.Vector Double -> Either StableError Double
realizedVolatilityIntraday rawReturns = do
  variance <- realizedVariance rawReturns
  finiteResearchResult (sqrt variance)

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
  go 1 0.0
  where
    observations = fromIntegral (U.length centered)
    go lag total
      | lag >= aggregationPeriods = finiteResearchResult total
      | otherwise =
          let offset = fromInteger lag
              gammaLag =
                sumVector
                  (U.zipWith (*) (U.drop offset centered) centered)
                  / observations
              weight = 1.0 - fromInteger lag / fromInteger aggregationPeriods
           in go (lag + 1) (total + weight * (gammaLag / gammaZero))

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
