module S14X.Contract.StrictJson
  ( objectMap,
    parseStrictJson,
    rawDouble,
    rawInteger,
  )
where

import           Control.Applicative ((<|>))
import           Control.Monad (replicateM_)
import           Data.Aeson (eitherDecodeStrict')
import           Data.Attoparsec.ByteString.Char8 (Parser, char, endOfInput, many', match, option,
                                                   parseOnly, peekChar, satisfy, sepBy, skipWhile,
                                                   string)
import           Data.ByteString (ByteString)
import           Data.Foldable (traverse_)
import           Data.Functor (($>))
import           Data.Map.Strict (Map)
import           Data.Scientific (Scientific, toRealFloat)
import           Data.Set (Set)
import           Data.Text (Text)
import           Data.Text.Encoding (decodeUtf8')

import qualified Data.ByteString.Char8 as BS8
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import qualified Data.Text as Text
import qualified Data.Text.Read as TextRead

import           S14X.Contract.Types (RawJson (RawArray, RawBool, RawNull, RawNumber, RawObject, RawString))

-- | Aeson object materialization 전에 모든 object level의 decoded duplicate key를 검사한다.
-- UTF-8·RFC 8259·유한 number만 허용하고 raw number token을 보존한다.
parseStrictJson :: ByteString -> Either Text RawJson
parseStrictJson bytes = do
  case decodeUtf8' bytes of
    Left _ -> Left "utf8"
    Right _ -> Right ()
  value <-
    case parseOnly (jsonSpace *> rawValue <* jsonSpace <* endOfInput) bytes of
      Left _ -> Left "syntax"
      Right parsed -> Right parsed
  rejectDuplicateKeys value
  rejectForbiddenNumbers value
  Right value

-- | strict JSON object의 pair를 key map으로 바꾸고 object가 아니면 @object@ 오류를 반환한다.
-- duplicate key는 'parseStrictJson'에서 이미 거부된 값에만 적용해야 한다.
objectMap :: RawJson -> Either Text (Map Text RawJson)
objectMap rawJson =
  case rawJson of
    RawObject pairs -> Right (Map.fromList pairs)
    _ -> Left "object"

-- | exponent나 decimal point가 없는 JSON number token만 arbitrary 'Integer'로 읽는다.
-- Bool/string과 변환 잔여 문자가 있는 token은 'Nothing'으로 거부한다.
rawInteger :: RawJson -> Maybe Integer
rawInteger rawJson =
  case rawJson of
    RawNumber token
      | isBareIntegerToken token ->
          case TextRead.signed TextRead.decimal token of
            Right (value, remainder)
              | Text.null remainder -> Just value
            _ -> Nothing
    _ -> Nothing

-- | JSON number를 finite Float64로 변환하고 overflow·NaN·infinity는 거부한다.
-- 입력 token의 shape와 duplicate 검증은 앞선 strict parser가 소유한다.
rawDouble :: RawJson -> Maybe Double
rawDouble rawJson =
  case rawJson of
    RawNumber token ->
      case eitherDecodeStrict' (BS8.pack (Text.unpack token)) :: Either String Scientific of
        Left _ -> Nothing
        Right value ->
          let converted = toRealFloat value
           in if isNaN converted || isInfinite converted
                then Nothing
                else Just converted
    _ -> Nothing

rawValue :: Parser RawJson
rawValue =
  rawObject
    <|> rawArray
    <|> (RawString <$> jsonString)
    <|> rawNumber
    <|> (string "true" $> RawBool True)
    <|> (string "false" $> RawBool False)
    <|> (string "null" $> RawNull)

rawObject :: Parser RawJson
rawObject =
  RawObject
    <$> betweenSpaces
      '{'
      '}'
      (pair `sepBy` comma)
  where
    pair = do
      key <- jsonString
      jsonSpace
      _ <- char ':'
      jsonSpace
      value <- rawValue
      pure (key, value)

rawArray :: Parser RawJson
rawArray =
  RawArray
    <$> betweenSpaces
      '['
      ']'
      (rawValue `sepBy` comma)

rawNumber :: Parser RawJson
rawNumber = do
  (token, _) <- match jsonNumber
  pure (RawNumber (Text.pack (BS8.unpack token)))

-- RFC 8259 number grammar를 직접 보존한다. 특히 leading zero, trailing decimal point,
-- exponent digit 누락을 parser 단계에서 거부한다.
jsonNumber :: Parser ()
jsonNumber = do
  _ <- option Nothing (Just <$> char '-')
  integerPart
  _ <- option Nothing (Just <$> fractionalPart)
  _ <- option Nothing (Just <$> exponentPart)
  pure ()
  where
    integerPart =
      (char '0' *> rejectFollowingDigit)
        <|> ((satisfy isNonZeroDigit *> many' (satisfy isDigit)) $> ())
    fractionalPart = do
      _ <- char '.'
      _ <- satisfy isDigit
      _ <- many' (satisfy isDigit)
      pure ()
    exponentPart = do
      _ <- satisfy (\character -> character == 'e' || character == 'E')
      _ <- option Nothing (Just <$> satisfy (\character -> character == '+' || character == '-'))
      _ <- satisfy isDigit
      _ <- many' (satisfy isDigit)
      pure ()
    rejectFollowingDigit = do
      next <- peekChar
      case next of
        Just character
          | isDigit character -> fail "leading zero"
        _ -> pure ()

jsonString :: Parser Text
jsonString = do
  (token, _) <-
    match $ do
      _ <- char '"'
      _ <- many' stringCharacter
      _ <- char '"'
      pure ()
  case eitherDecodeStrict' token of
    Left _ -> fail "invalid JSON string"
    Right value -> pure value

stringCharacter :: Parser ()
stringCharacter =
  (satisfy (\character -> character >= ' ' && character /= '"' && character /= '\\') $> ())
    <|> escapedCharacter

escapedCharacter :: Parser ()
escapedCharacter = do
  _ <- char '\\'
  simpleEscape <|> unicodeEscape
  where
    simpleEscape =
      satisfy (`elem` ['"', '\\', '/', 'b', 'f', 'n', 'r', 't']) $> ()
    unicodeEscape = do
      _ <- char 'u'
      replicateM_ 4 (satisfy isHex)
    isHex character =
      isDigit character
        || (character >= 'a' && character <= 'f')
        || (character >= 'A' && character <= 'F')

betweenSpaces :: Char -> Char -> Parser value -> Parser value
betweenSpaces opening closing parser = do
  _ <- char opening
  jsonSpace
  value <- parser
  jsonSpace
  _ <- char closing
  pure value

comma :: Parser Char
comma = jsonSpace *> char ',' <* jsonSpace

jsonSpace :: Parser ()
jsonSpace = skipWhile (`elem` [' ', '\t', '\n', '\r'])

rejectDuplicateKeys :: RawJson -> Either Text ()
rejectDuplicateKeys rawJson =
  case rawJson of
    RawObject pairs -> do
      rejectPairDuplicates Set.empty pairs
      traverse_ (rejectDuplicateKeys . snd) pairs
    RawArray values -> traverse_ rejectDuplicateKeys values
    _ -> Right ()

rejectPairDuplicates :: Set Text -> [(Text, RawJson)] -> Either Text ()
rejectPairDuplicates _ [] = Right ()
rejectPairDuplicates seen ((key, _) : remaining)
  | Set.member key seen = Left "duplicate"
  | otherwise = rejectPairDuplicates (Set.insert key seen) remaining

rejectForbiddenNumbers :: RawJson -> Either Text ()
rejectForbiddenNumbers rawJson =
  case rawJson of
    RawNumber "-0" -> Left "negative-zero-integer"
    RawNumber token
      | not (isBareIntegerToken token) ->
          case rawDouble rawJson of
            Nothing -> Left "non-finite-number"
            Just _ -> Right ()
    RawObject pairs -> traverse_ (rejectForbiddenNumbers . snd) pairs
    RawArray values -> traverse_ rejectForbiddenNumbers values
    _ -> Right ()

isBareIntegerToken :: Text -> Bool
isBareIntegerToken token =
  case Text.uncons token of
    Just ('0', rest) -> Text.null rest
    Just ('-', rest) -> nonZeroDigits rest
    _ -> nonZeroDigits token
  where
    nonZeroDigits value =
      case Text.uncons value of
        Just (first, rest) ->
          isNonZeroDigit first && Text.all isDigit rest
        Nothing -> False

isDigit :: Char -> Bool
isDigit character = character >= '0' && character <= '9'

isNonZeroDigit :: Char -> Bool
isNonZeroDigit character = character >= '1' && character <= '9'
