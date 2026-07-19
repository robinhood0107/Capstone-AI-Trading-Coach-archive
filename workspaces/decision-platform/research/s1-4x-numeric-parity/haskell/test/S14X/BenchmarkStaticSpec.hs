module S14X.BenchmarkStaticSpec (tests) where

import           Data.Aeson (FromJSON (parseJSON), eitherDecodeFileStrict', withObject, (.:))
import           Data.Bifunctor (second)
import           Data.List (isInfixOf)
import           Data.Map.Strict (Map)
import           Data.Text (Text)
import           Test.Tasty (TestTree, testGroup)
import           Test.Tasty.HUnit (assertBool, assertFailure, testCase, (@?=))

import qualified Data.Map.Strict as Map

data BenchmarkPlan = BenchmarkPlan [BenchmarkCase] [FamilySelector]
  deriving stock (Eq, Show)

data BenchmarkCase = BenchmarkCase Text Text
  deriving stock (Eq, Show)

data FamilySelector = FamilySelector Text Text [Text]
  deriving stock (Eq, Show)

instance FromJSON BenchmarkPlan where
  parseJSON =
    withObject "BenchmarkPlan" $ \objectValue ->
      BenchmarkPlan
        <$> objectValue .: "cases"
        <*> objectValue .: "familySelectors"

instance FromJSON BenchmarkCase where
  parseJSON =
    withObject "BenchmarkCase" $ \objectValue ->
      BenchmarkCase
        <$> objectValue .: "caseId"
        <*> objectValue .: "familyId"

instance FromJSON FamilySelector where
  parseJSON =
    withObject "FamilySelector" $ \objectValue ->
      FamilySelector
        <$> objectValue .: "boundaryId"
        <*> objectValue .: "familyId"
        <*> objectValue .: "expectedCaseIds"

tests :: TestTree
tests =
  testGroup
    "benchmark-static-contract"
    [ testCase "frozen plan exposes exact Haskell 89-case closure" exactCaseClosure,
      testCase "Criterion harness loads frozen binaries outside timed kernels" frozenFixtureHarness,
      testCase "DSR and coverage batches preserve frozen contiguous construction" batchConstruction
    ]

exactCaseClosure :: IO ()
exactCaseClosure = do
  plan <- decodePlan
  let BenchmarkPlan cases selectors = plan
      caseFamilies =
        Map.fromListWith
          (+)
          [(familyId, 1 :: Int) | BenchmarkCase _ familyId <- cases]
      haskellSelectors =
        [ (familyId, caseIds)
          | FamilySelector boundaryId familyId caseIds <- selectors,
            boundaryId == "haskell"
        ]
  length cases @?= 89
  caseFamilies @?= expectedFamilyCounts
  Map.fromList (fmap (second length) haskellSelectors)
    @?= expectedFamilyCounts

frozenFixtureHarness :: IO ()
frozenFixtureHarness = do
  source <- readFile "benchmark/Main.hs"
  let required =
        [ "large-prices-n100000.manifest.json",
          "large-returns-n100000.manifest.json",
          "large-coverage-realized-losses-n3200000.manifest.json",
          "large-coverage-forecast-var-n3200000.manifest.json",
          "sha256Hex payload",
          "getDoublele",
          "env",
          "setupPreparedCase",
          "validateBenchmarkResults",
          "nf runPrepared"
        ]
      forbidden =
        [ "inputVector",
          "U.generate",
          "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        ]
  assertBool
    "Criterion harness is missing frozen fixture/deep-evaluation bindings"
    (all (`isInfixOf` source) required)
  assertBool
    "Criterion harness retains synthetic or stale fixture construction"
    (not (any (`isInfixOf` source) forbidden))

batchConstruction :: IO ()
batchConstruction = do
  source <- readFile "benchmark/Main.hs"
  let required =
        [ "replicate 5462 2",
          "replicate 5461 (10 ^ (20 :: Int))",
          "replicate 5461 (10 ^ (308 :: Int))",
          "externally_estimated_effective_count",
          "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
          "U.slice (sequenceIndex * size) size",
          "sequenceIndex <- [0 .. 31]"
        ]
      forbidden = ["batchIndex `mod`", "U.replicate (U.length values)"]
  assertBool
    "DSR or coverage frozen batch construction is incomplete"
    (all (`isInfixOf` source) required)
  assertBool
    "timed batch path retains modulo or per-call forecast allocation"
    (not (any (`isInfixOf` source) forbidden))

decodePlan :: IO BenchmarkPlan
decodePlan = do
  decoded <- eitherDecodeFileStrict' "../benchmarks/benchmark-plan.v1.json"
  case decoded of
    Left failure ->
      assertFailure ("benchmark plan decode failed: " <> failure)
        >> pure (BenchmarkPlan [] [])
    Right plan -> pure plan

expectedFamilyCounts :: Map Text Int
expectedFamilyCounts =
  Map.fromList
    [ ("path-transform", 15),
      ("classical-path-risk", 45),
      ("intraday-realized", 10),
      ("serial-sharpe", 5),
      ("probabilistic-scalar", 2),
      ("coverage-batch", 12)
    ]
