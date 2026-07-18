module S14X.BenchmarkMain (main) where

import           Control.DeepSeq (NFData (rnf))
import           Control.Monad (unless)
import           Criterion.Main (Benchmark, bench, bgroup, defaultMain, env, nf)
import           Data.Aeson (FromJSON (parseJSON), Value, eitherDecodeFileStrict', encode,
                             object, withObject, (.:), (.=))
import           Data.Binary.Get (getDoublele, runGet)
import           Data.List (find)
import           Data.Maybe (fromMaybe)
import           Data.Text (Text)
import           System.Directory (canonicalizePath, doesDirectoryExist, doesFileExist,
                                   pathIsSymbolicLink)
import           System.Environment (getExecutablePath, lookupEnv)
import           System.Exit (ExitCode (ExitSuccess))
import           System.FilePath (isAbsolute, takeDirectory, takeFileName, (</>))
import           System.IO.Error (catchIOError, isDoesNotExistError)
import           System.Process (readProcessWithExitCode)

import qualified Data.ByteString as BS
import qualified Data.ByteString.Lazy as LBS
import qualified Data.Text as Text
import qualified Data.Text.Encoding as TextEncoding
import qualified Data.Vector.Unboxed as U

import           S14X.Contract.AtomicOutput (PublishResult (Published), exclusiveAtomicWrite)
import           S14X.Contract.BenchmarkValidation (BenchmarkResultShape (ConditionalCoverageBatch, IndependenceBatch, LikelihoodBatch, ScalarBatch, VectorBatch),
                                                    validateBenchmarkResults)
import           S14X.Contract.Process (sha256Hex)
import           S14X.Core.AdvancedRisk (christoffersenConditionalCoverageTest,
                                         christoffersenIndependenceTest, deflatedSharpeRatio,
                                         historicalExpectedShortfall,
                                         kupiecUnconditionalCoverageTest, loAdjustedSharpeRatio,
                                         probabilisticSharpeRatio, realizedVariance,
                                         realizedVolatilityIntraday)
import           S14X.Core.Error (StableError)
import           S14X.Core.Models (NumericResult (ConditionalCoverageRecord, IndependenceRecord, LikelihoodRecord, ScalarResult, VectorResult),
                                   TrialProvenance (TrialProvenance))
import           S14X.Core.ProductionMetrics (annualizedVolatility, cagr, cumulativeReturn,
                                              historicalCvar, historicalVar, logReturns,
                                              maxDrawdown, realizedVolatility, sharpeRatio,
                                              simpleReturns, sortinoRatio)

data BenchmarkPlan = BenchmarkPlan [FamilySelector] [BenchmarkCase]
  deriving stock (Eq, Show)

data FamilySelector = FamilySelector
  { selectorBoundaryId :: Text,
    selectorFamilyId :: Text,
    selectorId :: Text,
    selectorExpectedCaseIds :: [Text],
    selectorCriterionMatchMode :: Text,
    selectorCriterionPrefix :: Maybe Text
  }
  deriving stock (Eq, Show)

data BenchmarkCase = BenchmarkCase
  { benchmarkCaseId :: Text,
    benchmarkFamilyId :: Text,
    benchmarkFunctionId :: Text,
    benchmarkFixtureId :: Text,
    benchmarkLogicalOperations :: Int,
    benchmarkVectorLength :: Int,
    benchmarkBatchSize :: Int,
    benchmarkFunctionArguments :: Value
  }
  deriving stock (Eq, Show)

data BinaryManifest = BinaryManifest
  { manifestSchemaVersion :: Text,
    manifestFixtureId :: Text,
    manifestArgumentName :: Text,
    manifestFileName :: Text,
    manifestEncoding :: Text,
    manifestDtype :: Text,
    manifestByteOrder :: Text,
    manifestArrayOrder :: Text,
    manifestShape :: [Int],
    manifestCount :: Int,
    manifestByteLength :: Int,
    manifestSha256 :: Text
  }
  deriving stock (Eq, Show)

data FrozenInputs = FrozenInputs
  { frozenPrices :: U.Vector Double,
    frozenReturns :: U.Vector Double,
    frozenRealizedLosses :: U.Vector Double,
    frozenForecastVars :: U.Vector Double
  }
  deriving stock (Eq, Show)

data SingleFunction
  = SingleSimpleReturns
  | SingleLogReturns
  | SingleCumulativeReturn
  | SingleCagr
  | SingleRealizedVolatility
  | SingleAnnualizedVolatility
  | SingleMaxDrawdown
  | SingleSharpeRatio
  | SingleSortinoRatio
  | SingleHistoricalVar
  | SingleHistoricalCvar
  | SingleHistoricalExpectedShortfall
  | SingleRealizedVariance
  | SingleRealizedVolatilityIntraday
  | SingleLoAdjustedSharpeRatio
  deriving stock (Eq, Show)

data CoverageFunction
  = CoverageKupiec
  | CoverageChristoffersenIndependence
  | CoverageChristoffersenConditional
  deriving stock (Eq, Show)

data DsrInput = DsrInput Double Integer TrialProvenance
  deriving stock (Eq, Show)

