module Main (main) where

import Test.Tasty (TestTree, defaultMain, localOption, testGroup)
import Test.Tasty.QuickCheck (QuickCheckTests (QuickCheckTests))

import qualified S14X.ContractSpec as ContractSpec
import qualified S14X.CoreSpec as CoreSpec
import qualified S14X.PropertySpec as PropertySpec
import qualified S14X.StaticPolicySpec as StaticPolicySpec

main :: IO ()
main = defaultMain tests

tests :: TestTree
tests =
  testGroup
    "s1.4x-haskell"
    [ CoreSpec.tests,
      localOption (QuickCheckTests 1000) PropertySpec.tests,
      ContractSpec.tests,
      StaticPolicySpec.tests
    ]
