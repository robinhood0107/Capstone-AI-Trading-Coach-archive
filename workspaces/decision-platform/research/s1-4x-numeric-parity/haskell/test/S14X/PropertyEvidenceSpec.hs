module S14X.PropertyEvidenceSpec (tests) where

import           Test.Tasty (TestTree, testGroup)
import           Test.Tasty.HUnit (testCase, (@?=))

import           S14X.Contract.Process (implementationLabel)
import           S14X.PropertyEvidence (canonicalOuterCommandSha256, toolchainProfile)

tests :: TestTree
tests =
  testGroup
    "property-evidence-contract"
    [ testCase "outer argv uses canonical JSON SHA-256" canonicalOuterArgv,
      testCase "toolchain profile follows result compiler identity" compilerIdentity
    ]

canonicalOuterArgv :: IO ()
canonicalOuterArgv =
  canonicalOuterCommandSha256
    "/tmp/run-property-evidence.sh"
    "/tmp/evidence"
    @?= "1c30c65d6b4b5964e01ca64d0b76871c2326b4b522a5a46e5ca98818d58ac6eb"

compilerIdentity :: IO ()
compilerIdentity =
  toolchainProfile @?= implementationLabel <> "-baseline-o0-fasm"
