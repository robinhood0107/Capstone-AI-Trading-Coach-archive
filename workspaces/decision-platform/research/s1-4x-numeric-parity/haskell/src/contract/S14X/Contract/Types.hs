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

-- | strict parser가 decoded duplicate key와 number 원문을 보존하는 JSON 표현이다.
-- transport validation이 끝나기 전에는 Aeson object로 축약하지 않는다.
data RawJson
  = RawObject [(Text, RawJson)]
  | RawArray [RawJson]
  | RawString Text
  | RawNumber Text
  | RawBool Bool
  | RawNull
  deriving stock (Eq, Show)

-- | Gate 1 function registry의 20개 numeric kernel identity를 닫힌 순서로 표현한다.
-- 'Enum' 순서는 property fixture identity에 사용되므로 registry 변경 없이 재배열하지 않는다.
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

-- | 한 fixture와 한 function, validated raw argument map을 결속한 실행 요청이다.
-- fixture ID와 argument field 계약은 constructor 사용 전에 process parser가 검사한다.
data CaseRequest = CaseRequest
  { caseFixtureId :: Text,
    caseFunctionId :: FunctionId,
    caseArguments :: Map Text RawJson
  }
  deriving stock (Eq, Show)

-- | exact schema version·request ID와 최대 4096개 고유 fixture case를 담는다.
-- 외부 JSON에서 직접 만들지 않고 'parseRequest' 성공 결과로만 실행한다.
data RequestBatch = RequestBatch
  { requestSchemaVersion :: Text,
    requestIdentifier :: Text,
    requestCases :: [CaseRequest]
  }
  deriving stock (Eq, Show)

-- | case별 numeric success 또는 registry에 있는 stable semantic failure를 표현한다.
-- transport failure는 batch 결과에 섞지 않고 별도 'TransportError'로 반환한다.
data CaseResult
  = CaseSuccess Text FunctionId NumericResult
  | CaseFailure Text FunctionId StableError
  deriving stock (Eq, Show)

-- | request ID, compiler 결속 implementation label, case 결과 순서를 보존한다.
-- encoder는 입력 case 순서와 exact function identity를 변경하지 않는다.
data ResultBatch = ResultBatch
  { resultRequestId :: Text,
    resultImplementation :: Text,
    resultCases :: [CaseResult]
  }
  deriving stock (Eq, Show)

-- | request·manifest·binary·internal shell failure의 frozen transport 분류다.
-- numeric 'StableError'와 분리돼 process exit code와 error envelope를 결정한다.
data TransportCode
  = RequestInvalid
  | ManifestInvalid
  | BinaryInvalid
  | InternalError
  deriving stock (Eq, Ord, Show)

-- | transport code와 허용된 request·fixture·field context만 담는 오류 envelope다.
-- 원본 path, payload, exception, credential은 보안상 이 타입에 포함하지 않는다.
data TransportError = TransportError
  { transportCode :: TransportCode,
    transportRequestId :: Maybe Text,
    transportFixtureId :: Maybe Text,
    transportField :: Maybe Text
  }
  deriving stock (Eq, Show)

-- | 'FunctionId'를 Gate 1 registry의 lowercase snake-case wire identity로 변환한다.
-- request parsing과 result encoding이 동일한 mapping을 공유한다.
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
