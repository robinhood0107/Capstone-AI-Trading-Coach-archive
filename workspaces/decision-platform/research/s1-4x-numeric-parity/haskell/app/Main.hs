module Main (main) where

import           Control.Exception (SomeException, try)
import           Data.Map.Strict (Map)
import           System.Directory (doesDirectoryExist, doesFileExist, doesPathExist)
import           System.Environment (getArgs)
import           System.Exit (ExitCode (ExitFailure, ExitSuccess), exitWith)
import           System.FilePath (isAbsolute)
import           System.IO (stderr)

import qualified Data.ByteString as BS
import qualified Data.Map.Strict as Map

import           S14X.Contract.AtomicOutput (PublishResult (AlreadyExists, Published),
                                             exclusiveAtomicWrite)
import           S14X.Contract.Process (encodeResultBatch, encodeTransportError, parseRequest,
                                        runRequest)
import           S14X.Contract.Types (TransportCode (BinaryInvalid, InternalError, ManifestInvalid, RequestInvalid),
                                      TransportError (TransportError), transportCode)

data Cli = Cli
  { cliRequest :: FilePath,
    cliFixtureRoot :: FilePath,
    cliOutput :: FilePath
  }
  deriving stock (Eq, Show)

-- | absolute request·fixture·새 output path만 받아 process shell을 한 번 실행한다.
-- 모든 예외는 raw detail 없이 frozen transport envelope와 안정된 exit code로 닫는다.
main :: IO ()
main = do
  arguments <- getArgs
  attempted <- try (run arguments) :: IO (Either SomeException ExitCode)
  case attempted of
    Left _ -> do
      emitTransport (TransportError InternalError Nothing Nothing Nothing)
      exitWith (ExitFailure 70)
    Right exitCode -> exitWith exitCode

run :: [String] -> IO ExitCode
run arguments =
  case parseCli arguments of
    Left transport -> emitAndReturn 64 transport
    Right cli -> do
      requestExists <- doesFileExist (cliRequest cli)
      fixtureExists <- doesDirectoryExist (cliFixtureRoot cli)
      outputExists <- doesPathExist (cliOutput cli)
      if not requestExists || not fixtureExists || outputExists
        then
          emitAndReturn
            64
            (TransportError RequestInvalid Nothing Nothing Nothing)
        else do
          payload <- BS.readFile (cliRequest cli)
          case parseRequest payload of
            Left transport -> emitAndReturn 64 transport
            Right request -> do
              result <- runRequest (cliFixtureRoot cli) request
              case result of
                Left transport ->
                  emitAndReturn (transportExitCode transport) transport
                Right batch -> do
                  published <-
                    exclusiveAtomicWrite (cliOutput cli) (encodeResultBatch batch)
                  case published of
                    Published -> pure ExitSuccess
                    AlreadyExists ->
                      emitAndReturn
                        64
                        (TransportError RequestInvalid Nothing Nothing Nothing)

parseCli :: [String] -> Either TransportError Cli
parseCli arguments =
  let pairs = pairArguments arguments
      options = Map.fromList pairs
      expected = Map.fromList [("--request", ()), ("--fixture-root", ()), ("--output", ())]
   in if length arguments /= 6
        || length pairs /= 3
        || Map.size options /= 3
        || Map.keysSet options /= Map.keysSet expected
        then Left (TransportError RequestInvalid Nothing Nothing Nothing)
        else do
          request <- option options "--request"
          fixtureRoot <- option options "--fixture-root"
          output <- option options "--output"
          if all isAbsolute [request, fixtureRoot, output]
            then Right (Cli request fixtureRoot output)
            else Left (TransportError RequestInvalid Nothing Nothing Nothing)

pairArguments :: [String] -> [(String, String)]
pairArguments values =
  case values of
    name : value : remaining -> (name, value) : pairArguments remaining
    _ -> []

option :: Map String String -> String -> Either TransportError String
option options name =
  maybe
    (Left (TransportError RequestInvalid Nothing Nothing Nothing))
    Right
    (Map.lookup name options)

emitAndReturn :: Int -> TransportError -> IO ExitCode
emitAndReturn code transport = do
  emitTransport transport
  pure (ExitFailure code)

emitTransport :: TransportError -> IO ()
emitTransport = BS.hPut stderr . encodeTransportError

transportExitCode :: TransportError -> Int
transportExitCode transport =
  case transportCode transport of
    ManifestInvalid -> 65
    BinaryInvalid -> 65
    _ -> 70
