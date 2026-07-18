module S14X.Contract.Process
  ( encodeResultBatch,
    encodeTransportError,
    implementationLabel,
    parseRequest,
    runRequest,
    sha256Hex,
  )
where

import           Control.Exception (IOException, try)
import           Data.Aeson (Value, encode, object, toJSON, (.=))
import           Data.Aeson.Types (Pair)
import           Data.Binary.Get (getDoublele, runGet)
import           Data.ByteString (ByteString)
import           Data.Digest.Pure.SHA (sha256, showDigest)
import           Data.Either (lefts, rights)
import           Data.Map.Strict (Map)
import           Data.Set (Set)
import           Data.Text (Text)
import           Data.Version (showVersion)
import           System.Directory (canonicalizePath, doesDirectoryExist, doesFileExist,
                                   pathIsSymbolicLink)
import           System.FilePath ((</>))
import           System.Info (compilerName, fullCompilerVersion)

import qualified Data.Aeson.Key as Key
import qualified Data.ByteString.Char8 as BS8
import qualified Data.ByteString.Lazy as LBS
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import qualified Data.Text as Text
import qualified Data.Vector.Unboxed as U

import           S14X.Contract.StrictJson (objectMap, parseStrictJson, rawDouble, rawInteger)
import           S14X.Contract.Types (CaseRequest (CaseRequest),
                                      CaseResult (CaseFailure, CaseSuccess),
                                      FunctionId (AnnualizedVolatility, Cagr, ChristoffersenConditionalCoverageTest, ChristoffersenIndependenceTest, CumulativeReturn, DeflatedSharpeRatio, HistoricalCvar, HistoricalExpectedShortfall, HistoricalVar, KupiecUnconditionalCoverageTest, LoAdjustedSharpeRatio, LogReturns, MaxDrawdown, ProbabilisticSharpeRatio, RealizedVariance, RealizedVolatility, RealizedVolatilityIntraday, SharpeRatio, SimpleReturns, SortinoRatio),
                                      RawJson (RawArray, RawBool, RawNumber, RawObject, RawString),
                                      RequestBatch (RequestBatch), ResultBatch (ResultBatch),
                                      TransportCode (BinaryInvalid, InternalError, ManifestInvalid, RequestInvalid),
                                      TransportError (TransportError), functionIdText)
import           S14X.Core.AdvancedRisk (christoffersenConditionalCoverageTest,
                                         christoffersenIndependenceTest, deflatedSharpeRatio,
                                         historicalExpectedShortfall,
                                         kupiecUnconditionalCoverageTest, loAdjustedSharpeRatio,
                                         probabilisticSharpeRatio, realizedVariance,
                                         realizedVolatilityIntraday)
import           S14X.Core.Error (StableError (AggregationPeriodsInvalid, ConfidenceInvalid, InputBoolInvalid, InputNonFinite, InputShapeInvalid, InputTypeInvalid, MomentInvalid, PeriodsPerYearInvalid, ResearchInputInvalid, RiskFreeRateInvalid, SignificanceInvalid, TargetReturnInvalid, TrialCountInvalid, TrialProvenanceInvalid, TrialVarianceInvalid),
                                  stableErrorCode)
import           S14X.Core.Models (ConditionalCoverageResult (ConditionalCoverageResult),
                                   IndependenceResult (IndependenceResult),
                                   LikelihoodResult (LikelihoodResult),
                                   NumericResult (ConditionalCoverageRecord, IndependenceRecord, LikelihoodRecord, ScalarResult, VectorResult),
                                   TransitionCounts (TransitionCounts),
                                   TrialProvenance (TrialProvenance))
import           S14X.Core.ProductionMetrics (annualizedVolatility, cagr, cumulativeReturn,
                                              historicalCvar, historicalVar, logReturns,
                                              maxDrawdown, realizedVolatility, sharpeRatio,
                                              simpleReturns, sortinoRatio)

-- | UTF-8 JSON request를 strict duplicate/number grammar와 frozen 20-function 계약으로 검증한다.
-- 실패 시 payload나 local path를 노출하지 않는 typed 'TransportError'만 반환한다.
parseRequest :: ByteString -> Either TransportError RequestBatch
parseRequest payload = do
  root <- mapRequestFailure (parseStrictJson payload)
  envelope <- mapRequestFailure (objectMap root)
  requireExactFields envelope envelopeFields Nothing
  schemaVersion <- requestText envelope "schemaVersion" Nothing
  requestId <- requestText envelope "requestId" Nothing
  if schemaVersion /= "s1.4x-request-v1" || not (validIdentifier requestId)
    then Left (requestFailure (Just requestId) Nothing Nothing)
    else do
      rawCases <-
        case Map.lookup "cases" envelope of
          Just (RawArray values)
            | not (null values) && length values <= 4096 -> Right values
          _ -> Left (requestFailure (Just requestId) Nothing (Just "cases"))
      cases <- traverse (parseCase requestId) rawCases
      let fixtureIds = fmap caseFixtureText cases
      if length fixtureIds /= Set.size (Set.fromList fixtureIds)
        then Left (requestFailure (Just requestId) Nothing (Just "fixtureId"))
        else Right (RequestBatch schemaVersion requestId cases)

