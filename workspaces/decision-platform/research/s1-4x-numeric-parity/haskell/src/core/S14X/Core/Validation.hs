module S14X.Core.Validation
  ( BacktestInputs (..),
    transitionCounts,
    validateBacktestInputs,
    validateProductionVector,
    validateResearchVector,
    validateTransitionIdentifiability,
  )
where

import qualified Data.Vector.Unboxed as U

import S14X.Core.Error
  ( StableError
      ( ForecastShapeInvalid,
        ForecastVarNegative,
        InputEmpty,
        InputNonFinite,
        InputTooLong,
        InputTooShort,
        InsufficientSample,
        ResearchInputInvalid,
        ResearchInputTooShort
      ),
  )
import S14X.Core.Models (TransitionCounts (TransitionCounts))

-- | 손실·예측 VaR와 그 strict 초과 여부를 같은 길이로 결속한 backtest 입력이다.
-- 생성자는 검증 함수가 shape, 유한성, 비음수 forecast 계약을 통과한 뒤에만 사용한다.
data BacktestInputs = BacktestInputs
  { backtestRealizedLosses :: U.Vector Double,
    backtestForecastVars :: U.Vector Double,
    backtestExceptions :: U.Vector Int
  }
  deriving stock (Eq, Show)

-- | production Float64 벡터의 비어 있음, 최대 길이, 최소 길이, 유한성을 순서대로 검증한다.
-- 성공 시 입력 vector를 복사하지 않고 그대로 반환하며 실패는 production 'StableError'로 닫는다.
validateProductionVector :: Int -> U.Vector Double -> Either StableError (U.Vector Double)
validateProductionVector minimumLength values
  | U.null values = Left InputEmpty
  | U.length values > 100000 = Left InputTooLong
  | U.length values < minimumLength = Left InputTooShort
  | U.any nonFinite values = Left InputNonFinite
  | otherwise = Right values

-- | research Float64 벡터의 유한성을 먼저, 최소 길이를 다음으로 검증한다.
-- 오류 우선순위를 고정하기 위해 production vector validator와 합치지 않는다.
validateResearchVector :: Int -> U.Vector Double -> Either StableError (U.Vector Double)
validateResearchVector minimumLength values
  | U.any nonFinite values = Left ResearchInputInvalid
  | U.length values < minimumLength = Left ResearchInputTooShort
  | otherwise = Right values

-- | realized loss와 forecast VaR의 shape·유한성·최소 길이·forecast 비음수를 검증한다.
-- 예외는 @realized > forecast@인 경우에만 @1@로 만들며 같음은 예외가 아니다.
validateBacktestInputs ::
  Int ->
  U.Vector Double ->
  U.Vector Double ->
  Either StableError BacktestInputs
validateBacktestInputs minimumLength realizedLosses forecastVars
  | U.any nonFinite realizedLosses = Left ResearchInputInvalid
  | U.any nonFinite forecastVars = Left ResearchInputInvalid
  | U.length realizedLosses /= U.length forecastVars = Left ForecastShapeInvalid
  | U.length realizedLosses < minimumLength = Left ResearchInputTooShort
  | U.any (< 0.0) forecastVars = Left ForecastVarNegative
  | otherwise =
      Right
        ( BacktestInputs
            realizedLosses
            forecastVars
            (U.zipWith (\realized forecast -> if realized > forecast then 1 else 0) realizedLosses forecastVars)
        )

-- | 0/1 예외 벡터의 인접 상태쌍을 @n00,n01,n10,n11@로 집계한다.
-- 검증되지 않은 값은 세지 않으므로 public backtest validator가 만든 벡터에만 적용한다.
transitionCounts :: U.Vector Int -> TransitionCounts
transitionCounts exceptions =
  U.foldl' countPair (TransitionCounts 0 0 0 0) pairs
  where
    pairs = U.zip exceptions (U.drop 1 exceptions)
    countPair (TransitionCounts n00 n01 n10 n11) pair =
      case pair of
        (0, 0) -> TransitionCounts (n00 + 1) n01 n10 n11
        (0, 1) -> TransitionCounts n00 (n01 + 1) n10 n11
        (1, 0) -> TransitionCounts n00 n01 (n10 + 1) n11
        (1, 1) -> TransitionCounts n00 n01 n10 (n11 + 1)
        _ -> TransitionCounts n00 n01 n10 n11

-- | 이전 상태가 0인 행과 1인 행이 모두 관측돼 전이확률을 식별할 수 있는지 검사한다.
-- 어느 한 행도 없으면 likelihood 계산 전에 'InsufficientSample'로 닫는다.
validateTransitionIdentifiability :: TransitionCounts -> Either StableError ()
validateTransitionIdentifiability (TransitionCounts n00 n01 n10 n11)
  | n00 + n01 == 0 || n10 + n11 == 0 = Left InsufficientSample
  | otherwise = Right ()

nonFinite :: Double -> Bool
nonFinite value = isNaN value || isInfinite value