data PreparedCase
  = PreparedSingle SingleFunction (U.Vector Double)
  | PreparedPsr [Double]
  | PreparedDsr [DsrInput]
  | PreparedCoverage CoverageFunction [(U.Vector Double, U.Vector Double)]
  deriving stock (Eq, Show)

instance FromJSON BenchmarkPlan where
  parseJSON =
    withObject "BenchmarkPlan" $ \objectValue ->
      BenchmarkPlan
        <$> objectValue .: "familySelectors"
        <*> objectValue .: "cases"

instance FromJSON FamilySelector where
  parseJSON =
    withObject "FamilySelector" $ \objectValue ->
      FamilySelector
        <$> objectValue .: "boundaryId"
        <*> objectValue .: "familyId"
        <*> objectValue .: "selectorId"
        <*> objectValue .: "expectedCaseIds"
        <*> objectValue .: "criterionMatchMode"
        <*> objectValue .: "criterionPrefix"

instance FromJSON BenchmarkCase where
  parseJSON =
    withObject "BenchmarkCase" $ \objectValue ->
      BenchmarkCase
        <$> objectValue .: "caseId"
        <*> objectValue .: "familyId"
        <*> objectValue .: "functionId"
        <*> objectValue .: "fixtureId"
        <*> objectValue .: "logicalOperationsPerInvocation"
        <*> objectValue .: "vectorLength"
        <*> objectValue .: "batchSize"
        <*> objectValue .: "functionArguments"

instance FromJSON BinaryManifest where
  parseJSON =
    withObject "BinaryManifest" $ \objectValue ->
      BinaryManifest
        <$> objectValue .: "schemaVersion"
        <*> objectValue .: "fixtureId"
        <*> objectValue .: "argumentName"
        <*> objectValue .: "fileName"
        <*> objectValue .: "encoding"
        <*> objectValue .: "dtype"
        <*> objectValue .: "byteOrder"
        <*> objectValue .: "arrayOrder"
        <*> objectValue .: "shape"
        <*> objectValue .: "count"
        <*> objectValue .: "byteLength"
        <*> objectValue .: "sha256"

instance NFData FrozenInputs where
  rnf (FrozenInputs prices returns realized forecast) =
    forceVector prices
      `seq` forceVector returns
      `seq` forceVector realized
      `seq` forceVector forecast

instance NFData SingleFunction where
  rnf value = value `seq` ()

instance NFData CoverageFunction where
  rnf value = value `seq` ()

instance NFData DsrInput where
  rnf (DsrInput observed trials provenance) =
    rnf observed `seq` rnf trials `seq` rnf provenance

instance NFData PreparedCase where
  rnf prepared =
    case prepared of
      PreparedSingle functionId values ->
        rnf functionId `seq` forceVector values
      PreparedPsr observed -> rnf observed
      PreparedDsr inputs -> rnf inputs
      PreparedCoverage functionId pairs ->
        rnf functionId
          `seq` foldr
            (\(realized, forecast) unit -> forceVector realized `seq` forceVector forecast `seq` unit)
            ()
            pairs

-- | frozen benchmark plan·fixture root·output root를 검증한 뒤 Criterion case만 등록한다.
-- correctness와 prepared-result shape gate를 통과하지 못한 입력은 timing 시작 전에 실패한다.
main :: IO ()
main = do
  planPath <- configuredPath "S1_4X_BENCHMARK_PLAN" "../benchmarks/benchmark-plan.v1.json"
  fixtureRoot <- configuredPath "S1_4X_BENCHMARK_FIXTURE_ROOT" "../contract/fixtures"
  qualificationPath <- requiredConfiguredPath "S1_4X_BENCHMARK_QUALIFICATION"
  verifyPlanLock planPath
  plan <- decodeFile planPath
  either fail pure (validatePlan plan)
  selectedCases <- selectedBenchmarkCases plan
  publishRuntimeIdentity
  defaultMain
    [ env
        (setupBenchmarkEnvironment fixtureRoot qualificationPath selectedCases)
        (bgroup "" . zipWith benchmark selectedCases)
    ]

-- | Full rotation에서만 설정되는 출력 경로에 실제 Criterion process identity를 기록한다.
-- Profile qualification은 이 경계를 설정하지 않으므로 기존 4x2x7 argv와 실행을 바꾸지 않는다.
publishRuntimeIdentity :: IO ()
publishRuntimeIdentity = do
  configured <- lookupEnv "S1_4X_BENCHMARK_RUNTIME_IDENTITY"
  case configured of
    Nothing -> pure ()
    Just _ -> do
      output <- requiredConfiguredOutputPath "S1_4X_BENCHMARK_RUNTIME_IDENTITY"
      selectorIdText <- Text.pack <$> requiredConfiguredValue "S1_4X_BENCHMARK_SELECTOR_ID"
      executablePath <- getExecutablePath >>= canonicalizePath
      executableExists <- doesFileExist executablePath
      executableSymbolic <- pathIsSymbolicLink executablePath
      unless
        (executableExists && not executableSymbolic)
        (fail "benchmark executable identity is unsafe")
      executablePayload <- BS.readFile executablePath
      let executableSha256 = TextEncoding.decodeUtf8 (sha256Hex executablePayload)
          identity =
            object
              [ "schemaVersion"
                  .= ("s1.4x-haskell-benchmark-runtime-identity-v1" :: Text),
                "boundaryId" .= ("haskell" :: Text),
                "selectorId" .= selectorIdText,
                "executedBenchmarkPath" .= executablePath,
                "executedBenchmarkSha256" .= executableSha256,
                "status" .= ("PASS" :: Text)
              ]
      published <- exclusiveAtomicWrite output (LBS.toStrict (encode identity))
      unless
        (published == Published)
        (fail "benchmark runtime identity already exists")