parseCase :: Text -> RawJson -> Either TransportError CaseRequest
parseCase requestId rawCase = do
  fields <- mapRequestFailureWith requestId Nothing (objectMap rawCase)
  requireSubsetFields fields caseFields (Just requestId)
  fixtureId <- requestText fields "fixtureId" (Just requestId)
  functionText <- requestText fields "functionId" (Just requestId)
  functionId <-
    maybe
      (Left (requestFailure (Just requestId) (Just fixtureId) (Just "functionId")))
      Right
      (parseFunctionId functionText)
  if not (validIdentifier fixtureId)
    then Left (requestFailure (Just requestId) (Just fixtureId) (Just "fixtureId"))
    else do
      rawArguments <-
        maybe
          (Left (requestFailure (Just requestId) (Just fixtureId) (Just "arguments")))
          Right
          (Map.lookup "arguments" fields)
      arguments <-
        mapRequestFailureWith requestId (Just fixtureId) (objectMap rawArguments)
      let (required, optional) = argumentContract functionId
          actual = Map.keysSet arguments
      if Map.size arguments < 1
        || Map.size arguments > 8
        || not (required `Set.isSubsetOf` actual)
        || not (actual `Set.isSubsetOf` (required `Set.union` optional))
        then Left (requestFailure (Just requestId) (Just fixtureId) (Just "arguments"))
        else do
          validateOptionalCaseFields requestId fixtureId fields
          Right (CaseRequest fixtureId functionId arguments)

validateOptionalCaseFields ::
  Text ->
  Text ->
  Map Text RawJson ->
  Either TransportError ()
validateOptionalCaseFields requestId fixtureId fields = do
  case Map.lookup "expectedSemanticError" fields of
    Nothing -> Right ()
    Just (RawString code)
      | validFieldIdentifier code -> Right ()
    _ ->
      Left
        (requestFailure (Just requestId) (Just fixtureId) (Just "expectedSemanticError"))
  case Map.lookup "toleranceClass" fields of
    Nothing -> Right ()
    Just (RawString value)
      | value `elem` ["handPaper", "largeProperty"] -> Right ()
    _ -> Left (requestFailure (Just requestId) (Just fixtureId) (Just "toleranceClass"))

-- | validated request를 trusted fixture root에서 순서대로 실행해 원자 publish 전 batch를 만든다.
-- manifest·binary 경계 실패는 첫 transport 오류에서 중단하고 semantic 오류는 case 결과로 보존한다.
runRequest :: FilePath -> RequestBatch -> IO (Either TransportError ResultBatch)
runRequest fixtureRoot (RequestBatch _ requestId cases) = do
  evaluated <- traverse (runCase fixtureRoot) cases
  pure
    ( case firstTransportFailure evaluated of
        Just (TransportError code _ fixtureId field) ->
          Left
            (TransportError code (Just requestId) fixtureId field)
        Nothing ->
          Right
            ( ResultBatch
                requestId
                implementationLabel
                (rights evaluated)
            )
    )

-- | 현재 GHC compiler name과 full version을 result의 implementation identity로 만든다.
-- authoritative와 compatibility replay를 report에서 혼동하지 않도록 runtime compiler에 결속한다.
implementationLabel :: Text
implementationLabel =
  Text.pack ("haskell-" <> compilerName <> "-" <> showVersion fullCompilerVersion)

