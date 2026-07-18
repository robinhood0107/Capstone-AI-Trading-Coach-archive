module S14X.StaticPolicySpec (tests) where

import Data.Char (isSpace)
import Data.List (find, isInfixOf, isPrefixOf, isSuffixOf, tails)
import System.Directory (doesDirectoryExist, listDirectory)
import System.FilePath ((</>))
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit (assertBool, assertFailure, testCase)

tests :: TestTree
tests =
  testGroup
    "static-policy"
    [ testCase "candidate source has no forbidden native or unsafe forms" noForbiddenForms,
      testCase "every candidate module declares an explicit export list" explicitExports,
      testCase "Stack configurations have no forbidden override keys" noStackOverrides,
      testCase "core component cannot see contract shell dependencies" componentDependencyBoundary,
      testCase "formatter and HLint retain the frozen hard gates" formatterAndLintConfiguration,
      testCase
        "weighted autocorrelation uses the compensated sum primitive"
        weightedAutocorrelationUsesCompensatedSum,
      testCase "component entry modules have unique truthful names" uniqueComponentEntryModules
    ]

noForbiddenForms :: IO ()
noForbiddenForms = do
  files <- candidateSources
  contents <- traverse readFile files
  let forbidden =
        [ "foreign import",
          "foreign export",
          "unsafePerformIO",
          "unsafeCoerce",
          "System.IO.Unsafe",
          "GHC.IO.Unsafe",
          "Debug.Trace",
          "{-# OPTIONS_GHC",
          "{-# LANGUAGE Trustworthy",
          "{-# LANGUAGE Unsafe"
        ]
      violations =
        [ file <> ": " <> token
          | (file, content) <- zip files contents,
            token <- forbidden,
            token `isInfixOf` content
        ]
  assertBool ("forbidden source forms: " <> show violations) (null violations)

explicitExports :: IO ()
explicitExports = do
  files <- candidateSources
  contents <- traverse readFile files
  let missing =
        [ file
          | (file, content) <- zip files contents,
            "module " `isInfixOf` content,
            not (hasExplicitExportList content)
        ]
  assertBool ("missing explicit export lists: " <> show missing) (null missing)

hasExplicitExportList :: String -> Bool
hasExplicitExportList content =
  case find ("module " `isPrefixOf`) (tails content) >>= beforeMarker "where" of
    Nothing -> False
    Just header -> '(' `elem` header && ')' `elem` header

beforeMarker :: String -> String -> Maybe String
beforeMarker marker = go []
  where
    go reversedPrefix remaining
      | marker `isPrefixOf` remaining = Just (reverse reversedPrefix)
      | otherwise =
          case remaining of
            [] -> Nothing
            character : suffix -> go (character : reversedPrefix) suffix

noStackOverrides :: IO ()
noStackOverrides = do
  let files = ["stack.yaml", "stack-ghc-9.14.1.yaml"]
      forbiddenKeys = ["extra-deps", "drop-packages", "allow-newer", "allow-newer-deps"]
  contents <- traverse readFile files
  let violations =
        [ file <> ": " <> key
          | (file, content) <- zip files contents,
            line <- lines content,
            let key = takeWhile (/= ':') (dropWhile isSpace line),
            key `elem` forbiddenKeys
        ]
  assertBool ("forbidden Stack override keys: " <> show violations) (null violations)

componentDependencyBoundary :: IO ()
componentDependencyBoundary = do
  cabalFile <- readFile "s1-4x-haskell.cabal"
  case
      ( componentSection "library" cabalFile,
        componentSection "library s1-4x-core" cabalFile
      )
    of
      (Just shellSection, Just coreSection) -> do
        let shellOnlyPackages =
              [ "aeson",
                "attoparsec",
                "binary",
                "bytestring",
                "directory",
                "filepath",
                "SHA",
                "text",
                "unix"
              ]
            leaked =
              [ packageName
                | packageName <- shellOnlyPackages,
                  packageName `isInfixOf` coreSection
              ]
        assertBool ("shell dependency leaked into core component: " <> show leaked) (null leaked)
        assertBool
          "contract shell must depend on the internal core library"
          ("s1-4x-core" `isInfixOf` shellSection)
      _ -> assertFailure "generated Cabal must contain shell and s1-4x-core libraries"