selectedBenchmarkCases :: BenchmarkPlan -> IO [BenchmarkCase]
selectedBenchmarkCases (BenchmarkPlan selectors cases) = do
  configured <- lookupEnv "S1_4X_BENCHMARK_SELECTOR_ID"
  case configured of
    Nothing -> pure cases
    Just selectorIdText ->
      case find ((== Text.pack selectorIdText) . selectorId) selectors of
        Nothing -> fail "configured Haskell benchmark selector is unknown"
        Just selector -> do
          unless
            (selectorBoundaryId selector == "haskell")
            (fail "configured benchmark selector is not Haskell")
          let selected =
                filter
                  ((`elem` selectorExpectedCaseIds selector) . benchmarkCaseId)
                  cases
          unless
            (fmap benchmarkCaseId selected == selectorExpectedCaseIds selector)
            (fail "configured benchmark selector case closure mismatch")
          pure selected

setupBenchmarkEnvironment ::
  FilePath ->
  FilePath ->
  [BenchmarkCase] ->
  IO [PreparedCase]
setupBenchmarkEnvironment fixtureRoot qualificationPath benchmarkCases = do
  inputs <- loadFrozenInputs fixtureRoot
  preparedCases <- traverse (setupPreparedCase inputs) benchmarkCases
  -- Criterion이 env 값을 다시 force하기 전에 전체 setup closure를 닫아 PRE_RUN 경계를 보존한다.
  rnf preparedCases `seq` markMeasurementEntered qualificationPath
  pure preparedCases

markMeasurementEntered :: FilePath -> IO ()
markMeasurementEntered path = do
  pythonPath <-
    verifiedMarkerInput
      "S1_4X_BENCHMARK_MARKER_PYTHON"
      "S1_4X_BENCHMARK_MARKER_PYTHON_SHA256"
  markerPath <-
    verifiedMarkerInput
      "S1_4X_BENCHMARK_MARKER_SCRIPT"
      "S1_4X_BENCHMARK_MARKER_SCRIPT_SHA256"
  (exitCode, standardOutput, standardError) <-
    readProcessWithExitCode
      pythonPath
      [ markerPath,
        "mark-measurement-entered",
        "--qualification",
        path
      ]
      ""
  unless
    ( exitCode == ExitSuccess
        && standardOutput == "{\"status\": \"MEASUREMENT_ENTERED\"}\n"
        && null standardError
    )
    (fail "INVALID_PRE_RUN_QUALIFICATION_STATE")

verifiedMarkerInput :: String -> String -> IO FilePath
verifiedMarkerInput pathVariable shaVariable = do
  path <- requiredConfiguredPath pathVariable
  expectedSha256 <- requiredConfiguredValue shaVariable
  payload <- BS.readFile path
  unless
    (TextEncoding.decodeUtf8 (sha256Hex payload) == Text.pack expectedSha256)
    (fail "benchmark marker input SHA-256 mismatch")
  pure path

requiredConfiguredPath :: String -> IO FilePath
requiredConfiguredPath variable = do
  configured <- requiredConfiguredValue variable
  unless (isAbsolute configured) (fail (variable <> " must be absolute"))
  symbolic <- pathIsSymbolicLink configured
  exists <- doesFileExist configured
  unless (exists && not symbolic) (fail (variable <> " must be a regular non-symlink"))
  canonical <- canonicalizePath configured
  unless (canonical == configured) (fail (variable <> " must already be canonical"))
  pure canonical

requiredConfiguredValue :: String -> IO String
requiredConfiguredValue variable = do
  configured <- lookupEnv variable
  case configured of
    Just value | not (null value) -> pure value
    _ -> fail (variable <> " is required")

requiredConfiguredOutputPath :: String -> IO FilePath
requiredConfiguredOutputPath variable = do
  configured <- requiredConfiguredValue variable
  unless (isAbsolute configured) (fail (variable <> " must be absolute"))
  fileExists <- doesFileExist configured
  directoryExists <- doesDirectoryExist configured
  symbolic <-
    pathIsSymbolicLink configured
      `catchIOError` \exception ->
        if isDoesNotExistError exception
          then pure False
          else ioError exception
  unless
    (not fileExists && not directoryExists && not symbolic)
    (fail (variable <> " must not already exist"))
  parent <- canonicalizePath (takeDirectory configured)
  unless
    (takeDirectory configured == parent)
    (fail (variable <> " parent must already be canonical"))
  pure configured