runCase :: FilePath -> CaseRequest -> IO (Either TransportError CaseResult)
runCase fixtureRoot testCase@(CaseRequest fixtureId functionId _) = do
  computed <-
    case functionId of
      SimpleReturns ->
        withProductionVector fixtureRoot testCase "prices" (fmap VectorResult . simpleReturns)
      LogReturns ->
        withProductionVector fixtureRoot testCase "prices" (fmap VectorResult . logReturns)
      CumulativeReturn ->
        withProductionVector
          fixtureRoot
          testCase
          "returns"
          (fmap ScalarResult . cumulativeReturn)
      Cagr ->
        withProductionVector fixtureRoot testCase "prices" $ \values ->
          fmap ScalarResult $ do
            periods <- integerArgument testCase "periods_per_year" (Just 252) PeriodsPerYearInvalid
            cagr values periods
      RealizedVolatility ->
        withProductionVector
          fixtureRoot
          testCase
          "log_returns"
          (fmap ScalarResult . realizedVolatility)
      AnnualizedVolatility ->
        withProductionVector fixtureRoot testCase "log_returns" $ \values ->
          fmap ScalarResult $ do
            periods <- integerArgument testCase "periods_per_year" (Just 252) PeriodsPerYearInvalid
            annualizedVolatility values periods
      MaxDrawdown ->
        withProductionVector
          fixtureRoot
          testCase
          "equity_curve"
          (fmap ScalarResult . maxDrawdown)
      SharpeRatio ->
        withProductionVector fixtureRoot testCase "returns" $ \values ->
          fmap ScalarResult $ do
            riskFree <- realArgument testCase "risk_free_rate" (Just 0.0) RiskFreeRateInvalid
            periods <- integerArgument testCase "periods_per_year" (Just 252) PeriodsPerYearInvalid
            sharpeRatio values riskFree periods
      SortinoRatio ->
        withProductionVector fixtureRoot testCase "returns" $ \values ->
          fmap ScalarResult $ do
            target <- realArgument testCase "target_return" (Just 0.0) TargetReturnInvalid
            periods <- integerArgument testCase "periods_per_year" (Just 252) PeriodsPerYearInvalid
            sortinoRatio values target periods
      HistoricalVar ->
        withProductionVector fixtureRoot testCase "returns" $ \values ->
          fmap ScalarResult $ do
            confidence <- realArgument testCase "confidence" (Just 0.95) ConfidenceInvalid
            historicalVar values confidence
      HistoricalCvar ->
        withProductionVector fixtureRoot testCase "returns" $ \values ->
          fmap ScalarResult $ do
            confidence <- realArgument testCase "confidence" (Just 0.95) ConfidenceInvalid
            historicalCvar values confidence
      HistoricalExpectedShortfall ->
        withResearchVector fixtureRoot testCase "losses" $ \values ->
          fmap ScalarResult $ do
            confidence <- realArgument testCase "confidence" (Just 0.95) ResearchInputInvalid
            historicalExpectedShortfall values confidence
      RealizedVariance ->
        withResearchVector
          fixtureRoot
          testCase
          "intraday_log_returns"
          (fmap ScalarResult . realizedVariance)
      RealizedVolatilityIntraday ->
        withResearchVector
          fixtureRoot
          testCase
          "intraday_log_returns"
          (fmap ScalarResult . realizedVolatilityIntraday)
      LoAdjustedSharpeRatio ->
        withResearchVector fixtureRoot testCase "returns" $ \values ->
          fmap ScalarResult $ do
            periods <-
              integerArgument testCase "aggregation_periods" Nothing AggregationPeriodsInvalid
            riskFree <- realArgument testCase "risk_free_rate" (Just 0.0) ResearchInputInvalid
            loAdjustedSharpeRatio values periods riskFree
      ProbabilisticSharpeRatio ->
        pure
          ( Right
              ( fmap ScalarResult $ do
                  observed <-
                    realArgument testCase "observed_sharpe" Nothing ResearchInputInvalid
                  benchmark <-
                    realArgument testCase "benchmark_sharpe" Nothing ResearchInputInvalid
                  sample <- integerArgument testCase "sample_size" Nothing ResearchInputInvalid
                  skewness <- realArgument testCase "skewness" Nothing MomentInvalid
                  kurtosis <- realArgument testCase "kurtosis" Nothing MomentInvalid
                  probabilisticSharpeRatio observed benchmark sample skewness kurtosis
              )
          )
      DeflatedSharpeRatio ->
        pure
          ( Right
              ( fmap ScalarResult $ do
                  observed <-
                    realArgument testCase "observed_sharpe" Nothing ResearchInputInvalid
                  sample <- integerArgument testCase "sample_size" Nothing ResearchInputInvalid
                  skewness <- realArgument testCase "skewness" Nothing MomentInvalid
                  kurtosis <- realArgument testCase "kurtosis" Nothing MomentInvalid
                  trialCount <-
                    integerArgument testCase "trial_count" Nothing TrialCountInvalid
                  variance <-
                    realArgument
                      testCase
                      "sharpe_estimate_variance"
                      Nothing
                      TrialVarianceInvalid
                  provenance <- provenanceArgument testCase
                  deflatedSharpeRatio
                    observed
                    sample
                    skewness
                    kurtosis
                    trialCount
                    variance
                    provenance
              )
          )
      KupiecUnconditionalCoverageTest ->
        withBacktestVectors fixtureRoot testCase $ \realized forecast ->
          fmap LikelihoodRecord $ do
            confidence <- realArgument testCase "confidence" Nothing ResearchInputInvalid
            significance <- realArgument testCase "significance" (Just 0.05) SignificanceInvalid
            kupiecUnconditionalCoverageTest realized forecast confidence significance
      ChristoffersenIndependenceTest ->
        withBacktestVectors fixtureRoot testCase $ \realized forecast ->
          fmap IndependenceRecord $ do
            significance <- realArgument testCase "significance" (Just 0.05) SignificanceInvalid
            christoffersenIndependenceTest realized forecast significance
      ChristoffersenConditionalCoverageTest ->
        withBacktestVectors fixtureRoot testCase $ \realized forecast ->
          fmap ConditionalCoverageRecord $ do
            confidence <- realArgument testCase "confidence" Nothing ResearchInputInvalid
            significance <- realArgument testCase "significance" (Just 0.05) SignificanceInvalid
            christoffersenConditionalCoverageTest realized forecast confidence significance
  pure
    ( fmap
        ( either
            (CaseFailure fixtureId functionId)
            (CaseSuccess fixtureId functionId)
        )
        computed
    )

withProductionVector ::
  FilePath ->
  CaseRequest ->
  Text ->
  (U.Vector Double -> Either StableError NumericResult) ->
  IO (Either TransportError (Either StableError NumericResult))
withProductionVector fixtureRoot testCase name kernel = do
  decoded <- decodeVector fixtureRoot testCase name True
  pure (fmap (>>= kernel) decoded)

withResearchVector ::
  FilePath ->
  CaseRequest ->
  Text ->
  (U.Vector Double -> Either StableError NumericResult) ->
  IO (Either TransportError (Either StableError NumericResult))
withResearchVector fixtureRoot testCase name kernel = do
  decoded <- decodeVector fixtureRoot testCase name False
  pure (fmap (>>= kernel) decoded)

