module S14X.Contract.PinnedFd
  ( parsePinnedFdPath,
    pinnedRegularFileMatchesSha256,
    validPinnedRegularFilePath,
  )
where

import           Control.Exception (IOException, try)
import           Data.ByteString (ByteString)
import           Data.Char (isDigit)
import           Data.List (stripPrefix)
import           System.Directory (doesFileExist)

import qualified Data.ByteString as BS

import           S14X.Contract.Process (sha256Hex)

-- | 현재 process가 상속한 3 이상의 canonical FD 번호만 해석한다.
-- 원래 source pathname이나 다른 process의 FD namespace는 허용하지 않는다.
parsePinnedFdPath :: FilePath -> Maybe Integer
parsePinnedFdPath path =
  case stripPrefix "/proc/self/fd/" path of
    Just digits@(firstDigit : _)
      | firstDigit /= '0',
        all isDigit digits ->
          case reads digits :: [(Integer, String)] of
            [(descriptor, "")] | descriptor >= 3 -> Just descriptor
            _ -> Nothing
    _ -> Nothing

-- | Pinned path가 현재 process에서 열린 regular file descriptor인지 확인한다.
-- `/proc/self/fd/N` magic link 자체는 symlink이므로 generic pathname 정책을 적용하지 않는다.
validPinnedRegularFilePath :: FilePath -> IO Bool
validPinnedRegularFilePath path =
  case parsePinnedFdPath path of
    Nothing -> pure False
    Just _ -> doesFileExist path

-- | 상속 FD에서 실제로 읽은 bytes의 SHA-256을 비교해 closed/reused FD를 fail-closed한다.
-- Source pathname은 이 경계에서 다시 열지 않으며 malformed digest도 즉시 거부한다.
pinnedRegularFileMatchesSha256 :: FilePath -> ByteString -> IO Bool
pinnedRegularFileMatchesSha256 path expectedSha256
  | not (validSha256 expectedSha256) = pure False
  | otherwise = do
      valid <- validPinnedRegularFilePath path
      if not valid
        then pure False
        else do
          result <- try (BS.readFile path) :: IO (Either IOException ByteString)
          pure (either (const False) ((== expectedSha256) . sha256Hex) result)

validSha256 :: ByteString -> Bool
validSha256 value =
  BS.length value == 64
    && BS.all
      (\byte -> (byte >= 48 && byte <= 57) || (byte >= 97 && byte <= 102))
      value
