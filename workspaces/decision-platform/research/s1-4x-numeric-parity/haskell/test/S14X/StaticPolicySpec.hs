module S14X.StaticPolicySpec (tests) where

import Data.List (isInfixOf, isSuffixOf)
import System.Directory (doesDirectoryExist, listDirectory)
import System.FilePath ((</>))
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit (assertBool, assertFailure, testCase)

tests :: TestTree
tests =
  testGroup
    "static-policy"
    [ testCase "candidate source has no forbidden native or unsafe forms" noForbiddenForms,
      testCase "every candidate module declares an explicit export list" explicitExports
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
            not (" where" `isInfixOf` content && "(" `isInfixOf` content)
        ]
  assertBool ("missing explicit export lists: " <> show missing) (null missing)

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