withBacktestVectors ::
  FilePath ->
  CaseRequest ->
  (U.Vector Double -> U.Vector Double -> Either StableError NumericResult) ->
  IO (Either TransportError (Either StableError NumericResult))
withBacktestVectors fixtureRoot testCase kernel = do
  realized <- decodeVector fixtureRoot testCase "realized_losses" False
  case realized of
    Left transport -> pure (Left transport)
    Right (Left stableError) -> pure (Right (Left stableError))
    Right (Right realizedValues) -> do
      forecast <- decodeVector fixtureRoot testCase "forecast_vars" False
      pure
        ( case forecast of
            Left transport -> Left transport
            Right (Left stableError) -> Right (Left stableError)
            Right (Right forecastValues) -> Right (kernel realizedValues forecastValues)
        )

decodeVector ::
  FilePath ->
  CaseRequest ->
  Text ->
  Bool ->
  IO (Either TransportError (Either StableError (U.Vector Double)))
decodeVector fixtureRoot testCase@(CaseRequest _ _ arguments) name production =
  case Map.lookup name arguments of
    Just raw@(RawObject _) ->
      case binaryDescriptor raw of
        Nothing -> pure (Right (Left (vectorTypeError production raw)))
        Just manifestFile ->
          readBinaryVector fixtureRoot testCase name manifestFile
    Just (RawArray values) ->
      pure
        ( Right
            ( case classifyInlineVector production values of
                Left stableError -> Left stableError
                Right numbers -> Right (U.fromList numbers)
            )
        )
    Just raw -> pure (Right (Left (vectorTypeError production raw)))
    Nothing ->
      pure
        ( Right
            (Left (if production then InputTypeInvalid else ResearchInputInvalid))
        )

classifyInlineVector :: Bool -> [RawJson] -> Either StableError [Double]
classifyInlineVector production values
  | any isNested values = Left (if production then InputShapeInvalid else ResearchInputInvalid)
  | any isBoolean values = Left (if production then InputBoolInvalid else ResearchInputInvalid)
  | not (all isNumber values) =
      Left (if production then InputTypeInvalid else ResearchInputInvalid)
  | otherwise =
      case traverse rawDouble values of
        Nothing -> Left (if production then InputNonFinite else ResearchInputInvalid)
        Just numbers -> Right numbers
  where
    isNested value =
      case value of
        RawArray _ -> True
        RawObject _ -> True
        _ -> False
    isBoolean value =
      case value of
        RawBool _ -> True
        _ -> False
    isNumber value =
      case value of
        RawNumber _ -> True
        _ -> False

vectorTypeError :: Bool -> RawJson -> StableError
vectorTypeError production raw =
  case raw of
    RawBool _
      | production -> InputBoolInvalid
    _
      | production -> InputTypeInvalid
      | otherwise -> ResearchInputInvalid

binaryDescriptor :: RawJson -> Maybe Text
binaryDescriptor raw = do
  fields <- either (const Nothing) Just (objectMap raw)
  if Map.keysSet fields /= Set.fromList ["kind", "manifestFile"]
    then Nothing
    else do
      RawString kind <- Map.lookup "kind" fields
      RawString manifestFile <- Map.lookup "manifestFile" fields
      if kind == "binaryFloat64" && safeBasename manifestFile
        then Just manifestFile
        else Nothing

readBinaryVector ::
  FilePath ->
  CaseRequest ->
  Text ->
  Text ->
  IO (Either TransportError (Either StableError (U.Vector Double)))
readBinaryVector
  fixtureRoot
  (CaseRequest fixtureId _ _)
  argumentName
  manifestFile = do
    attempted <-
      try
        (readBinaryVectorUnchecked fixtureRoot fixtureId argumentName manifestFile) ::
        IO
          ( Either
              IOException
              (Either TransportError (Either StableError (U.Vector Double)))
          )
    pure
      ( case attempted of
          Left _ -> Left (transportFailure ManifestInvalid fixtureId Nothing)
          Right value -> value
      )

readBinaryVectorUnchecked ::
  FilePath ->
  Text ->
  Text ->
  Text ->
  IO (Either TransportError (Either StableError (U.Vector Double)))
readBinaryVectorUnchecked fixtureRoot fixtureId argumentName manifestFile = do
  let largeRoot = fixtureRoot </> "large"
      manifestPath = largeRoot </> Text.unpack manifestFile
  rootExists <- doesDirectoryExist largeRoot
  manifestExists <- doesFileExist manifestPath
  manifestLink <- if manifestExists then pathIsSymbolicLink manifestPath else pure False
  if not rootExists || not manifestExists || manifestLink
    then pure (Left (transportFailure ManifestInvalid fixtureId (Just "manifestFile")))
    else do
      rootCanonical <- canonicalizePath largeRoot
      manifestCanonical <- canonicalizePath manifestPath
      if not (pathWithin rootCanonical manifestCanonical)
        then pure (Left (transportFailure ManifestInvalid fixtureId (Just "manifestFile")))
        else do
          payload <- BS8.readFile manifestCanonical
          case parseManifest fixtureId argumentName payload of
            Left transport -> pure (Left transport)
            Right manifest -> loadManifestBinary rootCanonical fixtureId manifest

