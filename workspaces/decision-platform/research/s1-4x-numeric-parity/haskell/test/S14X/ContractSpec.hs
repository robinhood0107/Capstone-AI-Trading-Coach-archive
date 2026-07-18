module S14X.ContractSpec (tests) where

import Data.ByteString (ByteString)
import Data.Foldable (traverse_)
import Data.Version (showVersion)
import System.Info (compilerName, fullCompilerVersion)
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit ((@?=), assertBool, assertFailure, testCase)

import qualified Data.ByteString.Char8 as BS8
import qualified Data.Text as Text

import S14X.Contract.Process
  ( encodeResultBatch,
    implementationLabel,
    parseRequest,
    runRequest,
    sha256Hex,
  )
import S14X.Contract.Types
  ( RequestBatch (RequestBatch),
    ResultBatch (ResultBatch),
    TransportCode (ManifestInvalid, RequestInvalid),
    transportCode,
  )

tests :: TestTree
tests =
  testGroup
    "process-contract"
    [ testCase "strict parser rejects duplicate decoded keys" duplicateKeys,
      testCase "exact integer token kind is preserved" integerTokenKind,
      testCase "Unicode digits cannot enter SHA or path contracts" unicodeDigitsRejected,
      testCase "canonical request executes in order" canonicalRequest,
      testCase "result implementation follows the active compiler" compilerIdentity,
      testCase "recursive result encoding normalizes negative zero" negativeZero,
      testCase "pure SHA-256 has known answers" shaKnownAnswers
    ]

duplicateKeys :: IO ()
duplicateKeys =
  case parseRequest duplicateRequest of
    Left transport -> transportCode transport @?= RequestInvalid
    Right _ -> assertFailure "duplicate request key must be rejected"

integerTokenKind :: IO ()
integerTokenKind =
  case parseRequest decimalIntegerRequest of
    Left transport -> assertFailure ("unexpected transport error: " <> show transport)
    Right request -> do
      result <- runRequest "." request
      case result of
        Left transport -> assertFailure ("unexpected transport error: " <> show transport)
        Right batch ->
          assertBool
            "decimal periods_per_year must remain a semantic integer-kind error"
            ("periods_per_year_invalid" `BS8.isInfixOf` encodeResultBatch batch)

unicodeDigitsRejected :: IO ()
unicodeDigitsRejected =
  traverse_ rejectsManifest [unicodeDigitShaRequest, unicodeDigitPathRequest]
  where
    rejectsManifest payload =
      case parseRequest payload of
        Left transport -> assertFailure ("unexpected request error: " <> show transport)
        Right request -> do
          result <- runRequest "tools/fixtures/process" request
          case result of
            Left transport -> transportCode transport @?= ManifestInvalid
            Right _ -> assertFailure "Unicode digit manifest must fail closed"

canonicalRequest :: IO ()
canonicalRequest =
  case parseRequest smallRequest of
    Left transport -> assertFailure ("unexpected transport error: " <> show transport)
    Right request@(RequestBatch _ _ cases) -> do
      length cases @?= 2
      result <- runRequest "." request
      case result of
        Left transport -> assertFailure ("unexpected transport error: " <> show transport)
        Right (ResultBatch _ _ results) -> length results @?= 2

compilerIdentity :: IO ()
compilerIdentity = do
  let expected =
        Text.pack ("haskell-" <> compilerName <> "-" <> showVersion fullCompilerVersion)
  implementationLabel @?= expected
  case parseRequest smallRequest of
    Left transport -> assertFailure ("unexpected transport error: " <> show transport)
    Right request -> do
      result <- runRequest "." request
      case result of
        Left transport -> assertFailure ("unexpected transport error: " <> show transport)
        Right (ResultBatch _ implementation _) -> implementation @?= expected

negativeZero :: IO ()
negativeZero =
  case parseRequest negativeZeroRequest of
    Left transport -> assertFailure ("unexpected transport error: " <> show transport)
    Right request -> do
      result <- runRequest "." request
      case result of
        Left transport -> assertFailure ("unexpected transport error: " <> show transport)
        Right batch ->
          assertBool
            "encoded output must not contain negative zero"
            (not ("-0.0" `BS8.isInfixOf` encodeResultBatch batch))

shaKnownAnswers :: IO ()
shaKnownAnswers = do
  sha256Hex "" @?= "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  sha256Hex "abc" @?= "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

duplicateRequest :: ByteString
duplicateRequest =
  "{\"schemaVersion\":\"s1.4x-request-v1\",\"requestId\":\"a\",\"requestId\":\"b\","
    <> "\"cases\":[{\"fixtureId\":\"x\",\"functionId\":\"simple_returns\","
    <> "\"arguments\":{\"prices\":[1,2]}}]}"

decimalIntegerRequest :: ByteString
decimalIntegerRequest =
  "{\"schemaVersion\":\"s1.4x-request-v1\",\"requestId\":\"integer-kind\","
    <> "\"cases\":[{\"fixtureId\":\"integer-kind-case\",\"functionId\":\"cagr\","
    <> "\"arguments\":{\"prices\":[100,101],\"periods_per_year\":1000.0}}]}"

smallRequest :: ByteString
smallRequest =
  "{\"schemaVersion\":\"s1.4x-request-v1\",\"requestId\":\"small\","
    <> "\"cases\":["
    <> "{\"fixtureId\":\"a\",\"functionId\":\"simple_returns\","
    <> "\"arguments\":{\"prices\":[100,200,100]}},"
    <> "{\"fixtureId\":\"b\",\"functionId\":\"cumulative_return\","
    <> "\"arguments\":{\"returns\":[0.1,-0.1]}}]}"

negativeZeroRequest :: ByteString
negativeZeroRequest =
  "{\"schemaVersion\":\"s1.4x-request-v1\",\"requestId\":\"negative-zero\","
    <> "\"cases\":[{\"fixtureId\":\"zero\",\"functionId\":\"log_returns\","
    <> "\"arguments\":{\"prices\":[100,100]}}]}"

unicodeDigitShaRequest :: ByteString
unicodeDigitShaRequest =
  binaryManifestRequest "unicode-digit-sha" "unicode-digit-sha.manifest.json"

unicodeDigitPathRequest :: ByteString
unicodeDigitPathRequest =
  binaryManifestRequest "unicode-digit-path" "unicode-digit-path.manifest.json"

binaryManifestRequest :: ByteString -> ByteString -> ByteString
binaryManifestRequest fixtureId manifestFile =
  "{\"schemaVersion\":\"s1.4x-request-v1\",\"requestId\":\"unicode-digit-contract\","
    <> "\"cases\":[{\"fixtureId\":\""
    <> fixtureId
    <> "\",\"functionId\":\"simple_returns\",\"arguments\":{\"prices\":"
    <> "{\"kind\":\"binaryFloat64\",\"manifestFile\":\""
    <> manifestFile
    <> "\"}}}]}"
