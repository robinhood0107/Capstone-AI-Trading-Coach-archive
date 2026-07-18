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
      testCase "Stack configurations have no forbidden override keys" noStackOverrides
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
