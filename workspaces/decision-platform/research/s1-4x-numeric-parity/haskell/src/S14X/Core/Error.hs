{-# LANGUAGE Safe #-}

module S14X.Core.Error
  ( StableError (..),
    allStableErrors,
    stableErrorCode,
  )
where

import Control.DeepSeq (NFData (rnf))

-- 공개 오류 ADT는 Gate 1 registry의 19개 production 오류와 13개 research 오류를 닫힌
-- 집합으로 보존한다. shell은 이 생성자 외 임의 문자열을 numeric 오류로 내보낼 수 없다.
data StableError
  = InputTypeInvalid
  | InputShapeInvalid
  | InputEmpty
  | InputTooShort
  | InputTooLong
  | InputBoolInvalid
  | InputComplexInvalid
  | InputNonFinite
  | PricesNonPositive
  | EquityInitialNonPositive
  | EquityNegative
  | SimpleReturnBelowMinusOne
  | PeriodsPerYearInvalid
  | RiskFreeRateInvalid
  | TargetReturnInvalid
  | ConfidenceInvalid
  | DenominatorZero
  | TailEmpty
  | ResultNonFinite
  | ResearchInputInvalid
  | ResearchInputTooShort
  | AggregationPeriodsInvalid
  | MomentInvalid
  | TrialCountInvalid
  | TrialVarianceInvalid
  | TrialProvenanceInvalid
  | SignificanceInvalid
  | ForecastShapeInvalid
  | ForecastVarNegative
  | InsufficientSample
  | LikelihoodInvalid
  | ResearchResultNonFinite
  deriving stock (Bounded, Enum, Eq, Ord, Show)

instance NFData StableError where
  rnf stableError = stableError `seq` ()

allStableErrors :: [StableError]
allStableErrors = [minBound .. maxBound]

stableErrorCode :: StableError -> String
stableErrorCode stableError =
  case stableError of
    InputTypeInvalid -> "input_type_invalid"
    InputShapeInvalid -> "input_shape_invalid"
    InputEmpty -> "input_empty"
    InputTooShort -> "input_too_short"
    InputTooLong -> "input_too_long"
    InputBoolInvalid -> "input_bool_invalid"
    InputComplexInvalid -> "input_complex_invalid"
    InputNonFinite -> "input_non_finite"
    PricesNonPositive -> "prices_non_positive"
    EquityInitialNonPositive -> "equity_initial_non_positive"
    EquityNegative -> "equity_negative"
    SimpleReturnBelowMinusOne -> "simple_return_below_minus_one"
    PeriodsPerYearInvalid -> "periods_per_year_invalid"
    RiskFreeRateInvalid -> "risk_free_rate_invalid"
    TargetReturnInvalid -> "target_return_invalid"
    ConfidenceInvalid -> "confidence_invalid"
    DenominatorZero -> "denominator_zero"
    TailEmpty -> "tail_empty"
    ResultNonFinite -> "result_non_finite"
    ResearchInputInvalid -> "research_input_invalid"
    ResearchInputTooShort -> "research_input_too_short"
    AggregationPeriodsInvalid -> "aggregation_periods_invalid"
    MomentInvalid -> "moment_invalid"
    TrialCountInvalid -> "trial_count_invalid"
    TrialVarianceInvalid -> "trial_variance_invalid"
    TrialProvenanceInvalid -> "trial_provenance_invalid"
    SignificanceInvalid -> "significance_invalid"
    ForecastShapeInvalid -> "forecast_shape_invalid"
    ForecastVarNegative -> "forecast_var_negative"
    InsufficientSample -> "insufficient_sample"
    LikelihoodInvalid -> "likelihood_invalid"
    ResearchResultNonFinite -> "research_result_non_finite"