data BinaryManifest = BinaryManifest
  { manifestFileName :: Text,
    manifestCount :: Integer,
    manifestByteLength :: Integer,
    manifestSha256 :: Text,
    manifestExpectedError :: Maybe Text
  }
  deriving stock (Eq, Show)

parseManifest ::
  Text ->
  Text ->
  ByteString ->
  Either TransportError BinaryManifest
parseManifest fixtureId argumentName payload = do
  raw <- mapManifestFailure fixtureId (parseStrictJson payload)
  fields <- mapManifestFailure fixtureId (objectMap raw)
  let required =
        Set.fromList
          [ "schemaVersion",
            "fixtureId",
            "argumentName",
            "fileName",
            "encoding",
            "dtype",
            "byteOrder",
            "arrayOrder",
            "shape",
            "count",
            "byteLength",
            "sha256",
            "generator"
          ]
      allowed = Set.insert "expectedSemanticError" required
  if not (required `Set.isSubsetOf` Map.keysSet fields)
    || not (Map.keysSet fields `Set.isSubsetOf` allowed)
    then Left (transportFailure ManifestInvalid fixtureId Nothing)
    else do
      schema <- manifestText fields "schemaVersion" fixtureId
      manifestFixture <- manifestText fields "fixtureId" fixtureId
      manifestArgument <- manifestText fields "argumentName" fixtureId
      fileName <- manifestText fields "fileName" fixtureId
      encoding <- manifestText fields "encoding" fixtureId
      dtype <- manifestText fields "dtype" fixtureId
      byteOrder <- manifestText fields "byteOrder" fixtureId
      arrayOrder <- manifestText fields "arrayOrder" fixtureId
      count <- manifestInteger fields "count" fixtureId
      byteLength <- manifestInteger fields "byteLength" fixtureId
      sha <- manifestText fields "sha256" fixtureId
      shape <-
        case Map.lookup "shape" fields of
          Just (RawArray [item]) ->
            maybe
              (Left (transportFailure ManifestInvalid fixtureId (Just "shape")))
              Right
              (rawInteger item)
          _ -> Left (transportFailure ManifestInvalid fixtureId (Just "shape"))
      expected <-
        case Map.lookup "expectedSemanticError" fields of
          Nothing -> Right Nothing
          Just (RawString value)
            | validFieldIdentifier value -> Right (Just value)
          _ ->
            Left
              (transportFailure ManifestInvalid fixtureId (Just "expectedSemanticError"))
      case Map.lookup "generator" fields of
        Just (RawObject _) -> Right ()
        _ -> Left (transportFailure ManifestInvalid fixtureId (Just "generator"))
      if schema /= "s1.4x-binary-array-v1"
        || manifestFixture /= fixtureId
        || manifestArgument /= argumentName
        || encoding /= "ieee754-binary64"
        || dtype /= "float64"
        || byteOrder /= "little"
        || arrayOrder /= "C"
        || not (safeBasename fileName)
        || not (validSha256 sha)
        || shape <= 0
        || count /= shape
        || byteLength /= count * 8
        || byteLength > allocationCapBytes
        then Left (transportFailure ManifestInvalid fixtureId Nothing)
        else
          Right
            ( BinaryManifest
                fileName
                count
                byteLength
                sha
                expected
            )

loadManifestBinary ::
  FilePath ->
  Text ->
  BinaryManifest ->
  IO (Either TransportError (Either StableError (U.Vector Double)))
loadManifestBinary largeRoot fixtureId manifest = do
  let generatedRoot = largeRoot </> "generated"
      binaryPath = generatedRoot </> Text.unpack (manifestFileName manifest)
  generatedExists <- doesDirectoryExist generatedRoot
  binaryExists <- doesFileExist binaryPath
  binaryLink <- if binaryExists then pathIsSymbolicLink binaryPath else pure False
  if not generatedExists || not binaryExists || binaryLink
    then pure (Left (transportFailure BinaryInvalid fixtureId Nothing))
    else do
      generatedCanonical <- canonicalizePath generatedRoot
      binaryCanonical <- canonicalizePath binaryPath
      if not (pathWithin generatedCanonical binaryCanonical)
        then pure (Left (transportFailure BinaryInvalid fixtureId Nothing))
        else do
          payload <- BS8.readFile binaryCanonical
          let actualLength = toInteger (BS8.length payload)
              actualSha = sha256Hex payload
          if actualLength /= manifestByteLength manifest
            || actualSha /= BS8.pack (Text.unpack (manifestSha256 manifest))
            then pure (Left (transportFailure BinaryInvalid fixtureId Nothing))
            else do
              let count = fromInteger (manifestCount manifest)
                  values = runGet (U.replicateM count getDoublele) (LBS.fromStrict payload)
                  nonFinite = U.any (\value -> isNaN value || isInfinite value) values
              if nonFinite
                then
                  case manifestExpectedError manifest of
                    Just "input_non_finite" -> pure (Right (Left InputNonFinite))
                    Just "research_input_invalid" -> pure (Right (Left ResearchInputInvalid))
                    _ -> pure (Left (transportFailure BinaryInvalid fixtureId Nothing))
                else pure (Right (Right values))

realArgument ::
  CaseRequest ->
  Text ->
  Maybe Double ->
  StableError ->
  Either StableError Double
