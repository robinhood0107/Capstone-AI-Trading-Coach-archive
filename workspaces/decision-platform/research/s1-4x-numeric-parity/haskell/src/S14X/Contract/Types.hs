module S14X.Contract.Types
  ( CaseRequest (..),
    CaseResult (..),
    FunctionId (..),
    RawJson (..),
    RequestBatch (..),
    ResultBatch (..),
    TransportCode (..),
    TransportError (..),
    functionIdText,
  )
where

import Data.Map.Strict (Map)
import Data.Text (Text)

import S14X.Core.Error (StableError)
import S14X.Core.Models (NumericResult)

data RawJson
  = RawObject [(Text, RawJson)]
  | RawArray [RawJson]
  | RawString Text
  | RawNumber Text
  | RawBool Bool
  | RawNull
  deriving stock (Eq, Show)

data FunctionId
  = SimpleReturns
  | LogReturns
  | CumulativeReturn
  | Cagr
  | RealizedVolatility
  | AnnualizedVolatility
  | MaxDrawdown
  | SharpeRatio
  | SortinoRatio
  | HistoricalVar
  | HistoricalCvar
  | HistoricalExpectedShortfall
  | RealizedVariance
  | RealizedVolatilityIntraday
  | LoAdjustedSharpeRatio
  | ProbabilisticSharpeRatio
  | DeflatedSharpeRatio
  | KupiecUnconditionalCoverageTest
  | ChristoffersenIndependenceTest
  | ChristoffersenConditionalCoverageTest
  deriving stock (Bounded, Enum, Eq, Ord, Show)

data CaseRequest = CaseRequest
  { caseFixtureId :: Text,
    caseFunctionId :: FunctionId,
    caseArguments :: Map Text RawJson
  }
  deriving stock (Eq, Show)

data RequestBatch = RequestBatch
  { requestSchemaVersion :: Text,
    requestIdentifier :: Text,
    requestCases :: [CaseRequest]
  }
  deriving stock (Eq, Show)

data CaseResult
  = CaseSuccess Text FunctionId NumericResult
  | CaseFailure Text FunctionId StableError
  deriving stock (Eq, Show)

data ResultBatch = ResultBatch
  { resultRequestId :: Text,
    resultImplementation :: Text,
    resultCases :: [CaseResult]
  }
  deriving stock (Eq, Show)

data TransportCode
  = RequestInvalid
  | ManifestInvalid
  | BinaryInvalid
  | InternalError
  deriving stock (Eq, Ord, Show)

data TransportError = TransportError
  { transportCode :: TransportCode,
    transportRequestId :: Maybe Text,
    transportFixtureId :: Maybe Text,
    transportField :: Maybe Text
  }
  deriving stock (Eq, Show)

functionIdText :: FunctionId -> Text
functionIdText functionId =
  case functionId of
    SimpleReturns -> "simple_returns"
    LogReturns -> "log_returns"
    CumulativeReturn -> "cumulative_return"
    Cagr -> "cagr"
    RealizedVolatility -> "realized_volatility"
    AnnualizedVolatility -> "annualized_volatility"
    MaxDrawdown -> "max_drawdown"
    SharpeRatio -> "sharpe_ratio"
    SortinoRatio -> "sortino_ratio"
    HistoricalVar -> "historical_var"
    HistoricalCvar -> "historical_cvar"
    HistoricalExpectedShortfall -> "historical_expected_shortfall"
    RealizedVariance -> "realized_variance"
    RealizedVolatilityIntraday -> "realized_volatility_intraday"
    LoAdjustedSharpeRatio -> "lo_adjusted_sharpe_ratio"
    ProbabilisticSharpeRatio -> "probabilistic_sharpe_ratio"
    DeflatedSharpeRatio -> "deflated_sharpe_ratio"
    KupiecUnconditionalCoverageTest -> "kupiec_unconditional_coverage_test"
    ChristoffersenIndependenceTest -> "christoffersen_independence_test"
    ChristoffersenConditionalCoverageTest -> "christoffersen_conditional_coverage_test"