configuredPath :: String -> FilePath -> IO FilePath
configuredPath variable fallback = do
  configured <- lookupEnv variable
  canonicalizePath (fromMaybe fallback configured)

decodeFile :: FromJSON value => FilePath -> IO value
decodeFile path = do
  decoded <- eitherDecodeFileStrict' path
  case decoded of
    Left failure -> fail ("benchmark JSON decode failed: " <> failure)
    Right value -> pure value

verifyPlanLock :: FilePath -> IO ()
verifyPlanLock planPath = do
  planBytes <- BS.readFile planPath
  lockBytes <- BS.readFile (takeDirectory planPath </> "benchmark-plan.v1.sha256")
  let expected =
        TextEncoding.encodeUtf8
          "caf00112f58723e277293f59ccedb48bbd9ec82d096d3118ee3a9ed72658d1d1"
  unless
    (sha256Hex planBytes == expected && expected `BS.isPrefixOf` lockBytes)
    (fail "benchmark plan lock mismatch")

validatePlan :: BenchmarkPlan -> Either String ()
validatePlan (BenchmarkPlan selectors cases) = do
  require
    (fmap benchmarkCaseId cases == expectedCaseIds)
    "benchmark plan case order or IDs mismatch"
  traverse_ validateBenchmarkCase cases
  let haskellSelectors =
        [ selector
          | selector <- selectors,
            selectorBoundaryId selector == "haskell"
        ]
  require
    (fmap selectorIdentity haskellSelectors == expectedSelectorIdentities)
    "Haskell family selector identity mismatch"
  traverse_ (validateSelector cases) haskellSelectors
  where
    selectorIdentity selector =
      ( selectorId selector,
        selectorFamilyId selector,
        selectorCriterionMatchMode selector,
        selectorCriterionPrefix selector
      )

validateSelector :: [BenchmarkCase] -> FamilySelector -> Either String ()
validateSelector cases selector = do
  let actualIds =
        [ benchmarkCaseId benchmarkCase
          | benchmarkCase <- cases,
            benchmarkFamilyId benchmarkCase == selectorFamilyId selector
        ]
  require
    (selectorExpectedCaseIds selector == actualIds)
    ("selector case closure mismatch: " <> Text.unpack (selectorId selector))

validateBenchmarkCase :: BenchmarkCase -> Either String ()
validateBenchmarkCase benchmarkCase = do
  specification <-
    maybe
      (Left ("unknown benchmark function: " <> Text.unpack (benchmarkFunctionId benchmarkCase)))
      Right
      (functionSpecification (benchmarkFunctionId benchmarkCase))
  let (familyId, sizes, batchSize, logicalOperations, arguments, fixtureId, caseId) =
        specification (benchmarkVectorLength benchmarkCase)
  require
    (benchmarkFamilyId benchmarkCase == familyId)
    ("benchmark family mismatch: " <> Text.unpack (benchmarkCaseId benchmarkCase))
  require
    (benchmarkVectorLength benchmarkCase `elem` sizes)
    ("benchmark vector length mismatch: " <> Text.unpack (benchmarkCaseId benchmarkCase))
  require
    (benchmarkBatchSize benchmarkCase == batchSize)
    ("benchmark batch mismatch: " <> Text.unpack (benchmarkCaseId benchmarkCase))
  require
    (benchmarkLogicalOperations benchmarkCase == logicalOperations)
    ("benchmark logical operation mismatch: " <> Text.unpack (benchmarkCaseId benchmarkCase))
  require
    (benchmarkFunctionArguments benchmarkCase == arguments)
    ("benchmark arguments mismatch: " <> Text.unpack (benchmarkCaseId benchmarkCase))
  require
    (benchmarkFixtureId benchmarkCase == fixtureId)
    ("benchmark fixture mismatch: " <> Text.unpack (benchmarkCaseId benchmarkCase))
  require
    (benchmarkCaseId benchmarkCase == caseId)
    ("benchmark case ID mismatch: " <> Text.unpack (benchmarkCaseId benchmarkCase))

require :: Bool -> String -> Either String ()
require condition message =
  if condition then Right () else Left message

benchmark :: BenchmarkCase -> PreparedCase -> Benchmark
benchmark benchmarkCase prepared =
  bench (Text.unpack (benchmarkCaseId benchmarkCase)) (nf runPrepared prepared)

setupPreparedCase :: FrozenInputs -> BenchmarkCase -> IO PreparedCase
setupPreparedCase inputs benchmarkCase = do
  let prepared = prepareCase inputs benchmarkCase
      evaluated = runPrepared prepared
  case validateBenchmarkResults (expectedResultShape benchmarkCase) evaluated of
    Left failure ->
      fail
        ( "benchmark setup validation failed for "
            <> Text.unpack (benchmarkCaseId benchmarkCase)
            <> ": "
            <> failure
        )
    Right () -> pure prepared

