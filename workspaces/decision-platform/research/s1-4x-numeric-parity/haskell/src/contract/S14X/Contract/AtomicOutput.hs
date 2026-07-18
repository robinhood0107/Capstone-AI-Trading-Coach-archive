module S14X.Contract.AtomicOutput
  ( PublishResult (..),
    exclusiveAtomicWrite,
  )
where

import           Control.Exception (IOException, SomeException, bracket, try, tryJust)
import           Data.ByteString (ByteString)
import           System.Directory (createDirectoryIfMissing, removeFile)
import           System.FilePath (takeDirectory)
import           System.IO (Handle, hClose, hFlush, hSetBinaryMode, openBinaryTempFile)
import           System.IO.Error (isAlreadyExistsError)
import           System.Posix.Files (createLink)

import qualified Data.ByteString as BS

-- | 결과 파일을 새로 공개했는지 기존 winner 때문에 건너뛰었는지를 나타낸다.
-- 기존 결과의 교체나 부분 덮어쓰기는 상태로 표현하지 않고 구현에서 금지한다.
data PublishResult
  = Published
  | AlreadyExists
  deriving stock (Eq, Ord, Show)

-- | 완성된 임시 inode를 hard-link로만 공개해 경합 시 기존 결과를 절대 교체하지 않는다.
-- 임시 파일과 결과 파일은 같은 디렉터리여야 하며 payload와 경로는 신뢰된 shell 경계가 제공한다.
exclusiveAtomicWrite :: FilePath -> ByteString -> IO PublishResult
exclusiveAtomicWrite output payload = do
  let parent = takeDirectory output
  createDirectoryIfMissing True parent
  bracket
    (openBinaryTempFile parent ".s1-4x-haskell-result.tmp")
    cleanupTemporary
    (\(temporary, handle) -> do
        hSetBinaryMode handle True
        BS.hPut handle payload
        hFlush handle
        hClose handle
        linked <- tryJust onlyAlreadyExists (createLink temporary output)
        pure
          ( case linked of
              Left () -> AlreadyExists
              Right () -> Published
          )
    )

onlyAlreadyExists :: IOException -> Maybe ()
onlyAlreadyExists exception
  | isAlreadyExistsError exception = Just ()
  | otherwise = Nothing

cleanupTemporary :: (FilePath, Handle) -> IO ()
cleanupTemporary (temporary, handle) = do
  _ <- try (hClose handle) :: IO (Either SomeException ())
  _ <- try (removeFile temporary) :: IO (Either SomeException ())
  pure ()