componentSection :: String -> String -> Maybe String
componentSection header content =
  case dropWhile (/= header) (lines content) of
    [] -> Nothing
    _ : remaining ->
      Just
        ( unlines
            (takeWhile continuationLine remaining)
        )
  where
    continuationLine line =
      case line of
        [] -> True
        character : _ -> isSpace character

formatterAndLintConfiguration :: IO ()
formatterAndLintConfiguration = do
  stylish <- readFile ".stylish-haskell.yaml"
  hlint <- readFile ".hlint.yaml"
  let stylishRequirements =
        [ "newline: lf",
          "exit_code: error_on_format",
          "  - GHC2024"
        ]
      hlintRequirements =
        [ "-XGHC2024",
          "-XNoForeignFunctionInterface",
          "Data.Maybe.fromJust",
          "Data.Either.fromLeft",
          "Data.Either.fromRight",
          "Control.Exception.throw",
          "Control.Exception.throwIO",
          "foldl1",
          "maximum",
          "minimum",
          "Foreign",
          "System.IO.Unsafe",
          "GeneralizedNewtypeDeriving",
          "DerivingVia",
          "DeriveAnyClass"
        ]
  assertBool
    "stylish-haskell frozen configuration is incomplete"
    (all (`isInfixOf` stylish) stylishRequirements)
  assertBool
    "HLint frozen restrictions are incomplete"
    (all (`isInfixOf` hlint) hlintRequirements)

weightedAutocorrelationUsesCompensatedSum :: IO ()
weightedAutocorrelationUsesCompensatedSum = do
  source <- readFile "src/core/S14X/Core/AdvancedRisk.hs"
  section <-
    case sectionBetween "weightedCorrelationSum ::" "validatedPsrInputs ::" source of
      Nothing -> assertFailure "weightedCorrelationSum source section is missing" >> pure ""
      Just value -> pure value
  assertBool
    "weightedCorrelationSum must reduce its complete term vector through sumVector"
    ("sumVector weightedTerms" `isInfixOf` section)
  assertBool
    "weightedCorrelationSum must not use a naive total + weighted-term recurrence"
    (not ("total + weight" `isInfixOf` section))

sectionBetween :: String -> String -> String -> Maybe String
sectionBetween startMarker endMarker content = do
  start <- find (startMarker `isPrefixOf`) (tails content)
  beforeMarker endMarker start

uniqueComponentEntryModules :: IO ()
uniqueComponentEntryModules = do
  application <- readFile "app/Main.hs"
  testEntry <- readFile "test/Main.hs"
  benchmarkEntry <- readFile "benchmark/Main.hs"
  package <- readFile "package.yaml"
  assertBool "application entry module must remain Main" ("module Main (main) where" `isInfixOf` application)
  assertBool
    "test entry module must have a unique module-safety identity"
    ("module S14X.TestMain (main) where" `isInfixOf` testEntry)
  assertBool
    "benchmark entry module must have a unique module-safety identity"
    ("module S14X.BenchmarkMain (main) where" `isInfixOf` benchmarkEntry)
  assertBool
    "test component must bind its non-Main entry explicitly"
    ("-main-is S14X.TestMain.main" `isInfixOf` package)
  assertBool
    "benchmark component must bind its non-Main entry explicitly"
    ("-main-is S14X.BenchmarkMain.main" `isInfixOf` package)

haskellSources :: FilePath -> IO [FilePath]
haskellSources root = do
  exists <- doesDirectoryExist root
  if not exists
    then assertFailure ("source root missing: " <> root) >> pure []
    else walk root
  where
    walk directory = do
      entries <- listDirectory directory
      nested <- traverse (visit directory) entries
      pure (concat nested)
    visit directory entry = do
      let path = directory </> entry
      isDirectory <- doesDirectoryExist path
      if isDirectory && entry `notElem` [".stack-work", ".git"]
        then walk path
        else pure [path | ".hs" `isSuffixOf` path]

candidateSources :: IO [FilePath]
candidateSources = do
  groups <- traverse haskellSources ["src", "app", "benchmark"]
  pure (concat groups)