expectedResultShape :: BenchmarkCase -> BenchmarkResultShape
expectedResultShape benchmarkCase =
  case benchmarkFunctionId benchmarkCase of
    "simple_returns" -> VectorBatch 1 (benchmarkVectorLength benchmarkCase - 1)
    "log_returns" -> VectorBatch 1 (benchmarkVectorLength benchmarkCase - 1)
    "probabilistic_sharpe_ratio" -> ScalarBatch 16384
    "deflated_sharpe_ratio" -> ScalarBatch 16384
    "kupiec_unconditional_coverage_test" -> LikelihoodBatch 32
    "christoffersen_independence_test" -> IndependenceBatch 32
    "christoffersen_conditional_coverage_test" -> ConditionalCoverageBatch 32
    _ -> ScalarBatch 1

prepareCase :: FrozenInputs -> BenchmarkCase -> PreparedCase
prepareCase inputs benchmarkCase =
  let functionId = benchmarkFunctionId benchmarkCase
      size = benchmarkVectorLength benchmarkCase
      prices = U.take size (frozenPrices inputs)
      returns = U.take size (frozenReturns inputs)
   in case functionId of
        "simple_returns" -> PreparedSingle SingleSimpleReturns prices
        "log_returns" -> PreparedSingle SingleLogReturns prices
        "cumulative_return" -> PreparedSingle SingleCumulativeReturn returns
        "cagr" -> PreparedSingle SingleCagr prices
        "realized_volatility" -> PreparedSingle SingleRealizedVolatility returns
        "annualized_volatility" -> PreparedSingle SingleAnnualizedVolatility returns
        "max_drawdown" -> PreparedSingle SingleMaxDrawdown prices
        "sharpe_ratio" -> PreparedSingle SingleSharpeRatio returns
        "sortino_ratio" -> PreparedSingle SingleSortinoRatio returns
        "historical_var" -> PreparedSingle SingleHistoricalVar returns
        "historical_cvar" -> PreparedSingle SingleHistoricalCvar returns
        "historical_expected_shortfall" ->
          PreparedSingle SingleHistoricalExpectedShortfall returns
        "realized_variance" -> PreparedSingle SingleRealizedVariance returns
        "realized_volatility_intraday" ->
          PreparedSingle SingleRealizedVolatilityIntraday returns
        "lo_adjusted_sharpe_ratio" ->
          PreparedSingle SingleLoAdjustedSharpeRatio returns
        "probabilistic_sharpe_ratio" ->
          PreparedPsr (U.toList (U.take 16384 (frozenReturns inputs)))
        "deflated_sharpe_ratio" ->
          PreparedDsr
            ( prepareDsrInputs
                (U.toList (U.take 16384 (frozenReturns inputs)))
            )
        "kupiec_unconditional_coverage_test" ->
          PreparedCoverage CoverageKupiec (coveragePairs inputs size)
        "christoffersen_independence_test" ->
          PreparedCoverage CoverageChristoffersenIndependence (coveragePairs inputs size)
        "christoffersen_conditional_coverage_test" ->
          PreparedCoverage CoverageChristoffersenConditional (coveragePairs inputs size)
        _ -> PreparedPsr []

runPrepared :: PreparedCase -> [Either StableError NumericResult]
runPrepared prepared =
  case prepared of
    PreparedSingle functionId values -> [runSingle functionId values]
    PreparedPsr observed -> fmap runPsr observed
    PreparedDsr inputs -> fmap runDsr inputs
    PreparedCoverage functionId pairs -> fmap (runCoverage functionId) pairs

runSingle :: SingleFunction -> U.Vector Double -> Either StableError NumericResult
runSingle functionId values =
  case functionId of
    SingleSimpleReturns -> VectorResult <$> simpleReturns values
    SingleLogReturns -> VectorResult <$> logReturns values
    SingleCumulativeReturn -> ScalarResult <$> cumulativeReturn values
    SingleCagr -> ScalarResult <$> cagr values 252
    SingleRealizedVolatility -> ScalarResult <$> realizedVolatility values
    SingleAnnualizedVolatility -> ScalarResult <$> annualizedVolatility values 252
    SingleMaxDrawdown -> ScalarResult <$> maxDrawdown values
    SingleSharpeRatio -> ScalarResult <$> sharpeRatio values 0.0 252
    SingleSortinoRatio -> ScalarResult <$> sortinoRatio values 0.0 252
    SingleHistoricalVar -> ScalarResult <$> historicalVar values 0.95
    SingleHistoricalCvar -> ScalarResult <$> historicalCvar values 0.95
    SingleHistoricalExpectedShortfall ->
      ScalarResult <$> historicalExpectedShortfall values 0.95
    SingleRealizedVariance -> ScalarResult <$> realizedVariance values
    SingleRealizedVolatilityIntraday ->
      ScalarResult <$> realizedVolatilityIntraday values
    SingleLoAdjustedSharpeRatio ->
      ScalarResult <$> loAdjustedSharpeRatio values 5 0.0

runPsr :: Double -> Either StableError NumericResult
runPsr observed =
  ScalarResult <$> probabilisticSharpeRatio observed 0.0 252 0.0 3.0

