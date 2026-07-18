module S14X.PropertyEvidence
  ( canonicalOuterCommandSha256,
    runPropertyEvidence,
    toolchainProfile,
  )
where

import           Control.Monad (unless, when)
import           Data.Aeson (FromJSON (parseJSON), Value, eitherDecodeFileStrict',
                             eitherDecodeStrict', encode, object, withObject, (.:), (.=))
import           Data.ByteString (ByteString)
import           Data.Char (isDigit)
import           Data.List (sort)
import           Data.Text (Text)
import           Data.Time.Clock (UTCTime, getCurrentTime)
import           Data.Time.Format (defaultTimeLocale, formatTime)
import           System.Directory (doesDirectoryExist, doesPathExist, getExecutablePath,
                                   listDirectory, pathIsSymbolicLink)
import           System.FilePath (makeRelative, takeExtension, (</>))
import           Test.QuickCheck (Args, Property, Result, chatty, isSuccess, maxDiscardRatio,
                                  maxShrinks, maxSuccess, numDiscarded, numTests, output,
                                  quickCheckWithResult, replay, stdArgs)
import           Test.QuickCheck.Random (mkQCGen)

import qualified Data.ByteString as BS
import qualified Data.ByteString.Lazy as LBS
import qualified Data.Text as Text
import qualified Data.Text.Encoding as TextEncoding

import           S14X.Contract.AtomicOutput (PublishResult (AlreadyExists, Published),
                                             exclusiveAtomicWrite)
import           S14X.Contract.Process (implementationLabel, sha256Hex)
import           S14X.Contract.Types (FunctionId, functionIdText)
import           S14X.Core.Error (allStableErrors, stableErrorCode)
import           S14X.PropertyCases (PropertyCase (PropertyCase), propertyCases)

data SeedCorpus = SeedCorpus Text [Int]
  deriving stock (Eq, Show)

newtype FunctionRegistry = FunctionRegistry [Text]
  deriving stock (Eq, Show)

newtype ErrorRegistry = ErrorRegistry [ErrorEntry]
  deriving stock (Eq, Show)

data ErrorEntry = ErrorEntry
  { errorEntryCode :: Text,
    errorEntryTrack :: Text,
    errorEntryVerificationMode :: Text
  }
  deriving stock (Eq, Show)

data PropertyExecution = PropertyExecution
  { executionPropertyId :: String,
    executionSuccessful :: Int,
    executionDiscarded :: Int,
    executionSeedExecutions :: [SeedExecution]
  }
  deriving stock (Eq, Show)

data SeedExecution = SeedExecution
  { seedExecutionIndex :: Int,
    seedExecutionOriginalSeed :: Int,
    seedExecutionSuccessful :: Int,
    seedExecutionDiscarded :: Int
  }
  deriving stock (Eq, Show)

newtype SelectedProfile = SelectedProfile
  { selectedSourceTreeSha256 :: Text
  }
  deriving stock (Eq, Show)

instance FromJSON SeedCorpus where
  parseJSON =
    withObject "SeedCorpus" $ \objectValue ->
      SeedCorpus
        <$> objectValue .: "schemaVersion"
        <*> objectValue .: "seeds"

instance FromJSON FunctionRegistry where
  parseJSON =
    withObject "FunctionRegistry" $ \objectValue -> do
      entries <- objectValue .: "entries"
      FunctionRegistry <$> traverse parseFunctionEntry entries
    where
      parseFunctionEntry =
        withObject "FunctionEntry" $ \entry ->
          entry .: "functionId"

instance FromJSON ErrorRegistry where
  parseJSON =
    withObject "ErrorRegistry" $ \objectValue ->
      ErrorRegistry <$> objectValue .: "entries"

instance FromJSON ErrorEntry where
  parseJSON =
    withObject "ErrorEntry" $ \objectValue ->
      ErrorEntry
        <$> objectValue .: "code"
        <*> objectValue .: "track"
        <*> objectValue .: "verificationMode"

instance FromJSON SelectedProfile where
  parseJSON =
    withObject "SelectedProfile" $ \objectValue ->
      SelectedProfile <$> objectValue .: "sourceTreeSha256"

runPropertyEvidence :: [String] -> IO ()
runPropertyEvidence arguments =
  case arguments of
    [ outputDirectory,
      haskellRoot,
      propertyPlanPath,
      seedCorpusPath,
      functionRegistryPath,
      errorRegistryPath,
      outerRunnerPath,
      selectedProfilePath,
      sourceManifestPath,
      profileId,
      profileOptions,
      profileOptionsHash,
      buildArgumentsHash,
      selectedProfileHash,
      expectedSourceManifestHash,
      expectedSourceTreeHash,
      expectedPropertyClosureHash
      ] ->
      execute
        outputDirectory
        haskellRoot
        propertyPlanPath
        seedCorpusPath
        functionRegistryPath
        errorRegistryPath
        outerRunnerPath
        selectedProfilePath
        sourceManifestPath
        profileId
        profileOptions
        profileOptionsHash
        buildArgumentsHash
        selectedProfileHash
        expectedSourceManifestHash
        expectedSourceTreeHash
        expectedPropertyClosureHash
    _ ->
      fail
        "property evidence requires: OUTPUT_DIR HASKELL_ROOT PROPERTY_PLAN SEED_CORPUS FUNCTION_REGISTRY ERROR_REGISTRY OUTER_RUNNER SELECTED_PROFILE SOURCE_MANIFEST PROFILE_ID PROFILE_OPTIONS PROFILE_OPTIONS_SHA256 BUILD_ARGV_SHA256 SELECTED_PROFILE_SHA256 SOURCE_MANIFEST_SHA256 SOURCE_TREE_SHA256 PROPERTY_CLOSURE_SHA256"

execute ::
  FilePath ->
  FilePath ->
  FilePath ->
  FilePath ->
  FilePath ->
  FilePath ->
  FilePath ->
  FilePath ->
  FilePath ->
  String ->
  String ->
  String ->
  String ->
  String ->
  String ->
  String ->
  String ->
  IO ()
execute
  outputDirectory
  haskellRoot
  propertyPlanPath
  seedCorpusPath
  functionRegistryPath
  errorRegistryPath
  outerRunnerPath
  selectedProfilePath
  sourceManifestPath
  profileId
  profileOptions
  profileOptionsHash
  buildArgumentsHash
  selectedProfileHash
  expectedSourceManifestHash
  expectedSourceTreeHash
  expectedPropertyClosureHash = do
    validateAbsoluteInputs
      [ outputDirectory,
        haskellRoot,
        propertyPlanPath,
        seedCorpusPath,
        functionRegistryPath,
        errorRegistryPath,
        outerRunnerPath,
        selectedProfilePath,
        sourceManifestPath
      ]
    unless
      ( selectedProfilePath == haskellRoot </> "selected-profile.v1.json"
          && sourceManifestPath == haskellRoot </> "source-inputs.v1.json"
      )
      (fail "profile/manifest paths must be the fixed Haskell-root artifacts")
    validateProfileIdentity profileId profileOptions profileOptionsHash
    validateSha256 "build argv" buildArgumentsHash
    validateSha256 "selected profile" selectedProfileHash
    validateSha256 "source manifest" expectedSourceManifestHash
    validateSha256 "source tree" expectedSourceTreeHash
    validateSha256 "property closure" expectedPropertyClosureHash
    seedCorpus <- decodeFile seedCorpusPath
    functionRegistry <- decodeFile functionRegistryPath
    errorRegistry <- decodeFile errorRegistryPath
    validateSeedCorpus seedCorpus
    validateRegistries functionRegistry errorRegistry
    let SeedCorpus _ seeds = seedCorpus
    propertyPlanBytes <- BS.readFile propertyPlanPath
    seedCorpusBytes <- BS.readFile seedCorpusPath
    runnerBytes <- BS.readFile outerRunnerPath
    closureHash <-
      validateExecutionClosure
        haskellRoot
        selectedProfilePath
        sourceManifestPath
        (Text.pack selectedProfileHash)
        (Text.pack expectedSourceManifestHash)
        (Text.pack expectedSourceTreeHash)
        (Text.pack expectedPropertyClosureHash)
    executablePath <- getExecutablePath
    startedAt <- getCurrentTime
    executions <- traverse (runOneProperty seeds) propertyCases
    postExecutionClosureHash <-
      validateExecutionClosure
        haskellRoot
        selectedProfilePath
        sourceManifestPath
        (Text.pack selectedProfileHash)
        (Text.pack expectedSourceManifestHash)
        (Text.pack expectedSourceTreeHash)
        (Text.pack expectedPropertyClosureHash)
    unless
      (postExecutionClosureHash == closureHash)
      (fail "property execution closure changed while properties were running")
    finishedAt <- getCurrentTime
    let propertyPlanHash = sha256Text propertyPlanBytes
        runnerHash = sha256Text runnerBytes
        seedCorpusHash = sha256Text seedCorpusBytes
        outerCommandArgumentsHash =
          canonicalOuterCommandSha256 outerRunnerPath outputDirectory
        commandArgumentsHash =
          sha256Text
            ( LBS.toStrict
                ( encode
                    ( Text.pack executablePath
                        : "--s1-4x-property-evidence"
                        : fmap Text.pack arguments
                    )
                )
            )
        propertyReportPath = outputDirectory </> "haskell-property-report.v1.json"
        registryReportPath = outputDirectory </> "haskell-registry-report.v1.json"
        executionReportPath = outputDirectory </> "haskell-property-execution-evidence.v1.json"
        reportPaths = [propertyReportPath, registryReportPath, executionReportPath]
    collisions <- traverse doesPathExist reportPaths
    when (or collisions) (fail "property evidence output already exists")
    publishJson
      propertyReportPath
      (propertyReport propertyPlanHash executions)
    publishJson
      registryReportPath
      (registryReport functionRegistry errorRegistry)
    publishJson
      executionReportPath
      ( executionReport
          propertyPlanHash
          runnerHash
          closureHash
          commandArgumentsHash
          outerCommandArgumentsHash
          seedCorpusHash
          (Text.pack profileId)
          (Text.pack profileOptions)
          (Text.pack profileOptionsHash)
          (Text.pack buildArgumentsHash)
          (Text.pack selectedProfileHash)
          (Text.pack expectedSourceManifestHash)
          (Text.pack expectedSourceTreeHash)
          startedAt
          finishedAt
          seeds
          executions
      )

decodeFile :: FromJSON value => FilePath -> IO value
decodeFile path = do
  decoded <- eitherDecodeFileStrict' path
  case decoded of
    Left failure -> fail ("strict evidence input decode failed: " <> failure)
    Right value -> pure value

validateAbsoluteInputs :: [FilePath] -> IO ()
validateAbsoluteInputs paths =
  unless (all isAbsolutePath paths) (fail "property evidence paths must be absolute")
  where
    isAbsolutePath path =
      case path of
        '/' : _ -> True
        _ -> False

validateSeedCorpus :: SeedCorpus -> IO ()
validateSeedCorpus (SeedCorpus schemaVersion seeds) = do
  unless (schemaVersion == "s1.4x-property-seeds-v1") (fail "seed corpus schema mismatch")
  unless
    ( seeds
        == [ 0,
             1,
             2,
             3,
             5,
             8,
             13,
             21,
             34,
             55,
             89,
             144,
             233,
             377,
             610,
             987,
             1597,
             2584,
             4181,
             6765,
             10946,
             17711,
             28657,
             46368
           ]
    )
    (fail "seed corpus values mismatch")

validateProfileIdentity :: String -> String -> String -> IO ()
validateProfileIdentity profileId profileOptions profileOptionsHash = do
  let expectedOptions =
        case profileId of
          "baseline-o0-fasm" -> ["-O0", "-fasm"]
          "optimized-o2-fasm" -> ["-O2", "-fasm"]
          _ -> []
      actualOptions = words profileOptions
      actualHash =
        sha256Text
          (LBS.toStrict (encode (fmap Text.pack actualOptions)))
  unless (not (null expectedOptions) && actualOptions == expectedOptions)
    (fail "selected profile id/options mismatch")
  unless (Text.pack profileOptionsHash == actualHash)
    (fail "selected profile options SHA-256 mismatch")

validateSha256 :: String -> String -> IO ()
validateSha256 label digest =
  unless
    (length digest == 64 && all isLowerHex digest)
    (fail (label <> " SHA-256 must be lowercase hexadecimal"))
  where
    isLowerHex character =
      isDigit character
        || ('a' <= character && character <= 'f')

validateExecutionClosure ::
  FilePath ->
  FilePath ->
  FilePath ->
  Text ->
  Text ->
  Text ->
  Text ->
  IO Text
validateExecutionClosure
  haskellRoot
  selectedProfilePath
  sourceManifestPath
  expectedSelectedProfileHash
  expectedSourceManifestHash
  expectedSourceTreeHash
  expectedPropertyClosureHash = do
    selectedProfileBytes <- BS.readFile selectedProfilePath
    sourceManifestBytes <- BS.readFile sourceManifestPath
    unless
      (sha256Text selectedProfileBytes == expectedSelectedProfileHash)
      (fail "selected profile SHA-256 does not match the pre-build bytes")
    unless
      (sha256Text sourceManifestBytes == expectedSourceManifestHash)
      (fail "source manifest SHA-256 does not match the pre-build bytes")
    selectedProfile <-
      case eitherDecodeStrict' selectedProfileBytes of
        Left failure -> fail ("selected profile decode failed: " <> failure)
        Right value -> pure value
    unless
      (selectedSourceTreeSha256 selectedProfile == expectedSourceTreeHash)
      (fail "selected profile source-tree SHA-256 does not match the pre-build closure")
    closureHash <- sourceClosureSha256 haskellRoot
    unless
      (closureHash == expectedPropertyClosureHash)
      (fail "property source/config closure does not match the pre-build closure")
    selectedProfileBytesAfter <- BS.readFile selectedProfilePath
    sourceManifestBytesAfter <- BS.readFile sourceManifestPath
    unless
      ( selectedProfileBytesAfter == selectedProfileBytes
          && sourceManifestBytesAfter == sourceManifestBytes
      )
      (fail "profile/manifest bytes changed during closure validation")
    pure closureHash

validateRegistries :: FunctionRegistry -> ErrorRegistry -> IO ()
validateRegistries (FunctionRegistry functions) (ErrorRegistry errors) = do
  let expectedFunctions = fmap functionIdText ([minBound .. maxBound] :: [FunctionId])
      expectedErrors = fmap (Text.pack . stableErrorCode) allStableErrors
      actualErrors = fmap errorEntryCode errors
  unless (functions == expectedFunctions) (fail "function registry exact order mismatch")
  unless (actualErrors == expectedErrors) (fail "error registry exact order mismatch")
  unless (length functions == 20) (fail "function registry count mismatch")
  unless (length errors == 32) (fail "error registry count mismatch")

runOneProperty :: [Int] -> PropertyCase -> IO PropertyExecution
runOneProperty seeds (PropertyCase propertyId invariant) = do
  seedExecutions <-
    traverse
      (uncurry (runSeed propertyId invariant))
      (zip [0 ..] seeds)
  let successful = sum (fmap seedExecutionSuccessful seedExecutions)
      discarded = sum (fmap seedExecutionDiscarded seedExecutions)
  unless
    (all ((>= 42) . seedExecutionSuccessful) seedExecutions)
    (fail ("property per-seed success count below floor: " <> propertyId))
  unless (successful >= 1000) (fail ("property success count below floor: " <> propertyId))
  unless (discarded <= 100) (fail ("property discard count above cap: " <> propertyId))
  unless
    (discarded * 10 <= successful + discarded)
    (fail ("property discard ratio above cap: " <> propertyId))
  pure (PropertyExecution propertyId successful discarded seedExecutions)

runSeed ::
  String ->
  Property ->
  Int ->
  Int ->
  IO SeedExecution
runSeed propertyId invariant seedIndex seed = do
  result <- quickCheckWithResult (quickCheckArguments seed) invariant
  unless
    (isSuccess result)
    (fail ("property failed: " <> propertyId <> " seed=" <> show seed <> " " <> resultOutput result))
  pure
    ( SeedExecution
        seedIndex
        seed
        (numTests result)
        (numDiscarded result)
    )

quickCheckArguments :: Int -> Args
quickCheckArguments seed =
  stdArgs
    { chatty = False,
      maxSuccess = 42,
      maxDiscardRatio = 1,
      maxShrinks = 100,
      replay = Just (mkQCGen seed, 0)
    }

resultOutput :: Result -> String
resultOutput = output

propertyReport :: Text -> [PropertyExecution] -> Value
propertyReport propertyPlanHash executions =
  object
    [ "schemaVersion" .= ("s1.4x-candidate-property-coverage-v1" :: Text),
      "implementation" .= ("haskell" :: Text),
      "propertyPlanSha256" .= propertyPlanHash,
      "properties" .= fmap propertyCoverageValue executions,
      "status" .= ("PASS" :: Text)
    ]

propertyCoverageValue :: PropertyExecution -> Value
propertyCoverageValue execution =
  object
    [ "propertyId" .= executionPropertyId execution,
      "successfulTests" .= executionSuccessful execution,
      "discardedTests" .= executionDiscarded execution,
      "status" .= ("PASS" :: Text)
    ]

registryReport :: FunctionRegistry -> ErrorRegistry -> Value
registryReport (FunctionRegistry functions) (ErrorRegistry errors) =
  object
    [ "schemaVersion" .= ("s1.4x-candidate-registry-coverage-v1" :: Text),
      "implementation" .= ("haskell" :: Text),
      "functions" .= fmap functionCoverageValue functions,
      "errors" .= fmap errorCoverageValue errors,
      "status" .= ("PASS" :: Text)
    ]

functionCoverageValue :: Text -> Value
functionCoverageValue functionId =
  object
    [ "functionId" .= functionId,
      "status" .= ("PASS" :: Text)
    ]

errorCoverageValue :: ErrorEntry -> Value
errorCoverageValue entry =
  object
    [ "errorCode" .= errorEntryCode entry,
      "track" .= errorEntryTrack entry,
      "verificationMode" .= errorEntryVerificationMode entry,
      "status" .= ("PASS" :: Text)
    ]

executionReport ::
  Text ->
  Text ->
  Text ->
  Text ->
  Text ->
  Text ->
  Text ->
  Text ->
  Text ->
  Text ->
  Text ->
  Text ->
  Text ->
  UTCTime ->
  UTCTime ->
  [Int] ->
  [PropertyExecution] ->
  Value
executionReport
  propertyPlanHash
  runnerHash
  closureHash
  commandArgumentsHash
  outerCommandArgumentsHash
  seedCorpusHash
  profileId
  profileOptions
  profileOptionsHash
  buildArgumentsHash
  selectedProfileHash
  sourceManifestHash
  sourceTreeHash
  startedAt
  finishedAt
  seeds
  executions =
    object
      [ "schemaVersion" .= ("s1.4x-candidate-property-execution-v1" :: Text),
        "implementation" .= ("haskell" :: Text),
        "propertyPlanSha256" .= propertyPlanHash,
        "framework" .= ("QuickCheck-2.15.0.1" :: Text),
        "toolchainProfile" .= toolchainProfile profileId,
        "commandArgvSha256" .= commandArgumentsHash,
        "outerCommandArgvSha256" .= outerCommandArgumentsHash,
        "buildArgvSha256" .= buildArgumentsHash,
        "runnerSha256" .= runnerHash,
        "sourceClosureSha256" .= closureHash,
        "sourceInputManifestSha256" .= sourceManifestHash,
        "selectedProfileSha256" .= selectedProfileHash,
        "sourceTreeSha256" .= sourceTreeHash,
        "propertyClosureSha256" .= closureHash,
        "profileGhcOptions" .= Text.words profileOptions,
        "profileOptionsSha256" .= profileOptionsHash,
        "stackRootPathId" .= ("S1_4X_CACHE_ROOT/stack-root" :: Text),
        "seedCorpusSha256" .= seedCorpusHash,
        "seedCount" .= length seeds,
        "minimumSuccessfulPerSeed" .= (42 :: Int),
        "startedAt" .= utcText startedAt,
        "finishedAt" .= utcText finishedAt,
        "exitCode" .= (0 :: Int),
        "properties" .= fmap propertyExecutionValue executions,
        "status" .= ("PASS" :: Text)
      ]

propertyExecutionValue :: PropertyExecution -> Value
propertyExecutionValue execution =
  object
    [ "propertyId" .= executionPropertyId execution,
      "successfulTests" .= executionSuccessful execution,
      "discardedTests" .= executionDiscarded execution,
      "attemptedTests" .= (executionSuccessful execution + executionDiscarded execution),
      "seedCount" .= length (executionSeedExecutions execution),
      "seedExecutions" .= fmap seedExecutionValue (executionSeedExecutions execution),
      "shrinks" .= (0 :: Int),
      "status" .= ("PASS" :: Text)
    ]

seedExecutionValue :: SeedExecution -> Value
seedExecutionValue execution =
  object
    [ "seedIndex" .= seedExecutionIndex execution,
      "originalSeed" .= seedExecutionOriginalSeed execution,
      "successfulTests" .= seedExecutionSuccessful execution,
      "discardedTests" .= seedExecutionDiscarded execution,
      "attemptedTests"
        .= (seedExecutionSuccessful execution + seedExecutionDiscarded execution),
      "replayToken"
        .= ( "quickcheck-seed-v1:"
               <> Text.pack (show (seedExecutionOriginalSeed execution))
               <> ":size-0"
           ),
      "shrinks" .= (0 :: Int),
      "status" .= ("PASS" :: Text)
    ]

utcText :: UTCTime -> Text
utcText = Text.pack . formatTime defaultTimeLocale "%Y-%m-%dT%H:%M:%S%QZ"

sha256Text :: ByteString -> Text
sha256Text = TextEncoding.decodeUtf8 . sha256Hex

canonicalOuterCommandSha256 :: FilePath -> FilePath -> Text
canonicalOuterCommandSha256 runnerPath outputDirectory =
  sha256Text
    ( LBS.toStrict
        ( encode
            [ Text.pack runnerPath,
              "--output-dir",
              Text.pack outputDirectory
            ]
        )
    )

toolchainProfile :: Text -> Text
toolchainProfile profileId =
  implementationLabel <> "-" <> profileId

publishJson :: FilePath -> Value -> IO ()
publishJson path value = do
  publishResult <- exclusiveAtomicWrite path (LBS.toStrict (encode value))
  case publishResult of
    Published -> pure ()
    AlreadyExists -> fail ("property evidence output collision: " <> path)

sourceClosureSha256 :: FilePath -> IO Text
sourceClosureSha256 root = do
  files <- candidateClosureFiles root
  entries <- traverse (closureEntry root) files
  pure (sha256Text (BS.concat entries))

candidateClosureFiles :: FilePath -> IO [FilePath]
candidateClosureFiles root = do
  sourceFiles <- fmap concat (traverse (sourceFilesBelow root) ["src", "app", "test", "benchmark"])
  let configurationFiles =
        [ root </> "package.yaml",
          root </> "s1-4x-haskell.cabal",
          root </> "stack.yaml",
          root </> "stack-ghc-9.14.1.yaml",
          root </> "stack.yaml.lock",
          root </> "selected-profile.v1.json",
          root </> "source-inputs.v1.json"
        ]
  pure (sort (configurationFiles <> sourceFiles))

sourceFilesBelow :: FilePath -> FilePath -> IO [FilePath]
sourceFilesBelow root relative = do
  let directory = root </> relative
  rootSymbolic <- pathIsSymbolicLink directory
  when rootSymbolic (fail ("property source root symlink is forbidden: " <> directory))
  exists <- doesDirectoryExist directory
  if not exists
    then pure []
    else walk directory
  where
    walk directory = do
      entries <- sort <$> listDirectory directory
      nested <- traverse (visit directory) entries
      pure (concat nested)
    visit directory entry = do
      let path = directory </> entry
      symbolic <- pathIsSymbolicLink path
      when symbolic (fail ("property source symlink is forbidden: " <> path))
      isDirectory <- doesDirectoryExist path
      if isDirectory
        then walk path
        else pure [path | takeExtension path == ".hs"]

closureEntry :: FilePath -> FilePath -> IO ByteString
closureEntry root path = do
  symbolic <- pathIsSymbolicLink path
  when symbolic (fail ("property closure symlink is forbidden: " <> path))
  bytes <- BS.readFile path
  let relative = Text.pack (makeRelative root path)
      digest = sha256Text bytes
  pure (TextEncoding.encodeUtf8 (relative <> "\NUL" <> digest <> "\n"))
