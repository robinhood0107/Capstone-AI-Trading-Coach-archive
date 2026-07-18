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

data BacktestInputs = BacktestInputs
  { backtestRealizedLosses :: U.Vector Double,
    backtestForecastVars :: U.Vector Double,
    backtestExceptions :: U.Vector Int
  }
  deriving stock (Eq, Show)

validateProductionVector :: Int -> U.Vector Double -> Either StableError (U.Vector Double)
validateProductionVector minimumLength values
  | U.null values = Left InputEmpty
  | U.length values > 100000 = Left InputTooLong
  | U.length values < minimumLength = Left InputTooShort
  | U.any nonFinite values = Left InputNonFinite
  | otherwise = Right values

validateResearchVector :: Int -> U.Vector Double -> Either StableError (U.Vector Double)
validateResearchVector minimumLength values
  | U.any nonFinite values = Left ResearchInputInvalid
  | U.length values < minimumLength = Left ResearchInputTooShort
  | otherwise = Right values

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

validateTransitionIdentifiability :: TransitionCounts -> Either StableError ()
validateTransitionIdentifiability (TransitionCounts n00 n01 n10 n11)
  | n00 + n01 == 0 || n10 + n11 == 0 = Left InsufficientSample
  | otherwise = Right ()

nonFinite :: Double -> Bool
nonFinite value = isNaN value || isInfinite value
