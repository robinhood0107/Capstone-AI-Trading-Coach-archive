module Main (main) where

import Test.Tasty (TestTree, defaultMain, localOption, testGroup)
import Test.Tasty.QuickCheck (QuickCheckTests (QuickCheckTests))
import System.Environment (getArgs)

import qualified S14X.AtomicOutputSpec as AtomicOutputSpec
import qualified S14X.BenchmarkStaticSpec as BenchmarkStaticSpec
import qualified S14X.BenchmarkValidationSpec as BenchmarkValidationSpec
import qualified S14X.ContractSpec as ContractSpec
import qualified S14X.CoreSpec as CoreSpec
import qualified S14X.PropertyEvidence as PropertyEvidence
import qualified S14X.PropertyEvidenceSpec as PropertyEvidenceSpec
import qualified S14X.PropertySpec as PropertySpec
import qualified S14X.StaticPolicySpec as StaticPolicySpec

main :: IO ()
main = do
  arguments <- getArgs
  case arguments of
    "--s1-4x-property-evidence" : evidenceArguments ->
      PropertyEvidence.runPropertyEvidence evidenceArguments
    _ -> defaultMain tests

tests :: TestTree
tests =
  testGroup
    "s1.4x-haskell"
    [ CoreSpec.tests,
      localOption (QuickCheckTests 1000) PropertySpec.tests,
      AtomicOutputSpec.tests,
      BenchmarkStaticSpec.tests,
      BenchmarkValidationSpec.tests,
      ContractSpec.tests,
      PropertyEvidenceSpec.tests,
      StaticPolicySpec.tests
    ]