realArgument (CaseRequest _ _ arguments) name defaultValue stableError =
  case Map.lookup name arguments of
    Nothing -> maybe (Left stableError) Right defaultValue
    Just raw -> maybe (Left stableError) Right (rawDouble raw)

integerArgument ::
  CaseRequest ->
  Text ->
  Maybe Integer ->
  StableError ->
  Either StableError Integer
integerArgument (CaseRequest _ _ arguments) name defaultValue stableError =
  case Map.lookup name arguments of
    Nothing -> maybe (Left stableError) Right defaultValue
    Just raw -> maybe (Left stableError) Right (rawInteger raw)

provenanceArgument :: CaseRequest -> Either StableError TrialProvenance
provenanceArgument (CaseRequest _ _ arguments) = do
  raw <- maybe (Left TrialProvenanceInvalid) Right (Map.lookup "trial_provenance" arguments)
  fields <- either (const (Left TrialProvenanceInvalid)) Right (objectMap raw)
  if Map.keysSet fields /= provenanceFields
    then Left TrialProvenanceInvalid
    else do
      schema <- provenanceText fields "schema_version"
      method <- provenanceText fields "method"
      rawCount <- provenanceInteger fields "raw_trial_count"
      effectiveCount <- provenanceInteger fields "effective_trial_count"
      frequency <- provenanceText fields "sampling_frequency"
      digest <- provenanceText fields "trial_registry_sha256"
      varianceDof <- provenanceInteger fields "variance_ddof"
      Right
        ( TrialProvenance
            (Text.unpack schema)
            (Text.unpack method)
            rawCount
            effectiveCount
            (Text.unpack frequency)
            (Text.unpack digest)
            varianceDof
        )

provenanceText :: Map Text RawJson -> Text -> Either StableError Text
provenanceText fields name =
  case Map.lookup name fields of
    Just (RawString value) -> Right value
    _ -> Left TrialProvenanceInvalid

provenanceInteger :: Map Text RawJson -> Text -> Either StableError Integer
provenanceInteger fields name =
  maybe (Left TrialProvenanceInvalid) Right (Map.lookup name fields >>= rawInteger)

-- | 결과 batch를 frozen schema의 compact JSON bytes로 인코딩한다.
-- case 순서, stable error code, finite/negative-zero-normalized numeric shape를 그대로 보존한다.
encodeResultBatch :: ResultBatch -> ByteString
encodeResultBatch (ResultBatch requestId implementation results) =
  LBS.toStrict
    ( encode
        ( object
            [ "schemaVersion" .= ("s1.4x-result-batch-v1" :: Text),
              "requestId" .= requestId,
              "implementation" .= implementation,
              "results" .= fmap encodeCaseResult results
            ]
        )
    )
    <> "\n"

encodeCaseResult :: CaseResult -> Value
encodeCaseResult result =
  case result of
    CaseFailure fixtureId functionId stableError ->
      object
        [ "schemaVersion" .= ("s1.4x-result-v1" :: Text),
          "functionId" .= functionIdText functionId,
          "fixtureId" .= fixtureId,
          "status" .= ("error" :: Text),
          "errorCode" .= stableErrorCode stableError
        ]
    CaseSuccess fixtureId functionId numericResult ->
      object
        [ "schemaVersion" .= ("s1.4x-result-v1" :: Text),
          "functionId" .= functionIdText functionId,
          "fixtureId" .= fixtureId,
          "status" .= ("ok" :: Text),
          "values" .= encodeNumericResult numericResult
        ]

encodeNumericResult :: NumericResult -> Value
encodeNumericResult numericResult =
  case numericResult of
    ScalarResult value -> finiteNumber value
    VectorResult values -> toJSON (fmap normalizeZero (U.toList values))
    LikelihoodRecord value -> encodeLikelihood value
    IndependenceRecord value -> encodeIndependence value
    ConditionalCoverageRecord value -> encodeConditional value

encodeLikelihood :: LikelihoodResult -> Value
encodeLikelihood
  (LikelihoodResult statistic pValue reject observations exceptions dof significance) =
    object
      [ "statistic" .= normalizeZero statistic,
        "p_value" .= normalizeZero pValue,
        "reject" .= reject,
        "observations" .= observations,
        "exceptions" .= exceptions,
        "degrees_of_freedom" .= dof,
        "significance" .= normalizeZero significance
      ]

encodeIndependence :: IndependenceResult -> Value
encodeIndependence
  (IndependenceResult statistic pValue reject observations exceptions dof significance transitions) =
    object
      [ "statistic" .= normalizeZero statistic,
        "p_value" .= normalizeZero pValue,
        "reject" .= reject,
        "observations" .= observations,
        "exceptions" .= exceptions,
        "degrees_of_freedom" .= dof,
        "significance" .= normalizeZero significance,
        "transitions" .= encodeTransitions transitions
      ]

