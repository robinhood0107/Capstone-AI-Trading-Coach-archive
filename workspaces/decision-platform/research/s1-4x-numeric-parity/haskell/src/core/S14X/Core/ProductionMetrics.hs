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

import           Numeric.SpecFunctions (expm1)

import qualified Data.Vector.Unboxed as U

import           S14X.Core.Error (StableError (ConfidenceInvalid, DenominatorZero, EquityInitialNonPositive, EquityNegative, PeriodsPerYearInvalid, PricesNonPositive, ResultNonFinite, RiskFreeRateInvalid, SimpleReturnBelowMinusOne, TailEmpty, TargetReturnInvalid))
import           S14X.Core.NumericPrimitives (hf7Quantile, meanVector, sampleVariance, sumVector)
import           S14X.Core.ScalarValidation (ensureFinite, validateConfidence, validateFiniteScalar,
                                             validatePositiveInteger)
import           S14X.Core.Validation (validateProductionVector)

-- | 양수 가격 벡터를 인접 단순수익률 벡터로 변환한다.
-- 길이·유한성·양수 계약 위반과 비유한 결과는 stable production 오류로 반환한다.
simpleReturns :: U.Vector Double -> Either StableError (U.Vector Double)
simpleReturns rawPrices = do
  prices <- validateProductionVector 2 rawPrices
  if U.any (<= 0.0) prices
    then Left PricesNonPositive
    else ensureFiniteVector (U.zipWith (\previous current -> current / previous - 1.0) prices (U.drop 1 prices))

-- | 양수 가격 벡터의 인접 로그수익률을 계산한다.
-- 입력을 복사하거나 변경하지 않으며 가격·결과 검증 실패를 'StableError'로 닫는다.
logReturns :: U.Vector Double -> Either StableError (U.Vector Double)
logReturns rawPrices = do
  prices <- validateProductionVector 2 rawPrices
  if U.any (<= 0.0) prices
    then Left PricesNonPositive
    else
      ensureFiniteVector
        (U.zipWith (\previous current -> log current - log previous) prices (U.drop 1 prices))

-- | 단순수익률 경로의 복리 누적수익률을 반환한다.
-- @-1@은 전액 손실로 허용하지만 그보다 작은 수익률과 비유한 결과는 거부한다.
cumulativeReturn :: U.Vector Double -> Either StableError Double
cumulativeReturn rawReturns = do
  returns <- validateProductionVector 1 rawReturns
  if U.any (< -1.0) returns
    then Left SimpleReturnBelowMinusOne
    else
      if U.any (== -1.0) returns
        then Right (-1.0)
        else ensureFinite ResultNonFinite (U.foldl' (\total value -> total * (1.0 + value)) 1.0 returns - 1.0)

-- | 양수 가격 경로와 연간 관측 수로 연복리성장률을 계산한다.
-- arbitrary-size 'Integer' 주기는 양수여야 하며 최종 Float64 overflow는 결과 오류가 된다.
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

-- | 최소 두 수익률의 표본표준편차를 실현 변동성으로 반환한다.
-- production 벡터 검증과 결과 유한성 계약을 먼저 적용한다.
realizedVolatility :: U.Vector Double -> Either StableError Double
realizedVolatility rawReturns = do
  returns <- validateProductionVector 2 rawReturns
  ensureFinite ResultNonFinite (sqrt (sampleVariance returns))

-- | 실현 변동성을 양의 연간 관측 수의 제곱근으로 연율화한다.
-- 입력·주기·Float64 결과 오류는 정해진 production 오류 우선순위로 반환한다.
annualizedVolatility :: U.Vector Double -> Integer -> Either StableError Double
annualizedVolatility rawReturns periodsPerYear = do
  returns <- validateProductionVector 2 rawReturns
  periods <- validatePositiveInteger PeriodsPerYearInvalid periodsPerYear
  ensureFinite ResultNonFinite (sqrt (sampleVariance returns) * sqrt periods)

-- | 비음수 자산가치 경로에서 running peak 대비 최저 drawdown을 계산한다.
-- 최초 값은 양수여야 하고 이후 음수 값과 비유한 입력은 stable 오류로 거부한다.
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

-- | 수익률, 무위험수익률, 양의 연간 주기로 연율화 Sharpe ratio를 계산한다.
-- 표본분산이 0이면 나눗셈 대신 'DenominatorZero'를 반환한다.
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

-- | 수익률, 목표수익률, 양의 연간 주기로 Sortino ratio를 계산한다.
-- downside RMS가 0이면 결과를 만들지 않고 'DenominatorZero'로 닫는다.
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

-- | production 수익률의 하위 꼬리를 HF7 quantile로 평가해 historical VaR를 반환한다.
-- confidence는 열린 구간 @(0,1)@이어야 한다.
historicalVar :: U.Vector Double -> Double -> Either StableError Double
historicalVar rawReturns confidence = do
  returns <- validateProductionVector 2 rawReturns
  probability <- validateConfidence ConfidenceInvalid confidence
  hf7Quantile returns (1.0 - probability)

-- | HF7 VaR 이하 관측값의 보상합 평균으로 historical CVaR를 계산한다.
-- 비어 있는 꼬리와 비유한 결과는 각각 명시된 stable 오류가 된다.
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
