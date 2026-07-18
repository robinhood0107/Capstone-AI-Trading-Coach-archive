module S14X.Contract.StrictJson
  ( objectMap,
    parseStrictJson,
    rawDouble,
    rawInteger,
  )
where

import Control.Applicative ((<|>))
import Data.Aeson (eitherDecodeStrict')
import Data.Attoparsec.ByteString.Char8
  ( Parser,
    char,
    endOfInput,
    many',
    match,
    option,
    parseOnly,
    peekChar,
    satisfy,
    sepBy,
    skipWhile,
    string,
  )
import Data.ByteString (ByteString)
import Data.Foldable (traverse_)
import Data.Map.Strict (Map)
import Data.Scientific (Scientific, toRealFloat)
import Data.Set (Set)
import Data.Text (Text)
import Data.Text.Encoding (decodeUtf8')

import qualified Data.ByteString.Char8 as BS8
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import qualified Data.Text as Text
import qualified Data.Text.Read as TextRead

import S14X.Contract.Types
  ( RawJson
      ( RawArray,
        RawBool,
        RawNull,
        RawNumber,
        RawObject,
        RawString
      ),
  )

-- Aeson object materialization 전에 모든 object level의 decoded duplicate key를 검사한다.
-- 동시에 raw number token을 보존해 decimal/exponent를 Integer로 오인하지 않는다.
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

objectMap :: RawJson -> Either Text (Map Text RawJson)
objectMap rawJson =
  case rawJson of
    RawObject pairs -> Right (Map.fromList pairs)
    _ -> Left "object"

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
    <|> (string "true" *> pure (RawBool True))
    <|> (string "false" *> pure (RawBool False))
    <|> (string "null" *> pure RawNull)

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
        <|> (satisfy isNonZeroDigit *> many' (satisfy isDigit) *> pure ())
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
  (satisfy (\character -> character >= ' ' && character /= '"' && character /= '\\') *> pure ())
    <|> escapedCharacter

escapedCharacter :: Parser ()
escapedCharacter = do
  _ <- char '\\'
  simpleEscape <|> unicodeEscape
  where
    simpleEscape =
      satisfy (`elem` ['"', '\\', '/', 'b', 'f', 'n', 'r', 't']) *> pure ()
    unicodeEscape = do
      _ <- char 'u'
      _ <- sequenceA (replicate 4 (satisfy isHex))
      pure ()
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