runDsr :: DsrInput -> Either StableError NumericResult
runDsr (DsrInput observed trialCount provenance) =
  ScalarResult
    <$> deflatedSharpeRatio
      observed
      252
      0.0
      3.0
      trialCount
      1.0
      provenance

runCoverage ::
  CoverageFunction ->
  (U.Vector Double, U.Vector Double) ->
  Either StableError NumericResult
runCoverage functionId (realized, forecast) =
  case functionId of
    CoverageKupiec ->
      LikelihoodRecord
        <$> kupiecUnconditionalCoverageTest realized forecast 0.95 0.05
    CoverageChristoffersenIndependence ->
      IndependenceRecord
        <$> christoffersenIndependenceTest realized forecast 0.05
    CoverageChristoffersenConditional ->
      ConditionalCoverageRecord
        <$> christoffersenConditionalCoverageTest realized forecast 0.95 0.05

prepareDsrInputs :: [Double] -> [DsrInput]
prepareDsrInputs observed =
  zipWith
    (\value trialCount -> DsrInput value trialCount (benchmarkProvenance trialCount))
    observed
    benchmarkTrialCounts

benchmarkTrialCounts :: [Integer]
benchmarkTrialCounts =
  replicate 5462 2
    <> replicate 5461 (10 ^ (20 :: Int))
    <> replicate 5461 (10 ^ (308 :: Int))

benchmarkProvenance :: Integer -> TrialProvenance
benchmarkProvenance trialCount =
  TrialProvenance
    "s1.4r-effective-trials-v1"
    "externally_estimated_effective_count"
    trialCount
    trialCount
    "daily"
    "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    1

coveragePairs :: FrozenInputs -> Int -> [(U.Vector Double, U.Vector Double)]
coveragePairs inputs size =
  [ ( U.slice (sequenceIndex * size) size (frozenRealizedLosses inputs),
      U.slice (sequenceIndex * size) size (frozenForecastVars inputs)
    )
    | sequenceIndex <- [0 .. 31]
  ]

loadFrozenInputs :: FilePath -> IO FrozenInputs
loadFrozenInputs fixtureRoot = do
  let largeRoot = fixtureRoot </> "large"
  prices <-
    loadBinary
      largeRoot
      "large-prices-n100000.manifest.json"
      "large-prices-n100000"
      "prices"
      "large-prices-n100000.f64le"
      100000
  returns <-
    loadBinary
      largeRoot
      "large-returns-n100000.manifest.json"
      "large-returns-n100000"
      "returns"
      "large-returns-n100000.f64le"
      100000
  realized <-
    loadBinary
      largeRoot
      "large-coverage-realized-losses-n3200000.manifest.json"
      "large-coverage-realized-losses-n3200000"
      "realized_losses"
      "large-coverage-realized-losses-n3200000.f64le"
      3200000
  forecast <-
    loadBinary
      largeRoot
      "large-coverage-forecast-var-n3200000.manifest.json"
      "large-coverage-forecast-var-n3200000"
      "forecast_vars"
      "large-coverage-forecast-var-n3200000.f64le"
      3200000
  pure (FrozenInputs prices returns realized forecast)

loadBinary ::
  FilePath ->
  FilePath ->
  Text ->
  Text ->
  Text ->
  Int ->
  IO (U.Vector Double)
loadBinary
  largeRoot
  manifestName
  expectedFixtureId
  expectedArgumentName
  expectedFileName
  expectedCount = do
    manifest <- decodeFile (largeRoot </> manifestName)
    validateManifest
      manifest
      expectedFixtureId
      expectedArgumentName
      expectedFileName
      expectedCount
    let generatedRoot = largeRoot </> "generated"
        binaryPath = generatedRoot </> Text.unpack (manifestFileName manifest)
    generatedExists <- doesDirectoryExist generatedRoot
    binaryExists <- doesFileExist binaryPath
    binaryLink <- if binaryExists then pathIsSymbolicLink binaryPath else pure False
    unless
      (generatedExists && binaryExists && not binaryLink)
      (fail ("benchmark binary fixture missing or unsafe: " <> binaryPath))
    generatedCanonical <- canonicalizePath generatedRoot
    binaryCanonical <- canonicalizePath binaryPath
    unless
      (takeDirectory binaryCanonical == generatedCanonical)
      (fail ("benchmark binary fixture escaped generated root: " <> binaryPath))
    payload <- BS.readFile binaryCanonical
    unless
      ( BS.length payload == manifestByteLength manifest
          && sha256Hex payload == TextEncoding.encodeUtf8 (manifestSha256 manifest)
      )
      (fail ("benchmark binary fixture identity mismatch: " <> binaryPath))
    let values =
          runGet
            (U.replicateM (manifestCount manifest) getDoublele)
            (LBS.fromStrict payload)
    unless
      (U.length values == expectedCount && U.all finite values)
      (fail ("benchmark binary fixture values invalid: " <> binaryPath))
    pure values

validateManifest ::
  BinaryManifest ->
  Text ->
  Text ->
  Text ->
  Int ->
  IO ()
