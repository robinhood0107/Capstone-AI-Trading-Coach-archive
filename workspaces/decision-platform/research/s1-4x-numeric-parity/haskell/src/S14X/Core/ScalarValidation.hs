{-# LANGUAGE Safe #-}

module S14X.Core.ScalarValidation
  ( ensureFinite,
    maxFloatInteger,
    validateConfidence,
    validateFiniteScalar,
    validateMomentPair,
    validatePositiveInteger,
    validateSampleSize,
    validateSignificance,
    validateTrialCount,
  )
where

import S14X.Core.Error
  ( StableError
      ( MomentInvalid,
        ResearchInputInvalid,
        ResearchInputTooShort,
        SignificanceInvalid,
        TrialCountInvalid
      ),
  )

ensureFinite :: StableError -> Double -> Either StableError Double
ensureFinite stableError value
  | isNaN value || isInfinite value = Left stableError
  | otherwise = Right value

validateFiniteScalar :: StableError -> Double -> Either StableError Double
validateFiniteScalar = ensureFinite

validateConfidence :: StableError -> Double -> Either StableError Double
validateConfidence stableError value = do
  finite <- validateFiniteScalar stableError value
  if finite > 0.0 && finite < 1.0
    then Right finite
    else Left stableError

validateSignificance :: Double -> Either StableError Double
validateSignificance = validateConfidence SignificanceInvalid

validatePositiveInteger :: StableError -> Integer -> Either StableError Double
validatePositiveInteger stableError value
  | value <= 0 = Left stableError
  -- 양수 arbitrary-size Integer의 float64 overflow는 signature error가 아니라
  -- downstream kernel의 result_non_finite 경계가 소유한다.
  | otherwise = Right (fromInteger value)

validateSampleSize :: Integer -> Either StableError Double
validateSampleSize value
  | value <= 1 = Left ResearchInputTooShort
  | value > maxFloatInteger = Left ResearchInputInvalid
  | otherwise = Right (fromInteger value)

validateTrialCount :: Integer -> Either StableError Double
validateTrialCount value
  | value < 2 || value > maxFloatInteger = Left TrialCountInvalid
  | otherwise = Right (fromInteger value)

validateMomentPair :: Double -> Double -> Either StableError ()
validateMomentPair skewness kurtosis = do
  skew <- validateFiniteScalar MomentInvalid skewness
  pearsonKurtosis <- validateFiniteScalar MomentInvalid kurtosis
  let lowerBound = skew * skew + 1.0
      tolerance =
        64.0
          * encodeFloat 1 (-52)
          * max 1.0 (max (abs pearsonKurtosis) (abs lowerBound))
  if isNaN lowerBound
    || isInfinite lowerBound
    || pearsonKurtosis + tolerance < lowerBound
    then Left MomentInvalid
    else Right ()

-- `floatRadix`/`floatDigits`/`floatRange`로 max finite Double을 구성해 arbitrary Integer
-- lexical 입력을 64-bit로 줄이지 않고 Python float64 상한과 같은 경계를 만든다.
maxFloatInteger :: Integer
maxFloatInteger =
  let digits = floatDigits (0.0 :: Double)
      (_, maximumExponent) = floatRange (0.0 :: Double)
      maximumSignificand = (2 :: Integer) ^ digits - 1
   in maximumSignificand * (2 :: Integer) ^ (maximumExponent - digits)