encodeConditional :: ConditionalCoverageResult -> Value
encodeConditional
  ( ConditionalCoverageResult
      statistic
      pValue
      reject
      observations
      exceptions
      dof
      significance
      transitions
      conditionedObservations
      conditionedExceptions
      unconditionalComponent
      independenceComponent
    ) =
    object
      [ "statistic" .= normalizeZero statistic,
        "p_value" .= normalizeZero pValue,
        "reject" .= reject,
        "observations" .= observations,
        "exceptions" .= exceptions,
        "degrees_of_freedom" .= dof,
        "significance" .= normalizeZero significance,
        "transitions" .= encodeTransitions transitions,
        "conditioned_observations" .= conditionedObservations,
        "conditioned_exceptions" .= conditionedExceptions,
        "unconditional_component_statistic" .= normalizeZero unconditionalComponent,
        "independence_component_statistic" .= normalizeZero independenceComponent
      ]

encodeTransitions :: TransitionCounts -> Value
encodeTransitions (TransitionCounts n00 n01 n10 n11) =
  object
    [ "n00" .= n00,
      "n01" .= n01,
      "n10" .= n10,
      "n11" .= n11
    ]

-- | transport 오류를 허용된 context field만 포함한 frozen JSON envelope로 인코딩한다.
-- exception text, host path, raw request와 binary payload는 절대 직렬화하지 않는다.
encodeTransportError :: TransportError -> ByteString
encodeTransportError
  (TransportError code requestId fixtureId field) =
    LBS.toStrict
      ( encode
          ( object
              ( [ "schemaVersion" .= ("s1.4x-transport-error-v1" :: Text),
                  "code" .= transportCodeText code
                ]
                  <> optionalPair "requestId" requestId
                  <> optionalPair "fixtureId" fixtureId
                  <> optionalPair "field" field
              )
          )
      )
      <> "\n"

-- | 입력 bytes의 SHA-256을 lowercase ASCII hexadecimal 64자로 반환한다.
-- manifest 검증은 이 digest를 exact 비교하며 Unicode digit이나 대문자를 허용하지 않는다.
sha256Hex :: ByteString -> ByteString
sha256Hex = BS8.pack . showDigest . sha256 . LBS.fromStrict

finiteNumber :: Double -> Value
finiteNumber = toJSON . normalizeZero

normalizeZero :: Double -> Double
normalizeZero value
  | value == 0.0 = 0.0
  | otherwise = value

transportCodeText :: TransportCode -> Text
transportCodeText transport =
  case transport of
    RequestInvalid -> "request_invalid"
    ManifestInvalid -> "manifest_invalid"
    BinaryInvalid -> "binary_invalid"
    InternalError -> "internal_error"

optionalPair :: Text -> Maybe Text -> [Pair]
optionalPair name = maybe [] (\value -> [Key.fromText name .= value])

firstTransportFailure ::
  [Either TransportError CaseResult] ->
  Maybe TransportError
firstTransportFailure values =
  case lefts values of
    [] -> Nothing
    first : _ -> Just first

requestText ::
  Map Text RawJson ->
  Text ->
  Maybe Text ->
  Either TransportError Text
requestText fields name requestId =
  case Map.lookup name fields of
    Just (RawString value) -> Right value
    _ -> Left (requestFailure requestId Nothing (Just name))

manifestText ::
  Map Text RawJson ->
  Text ->
  Text ->
  Either TransportError Text
manifestText fields name fixtureId =
  case Map.lookup name fields of
    Just (RawString value) -> Right value
    _ -> Left (transportFailure ManifestInvalid fixtureId (Just name))

manifestInteger ::
  Map Text RawJson ->
  Text ->
  Text ->
  Either TransportError Integer
manifestInteger fields name fixtureId =
  maybe
    (Left (transportFailure ManifestInvalid fixtureId (Just name)))
    Right
    (Map.lookup name fields >>= rawInteger)

requireExactFields ::
  Map Text RawJson ->
  Set Text ->
  Maybe Text ->
  Either TransportError ()
requireExactFields fields expected requestId =
  if Map.keysSet fields == expected
    then Right ()
    else Left (requestFailure requestId Nothing Nothing)

requireSubsetFields ::
  Map Text RawJson ->
  Set Text ->
  Maybe Text ->
  Either TransportError ()
requireSubsetFields fields expected requestId =
  if requiredCaseFields `Set.isSubsetOf` Map.keysSet fields
    && Map.keysSet fields `Set.isSubsetOf` expected
    then Right ()
    else Left (requestFailure requestId Nothing Nothing)

mapRequestFailure :: Either Text value -> Either TransportError value
mapRequestFailure = either (const (Left (requestFailure Nothing Nothing Nothing))) Right

mapRequestFailureWith ::
  Text ->
  Maybe Text ->
  Either Text value ->
  Either TransportError value
mapRequestFailureWith requestId fixtureId =
  either (const (Left (requestFailure (Just requestId) fixtureId Nothing))) Right

mapManifestFailure ::
  Text ->
  Either Text value ->
  Either TransportError value
mapManifestFailure fixtureId =
  either (const (Left (transportFailure ManifestInvalid fixtureId Nothing))) Right

requestFailure ::
  Maybe Text ->
  Maybe Text ->
  Maybe Text ->
  TransportError
requestFailure = TransportError RequestInvalid

transportFailure :: TransportCode -> Text -> Maybe Text -> TransportError
transportFailure code fixtureId =
  TransportError code Nothing (Just fixtureId)

caseFixtureText :: CaseRequest -> Text
caseFixtureText (CaseRequest fixtureId _ _) = fixtureId

parseFunctionId :: Text -> Maybe FunctionId
parseFunctionId value =
  Map.lookup value functionIds

