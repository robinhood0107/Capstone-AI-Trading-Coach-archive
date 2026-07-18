module S14X.AtomicOutputSpec (tests) where

import           Control.Concurrent (MVar, forkIO, newEmptyMVar, putMVar, readMVar, takeMVar)
import           Control.Exception (SomeException, bracket, try)
import           Data.ByteString (ByteString)
import           Data.List (sort)
import           System.Directory (createDirectory, getTemporaryDirectory, listDirectory,
                                   removeDirectoryRecursive, removeFile)
import           System.FilePath ((</>))
import           System.IO (hClose, openBinaryTempFile)
import           Test.Tasty (TestTree, testGroup)
import           Test.Tasty.HUnit (assertBool, assertFailure, testCase, (@?=))

import qualified Data.ByteString as BS

import           S14X.Contract.AtomicOutput (PublishResult (AlreadyExists, Published),
                                             exclusiveAtomicWrite)

tests :: TestTree
tests =
  testGroup
    "exclusive-atomic-output"
    [ testCase "existing output is never replaced" existingOutputIsNeverReplaced,
      testCase "racing publishers expose exactly one complete winner" racingPublishersHaveOneWinner
    ]

existingOutputIsNeverReplaced :: IO ()
existingOutputIsNeverReplaced =
  withTemporaryDirectory $ \directory -> do
    let output = directory </> "result.json"
    first <- exclusiveAtomicWrite output "winner"
    second <- exclusiveAtomicWrite output "loser"
    bytes <- BS.readFile output
    entries <- listDirectory directory
    first @?= Published
    second @?= AlreadyExists
    bytes @?= "winner"
    entries @?= ["result.json"]

racingPublishersHaveOneWinner :: IO ()
racingPublishersHaveOneWinner =
  withTemporaryDirectory $ \directory -> do
    let output = directory </> "result.json"
        leftPayload = "left-complete-payload"
        rightPayload = "right-complete-payload"
    start <- newEmptyMVar
    leftReady <- newEmptyMVar
    rightReady <- newEmptyMVar
    leftDone <- newEmptyMVar
    rightDone <- newEmptyMVar
    _ <- forkIO (publisher start leftReady leftDone output leftPayload)
    _ <- forkIO (publisher start rightReady rightDone output rightPayload)
    takeMVar leftReady
    takeMVar rightReady
    putMVar start ()
    left <- takeMVar leftDone
    right <- takeMVar rightDone
    results <- traverse unwrapPublisher [left, right]
    bytes <- BS.readFile output
    entries <- listDirectory directory
    sort results @?= [Published, AlreadyExists]
    assertBool
      ("unexpected output bytes: " <> show bytes)
      (bytes == leftPayload || bytes == rightPayload)
    entries @?= ["result.json"]

publisher ::
  MVar () ->
  MVar () ->
  MVar (Either SomeException PublishResult) ->
  FilePath ->
  ByteString ->
  IO ()
publisher start ready done output payload = do
  putMVar ready ()
  readMVar start
  result <- try (exclusiveAtomicWrite output payload)
  putMVar done result

unwrapPublisher :: Either SomeException PublishResult -> IO PublishResult
unwrapPublisher result =
  case result of
    Left exception -> assertFailure ("publisher failed: " <> show exception) >> pure AlreadyExists
    Right publishResult -> pure publishResult

withTemporaryDirectory :: (FilePath -> IO value) -> IO value
withTemporaryDirectory = bracket createTemporaryDirectory removeDirectoryRecursive

createTemporaryDirectory :: IO FilePath
createTemporaryDirectory = do
  root <- getTemporaryDirectory
  (path, handle) <- openBinaryTempFile root "s1-4x-haskell-output-test"
  hClose handle
  removeFile path
  createDirectory path
  pure path