validateManifest manifest expectedFixtureId expectedArgumentName expectedFileName expectedCount =
  unless
    ( manifestSchemaVersion manifest == "s1.4x-binary-array-v1"
        && manifestFixtureId manifest == expectedFixtureId
        && manifestArgumentName manifest == expectedArgumentName
        && manifestFileName manifest == expectedFileName
        && takeFileName (Text.unpack (manifestFileName manifest))
          == Text.unpack (manifestFileName manifest)
        && manifestEncoding manifest == "ieee754-binary64"
        && manifestDtype manifest == "float64"
        && manifestByteOrder manifest == "little"
        && manifestArrayOrder manifest == "C"
        && manifestShape manifest == [expectedCount]
        && manifestCount manifest == expectedCount
        && manifestByteLength manifest == expectedCount * 8
        && validSha256 (manifestSha256 manifest)
    )
    (fail ("benchmark binary manifest mismatch: " <> Text.unpack expectedFixtureId))

finite :: Double -> Bool
finite value = not (isNaN value || isInfinite value)

validSha256 :: Text -> Bool
validSha256 digest =
  Text.length digest == 64
    && Text.all
      (\character -> character `elem` ("0123456789abcdef" :: String))
      digest

forceVector :: U.Vector Double -> ()
forceVector =
  U.foldl'
    (\unit value -> rnf value `seq` unit)
    ()

functionSpecification ::
  Text ->
  Maybe (Int -> (Text, [Int], Int, Int, Value, Text, Text))
functionSpecification functionId =
  case functionId of
    "simple_returns" -> Just (standardSpecification "path-transform" "prices" functionId noArguments)
    "log_returns" -> Just (standardSpecification "path-transform" "prices" functionId noArguments)
    "cumulative_return" ->
      Just (standardSpecification "path-transform" "returns" functionId noArguments)
    "cagr" ->
      Just
        ( standardSpecification
            "classical-path-risk"
            "prices"
            functionId
            (object ["periods_per_year" .= (252 :: Integer)])
        )
    "realized_volatility" ->
      Just (standardSpecification "classical-path-risk" "returns" functionId noArguments)
    "annualized_volatility" ->
      Just
        ( standardSpecification
            "classical-path-risk"
            "returns"
            functionId
            (object ["periods_per_year" .= (252 :: Integer)])
        )
    "max_drawdown" ->
      Just (standardSpecification "classical-path-risk" "prices" functionId noArguments)
    "sharpe_ratio" ->
      Just
        ( standardSpecification
            "classical-path-risk"
            "returns"
            functionId
            ( object
                [ "periods_per_year" .= (252 :: Integer),
                  "risk_free_rate" .= (0.0 :: Double)
                ]
            )
        )
    "sortino_ratio" ->
      Just
        ( standardSpecification
            "classical-path-risk"
            "returns"
            functionId
            ( object
                [ "periods_per_year" .= (252 :: Integer),
                  "target_return" .= (0.0 :: Double)
                ]
            )
        )
    "historical_var" ->
      Just (confidenceSpecification functionId)
    "historical_cvar" ->
      Just (confidenceSpecification functionId)
    "historical_expected_shortfall" ->
      Just (confidenceSpecification functionId)
    "realized_variance" ->
      Just (standardSpecification "intraday-realized" "returns" functionId noArguments)
    "realized_volatility_intraday" ->
      Just (standardSpecification "intraday-realized" "returns" functionId noArguments)
    "lo_adjusted_sharpe_ratio" ->
      Just loSpecification
    "probabilistic_sharpe_ratio" ->
      Just psrSpecification
    "deflated_sharpe_ratio" ->
      Just dsrSpecification
    "kupiec_unconditional_coverage_test" ->
      Just (coverageSpecification functionId "kupiec_pof" True)
    "christoffersen_independence_test" ->
      Just (coverageSpecification functionId "christoffersen_independence" False)
    "christoffersen_conditional_coverage_test" ->
      Just (coverageSpecification functionId "christoffersen_conditional_coverage" True)
    _ -> Nothing

standardSpecification ::
  Text ->
  Text ->
  Text ->
  Value ->
  Int ->
  (Text, [Int], Int, Int, Value, Text, Text)
standardSpecification familyId fixtureKind functionId arguments size =
  ( familyId,
    standardSizes,
    1,
    1,
    arguments,
    "large-" <> fixtureKind <> "-n100000-prefix-n" <> decimal size,
    familyId <> "/" <> functionId <> "/n" <> decimal size <> "/b1"
  )

confidenceSpecification ::
  Text ->
  Int ->
  (Text, [Int], Int, Int, Value, Text, Text)
confidenceSpecification functionId =
  standardSpecification
    "classical-path-risk"
    "returns"
    functionId
    (object ["confidence" .= (0.95 :: Double)])

loSpecification :: Int -> (Text, [Int], Int, Int, Value, Text, Text)
loSpecification size =
  ( "serial-sharpe",
    standardSizes,
    1,
    1,
    object
      [ "aggregation_periods" .= (5 :: Integer),
        "risk_free_rate" .= (0.0 :: Double)
      ],
    "large-returns-n100000-prefix-n" <> decimal size,
    "serial-sharpe/lo_adjusted_sharpe_ratio/n" <> decimal size <> "/q5/b1"
  )

