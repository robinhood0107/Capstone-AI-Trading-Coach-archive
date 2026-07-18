module S14X.Core.ProductionMetrics
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
where

import Numeric.SpecFunctions (expm1)

import qualified Data.Vector.Unboxed as U

import S14X.Core.Error
  ( StableError
      ( ConfidenceInvalid,
        DenominatorZero,
        EquityInitialNonPositive,
        EquityNegative,
        PeriodsPerYearInvalid,
        PricesNonPositive,
        ResultNonFinite,
        RiskFreeRateInvalid,
        SimpleReturnBelowMinusOne,
        TailEmpty,
        TargetReturnInvalid
      ),
  )
import S14X.Core.NumericPrimitives
  ( hf7Quantile,
    meanVector,
    sampleVariance,
    sumVector,
  )
import S14X.Core.ScalarValidation
  ( ensureFinite,
    validateConfidence,
    validateFiniteScalar,
    validatePositiveInteger,
  )
import S14X.Core.Validation (validateProductionVector)

simpleReturns :: U.Vector Double -> Either StableError (U.Vector Double)
simpleReturns rawPrices = do
  prices <- validateProductionVector 2 rawPrices
  if U.any (<= 0.0) prices
    then Left PricesNonPositive
    else ensureFiniteVector (U.zipWith (\previous current -> current / previous - 1.0) prices (U.drop 1 prices))

logReturns :: U.Vector Double -> Either StableError (U.Vector Double)
logReturns rawPrices = do
  prices <- validateProductionVector 2 rawPrices
  if U.any (<= 0.0) prices
    then Left PricesNonPositive
    else
      ensureFiniteVector
        (U.zipWith (\previous current -> log current - log previous) prices (U.drop 1 prices))

cumulativeReturn :: U.Vector Double -> Either StableError Double
cumulativeReturn rawReturns = do
  returns <- validateProductionVector 1 rawReturns
  if U.any (< -1.0) returns
    then Left SimpleReturnBelowMinusOne
    else
      if U.any (== -1.0) returns
        then Right (-1.0)
        else ensureFinite ResultNonFinite (U.foldl' (\total value -> total * (1.0 + value)) 1.0 returns - 1.0)

cagr :: U.Vector Double -> Integer -> Either StableError Double
cagr rawPrices periodsPerYear = do
  prices <- validateProductionVector 2 rawPrices
  periods <- validatePositiveInteger PeriodsPerYearInvalid periodsPerYear
  if U.any (<= 0.0) prices
    then Left PricesNonPositive
    else
      case (prices U.!? 0, prices U.!? (U.length prices - 1)) of
        (Just initialPrice, Just finalPrice) ->
          let annualization = periods / fromIntegral (U.length prices - 1)
              logGrowth = log finalPrice - log initialPrice
           in ensureFinite ResultNonFinite (expm1 (annualization * logGrowth))
        _ -> Left ResultNonFinite

realizedVolatility :: U.Vector Double -> Either StableError Double
realizedVolatility rawReturns = do
  returns <- validateProductionVector 2 rawReturns
  ensureFinite ResultNonFinite (sqrt (sampleVariance returns))

annualizedVolatility :: U.Vector Double -> Integer -> Either StableError Double
annualizedVolatility rawReturns periodsPerYear = do
  returns <- validateProductionVector 2 rawReturns
  periods <- validatePositiveInteger PeriodsPerYearInvalid periodsPerYear
  ensureFinite ResultNonFinite (sqrt (sampleVariance returns) * sqrt periods)

maxDrawdown :: U.Vector Double -> Either StableError Double
maxDrawdown rawEquity = do
  equity <- validateProductionVector 1 rawEquity
  case equity U.!? 0 of
    Nothing -> Left ResultNonFinite
    Just initial
      | initial <= 0.0 -> Left EquityInitialNonPositive
      | U.any (< 0.0) (U.drop 1 equity) -> Left EquityNegative
      | otherwise ->
          let (_, drawdown) =
                U.foldl'
                  ( \(peak, minimumDrawdown) value ->
                      let nextPeak = max peak value
                          currentDrawdown = value / nextPeak - 1.0
                       in (nextPeak, min minimumDrawdown currentDrawdown)
                  )
                  (initial, 0.0)
                  equity
           in ensureFinite ResultNonFinite drawdown

sharpeRatio ::
  U.Vector Double ->
  Double ->
  Integer ->
  Either StableError Double
sharpeRatio rawReturns riskFreeRate periodsPerYear = do
  returns <- validateProductionVector 2 rawReturns
  riskFree <- validateFiniteScalar RiskFreeRateInvalid riskFreeRate
  periods <- validatePositiveInteger PeriodsPerYearInvalid periodsPerYear
  let excess = U.map (\value -> value - riskFree) returns
      denominator = sqrt (sampleVariance excess)
  if denominator == 0.0
    then Left DenominatorZero
    else ensureFinite ResultNonFinite (meanVector excess / denominator * sqrt periods)

sortinoRatio ::
  U.Vector Double ->
  Double ->
  Integer ->
  Either StableError Double
sortinoRatio rawReturns targetReturn periodsPerYear = do
  returns <- validateProductionVector 2 rawReturns
  target <- validateFiniteScalar TargetReturnInvalid targetReturn
  periods <- validatePositiveInteger PeriodsPerYearInvalid periodsPerYear
  let excess = U.map (\value -> value - target) returns
      downside = U.map (`min` 0.0) excess
      denominator = sqrt (sumVector (U.map (\value -> value * value) downside) / fromIntegral (U.length downside))
  if denominator == 0.0
    then Left DenominatorZero
    else ensureFinite ResultNonFinite (meanVector excess / denominator * sqrt periods)

historicalVar :: U.Vector Double -> Double -> Either StableError Double
historicalVar rawReturns confidence = do
  returns <- validateProductionVector 2 rawReturns
  probability <- validateConfidence ConfidenceInvalid confidence
  hf7Quantile returns (1.0 - probability)

historicalCvar :: U.Vector Double -> Double -> Either StableError Double
historicalCvar rawReturns confidence = do
  returns <- validateProductionVector 2 rawReturns
  probability <- validateConfidence ConfidenceInvalid confidence
  threshold <- hf7Quantile returns (1.0 - probability)
  let tailValues = U.filter (<= threshold) returns
  if U.null tailValues
    then Left TailEmpty
    else ensureFinite ResultNonFinite (meanVector tailValues)

ensureFiniteVector :: U.Vector Double -> Either StableError (U.Vector Double)
ensureFiniteVector values
  | U.any (\value -> isNaN value || isInfinite value) values = Left ResultNonFinite
  | otherwise = Right values