argumentContract :: FunctionId -> (Set Text, Set Text)
argumentContract functionId =
  case functionId of
    SimpleReturns -> contract ["prices"] []
    LogReturns -> contract ["prices"] []
    CumulativeReturn -> contract ["returns"] []
    Cagr -> contract ["prices"] ["periods_per_year"]
    RealizedVolatility -> contract ["log_returns"] []
    AnnualizedVolatility -> contract ["log_returns"] ["periods_per_year"]
    MaxDrawdown -> contract ["equity_curve"] []
    SharpeRatio -> contract ["returns"] ["risk_free_rate", "periods_per_year"]
    SortinoRatio -> contract ["returns"] ["target_return", "periods_per_year"]
    HistoricalVar -> contract ["returns"] ["confidence"]
    HistoricalCvar -> contract ["returns"] ["confidence"]
    HistoricalExpectedShortfall -> contract ["losses"] ["confidence"]
    RealizedVariance -> contract ["intraday_log_returns"] []
    RealizedVolatilityIntraday -> contract ["intraday_log_returns"] []
    LoAdjustedSharpeRatio -> contract ["returns", "aggregation_periods"] ["risk_free_rate"]
    ProbabilisticSharpeRatio ->
      contract
        ["observed_sharpe", "benchmark_sharpe", "sample_size", "skewness", "kurtosis"]
        []
    DeflatedSharpeRatio ->
      contract
        [ "observed_sharpe",
          "sample_size",
          "skewness",
          "kurtosis",
          "trial_count",
          "sharpe_estimate_variance",
          "trial_provenance"
        ]
        []
    KupiecUnconditionalCoverageTest ->
      contract ["realized_losses", "forecast_vars", "confidence"] ["significance"]
    ChristoffersenIndependenceTest ->
      contract ["realized_losses", "forecast_vars"] ["significance"]
    ChristoffersenConditionalCoverageTest ->
      contract ["realized_losses", "forecast_vars", "confidence"] ["significance"]

contract :: [Text] -> [Text] -> (Set Text, Set Text)
contract required optional = (Set.fromList required, Set.fromList optional)

validIdentifier :: Text -> Bool
validIdentifier value =
  case (Text.uncons value, Text.unsnoc value) of
    (Just (first, _), Just (_, final)) ->
      Text.length value <= 128
        && validIdentifierEdge first
        && validIdentifierEdge final
        && Text.all validIdentifierCharacter value
    _ -> False

validFieldIdentifier :: Text -> Bool
validFieldIdentifier value =
  case Text.uncons value of
    Just (first, _) ->
      Text.length value <= 64
        && isLowerAscii first
        && Text.all
          (\character -> isLowerAscii character || isDigitAscii character || character == '_')
          value
    Nothing -> False

validIdentifierEdge :: Char -> Bool
validIdentifierEdge character =
  isLowerAscii character || isDigitAscii character

validIdentifierCharacter :: Char -> Bool
validIdentifierCharacter character =
  validIdentifierEdge character || character `elem` ['.', '_', ':', '-']

safeBasename :: Text -> Bool
safeBasename value =
  case Text.uncons value of
    Just (first, _) ->
      Text.length value <= 128
        && isAsciiAlphaNumeric first
        && Text.all
          (\character -> isAsciiAlphaNumeric character || character `elem` ['.', '_', '-'])
          value
    Nothing -> False

validSha256 :: Text -> Bool
validSha256 value =
  Text.length value == 64
    && Text.all
      (\character -> isDigitAscii character || (character >= 'a' && character <= 'f'))
      value

isAsciiAlphaNumeric :: Char -> Bool
isAsciiAlphaNumeric character =
  isLowerAscii character
    || (character >= 'A' && character <= 'Z')
    || isDigitAscii character

isLowerAscii :: Char -> Bool
isLowerAscii character = character >= 'a' && character <= 'z'

isDigitAscii :: Char -> Bool
isDigitAscii character = character >= '0' && character <= '9'

pathWithin :: FilePath -> FilePath -> Bool
pathWithin root candidate =
  candidate == root || (root <> "/") `prefixOf` candidate

prefixOf :: Eq value => [value] -> [value] -> Bool
prefixOf [] _ = True
prefixOf _ [] = False
prefixOf (left : leftRest) (right : rightRest) =
  left == right && prefixOf leftRest rightRest

allocationCapBytes :: Integer
allocationCapBytes = 536870912

envelopeFields :: Set Text
envelopeFields = Set.fromList ["schemaVersion", "requestId", "cases"]

requiredCaseFields :: Set Text
requiredCaseFields = Set.fromList ["fixtureId", "functionId", "arguments"]

caseFields :: Set Text
caseFields =
  Set.fromList
    [ "fixtureId",
      "functionId",
      "arguments",
      "expectedSemanticError",
      "toleranceClass"
    ]

provenanceFields :: Set Text
provenanceFields =
  Set.fromList
    [ "schema_version",
      "method",
      "raw_trial_count",
      "effective_trial_count",
      "sampling_frequency",
      "trial_registry_sha256",
      "variance_ddof"
    ]

functionIds :: Map Text FunctionId
functionIds =
  Map.fromList
    [ (functionIdText functionId, functionId)
      | functionId <- [minBound .. maxBound]
    ]