psrSpecification :: Int -> (Text, [Int], Int, Int, Value, Text, Text)
psrSpecification _ =
  ( "probabilistic-scalar",
    [16384],
    16384,
    16384,
    object
      [ "benchmark_sharpe" .= (0.0 :: Double),
        "kurtosis" .= (3.0 :: Double),
        "sample_size" .= (252 :: Integer),
        "skewness" .= (0.0 :: Double)
      ],
    "precomputed-probabilistic_sharpe_ratio-b16384",
    "probabilistic-scalar/probabilistic_sharpe_ratio/b16384"
  )

dsrSpecification :: Int -> (Text, [Int], Int, Int, Value, Text, Text)
dsrSpecification _ =
  ( "probabilistic-scalar",
    [16384],
    16384,
    16384,
    object
      [ "kurtosis" .= (3.0 :: Double),
        "sample_size" .= (252 :: Integer),
        "sharpe_estimate_variance" .= (1.0 :: Double),
        "skewness" .= (0.0 :: Double),
        "trial_count_mix"
          .= [ object
                 [ "evaluation_count" .= (5462 :: Integer),
                   "trial_count" .= (2 :: Integer)
                 ],
               object
                 [ "evaluation_count" .= (5461 :: Integer),
                   "trial_count" .= (10 ^ (20 :: Int) :: Integer)
                 ],
               object
                 [ "evaluation_count" .= (5461 :: Integer),
                   "trial_count" .= (10 ^ (308 :: Int) :: Integer)
                 ]
             ],
        "trial_count_provenance"
          .= ("externally_estimated_effective_count" :: Text)
      ],
    "precomputed-deflated_sharpe_ratio-b16384",
    "probabilistic-scalar/deflated_sharpe_ratio/b16384"
  )

coverageSpecification ::
  Text ->
  Text ->
  Bool ->
  Int ->
  (Text, [Int], Int, Int, Value, Text, Text)
coverageSpecification functionId caseName needsConfidence size =
  ( "coverage-batch",
    coverageSizes,
    32,
    32,
    if needsConfidence
      then
        object
          [ "confidence" .= (0.95 :: Double),
            "significance" .= (0.05 :: Double)
          ]
      else object ["significance" .= (0.05 :: Double)],
    "large-coverage-pair-n3200000/prefix-n"
      <> decimal size
      <> "-sequences-b32",
    "coverage-batch/" <> caseName <> "/n" <> decimal size <> "/b32"
  )

noArguments :: Value
noArguments = object []

decimal :: Int -> Text
decimal = Text.pack . show

standardSizes :: [Int]
standardSizes = [32, 252, 1000, 10000, 100000]

coverageSizes :: [Int]
coverageSizes = [252, 1000, 10000, 100000]

expectedCaseIds :: [Text]
expectedCaseIds =
  concatMap (standardCaseIds "path-transform") pathFunctions
    <> concatMap (standardCaseIds "classical-path-risk") classicalFunctions
    <> concatMap (standardCaseIds "intraday-realized") intradayFunctions
    <> [ "serial-sharpe/lo_adjusted_sharpe_ratio/n"
           <> decimal size
           <> "/q5/b1"
         | size <- standardSizes
       ]
    <> [ "probabilistic-scalar/probabilistic_sharpe_ratio/b16384",
         "probabilistic-scalar/deflated_sharpe_ratio/b16384"
       ]
    <> concatMap coverageCaseIds coverageCaseNames
  where
    standardCaseIds familyId functionId =
      [ familyId <> "/" <> functionId <> "/n" <> decimal size <> "/b1"
        | size <- standardSizes
      ]
    coverageCaseIds caseName =
      [ "coverage-batch/" <> caseName <> "/n" <> decimal size <> "/b32"
        | size <- coverageSizes
      ]

pathFunctions :: [Text]
pathFunctions = ["simple_returns", "log_returns", "cumulative_return"]

classicalFunctions :: [Text]
classicalFunctions =
  [ "cagr",
    "realized_volatility",
    "annualized_volatility",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
    "historical_var",
    "historical_cvar",
    "historical_expected_shortfall"
  ]

intradayFunctions :: [Text]
intradayFunctions = ["realized_variance", "realized_volatility_intraday"]

coverageCaseNames :: [Text]
coverageCaseNames =
  [ "kupiec_pof",
    "christoffersen_independence",
    "christoffersen_conditional_coverage"
  ]

expectedSelectorIdentities :: [(Text, Text, Text, Maybe Text)]
expectedSelectorIdentities =
  [ ( "haskell/" <> familyId,
      familyId,
      "prefix",
      Just (familyId <> "/")
    )
    | familyId <-
        [ "path-transform",
          "classical-path-risk",
          "intraday-realized",
          "serial-sharpe",
          "probabilistic-scalar",
          "coverage-batch"
        ]
  ]

traverse_ :: (value -> Either String ()) -> [value] -> Either String ()
traverse_ action = foldr (\value rest -> action value >> rest) (Right ())
