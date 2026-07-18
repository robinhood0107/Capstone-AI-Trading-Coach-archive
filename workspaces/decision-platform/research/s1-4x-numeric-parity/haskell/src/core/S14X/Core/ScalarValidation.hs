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

import           S14X.Core.Error (StableError (MomentInvalid, ResearchInputInvalid, ResearchInputTooShort, SignificanceInvalid, TrialCountInvalid))

-- | NaN/infinity를 caller가 지정한 stable 오류로 바꾸고 유한 값은 보존한다.
-- 모든 scalar public boundary가 같은 결과 유한성 규칙을 재사용한다.
ensureFinite :: StableError -> Double -> Either StableError Double
ensureFinite stableError value
  | isNaN value || isInfinite value = Left stableError
  | otherwise = Right value

-- | public scalar 입력의 유한성을 caller 지정 오류로 검증한다.
-- 현재 'ensureFinite'와 같지만 입력 경계 의미를 signature에 드러낸다.
validateFiniteScalar :: StableError -> Double -> Either StableError Double
validateFiniteScalar = ensureFinite

-- | 유한한 열린 구간 @(0,1)@ 확률인지 검증한다.
-- confidence와 significance가 각자 정해진 stable 오류를 유지하도록 오류를 인자로 받는다.
validateConfidence :: StableError -> Double -> Either StableError Double
validateConfidence stableError value = do
  finite <- validateFiniteScalar stableError value
  if finite > 0.0 && finite < 1.0
    then Right finite
    else Left stableError

-- | significance를 유한한 열린 구간 @(0,1)@로 검증한다.
-- 실패는 항상 'SignificanceInvalid'로 매핑한다.
validateSignificance :: Double -> Either StableError Double
validateSignificance = validateConfidence SignificanceInvalid

-- | 양의 arbitrary-size 'Integer' 주기를 Float64로 변환한다.
-- 양수 overflow는 lexical 오류가 아니라 downstream 결과 유한성 경계가 판정한다.
validatePositiveInteger :: StableError -> Integer -> Either StableError Double
validatePositiveInteger stableError value
  | value <= 0 = Left stableError
  -- 양수 arbitrary-size Integer의 float64 overflow는 signature error가 아니라
  -- downstream kernel의 result_non_finite 경계가 소유한다.
  | otherwise = Right (fromInteger value)

-- | PSR/DSR 표본크기를 @1 < n <= maxFiniteDoubleInteger@ 범위로 검증한다.
-- 작은 표본과 Float64 표현 불가 입력을 서로 다른 research 오류로 구분한다.
validateSampleSize :: Integer -> Either StableError Double
validateSampleSize value
  | value <= 1 = Left ResearchInputTooShort
  | value > maxFloatInteger = Left ResearchInputInvalid
  | otherwise = Right (fromInteger value)

-- | effective trial count를 @2@ 이상이며 Float64로 정확히 범위화 가능한 값으로 검증한다.
-- 범위를 벗어나면 'TrialCountInvalid'를 반환한다.
validateTrialCount :: Integer -> Either StableError Double
validateTrialCount value
  | value < 2 || value > maxFloatInteger = Left TrialCountInvalid
  | otherwise = Right (fromInteger value)

-- | 왜도와 Pearson kurtosis의 유한성 및 Pearson 하한을 roundoff 허용치와 함께 검증한다.
-- moment 제약 위반은 확률 kernel 실행 전에 'MomentInvalid'로 닫는다.
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

-- | 'floatRadix'/'floatDigits'/'floatRange'로 max finite Double의 정수 상한을 구성한다.
-- arbitrary 'Integer'를 64-bit로 줄이지 않고 Python Float64 경계와 맞춘다.
maxFloatInteger :: Integer
maxFloatInteger =
  let digits = floatDigits (0.0 :: Double)
      (_, maximumExponent) = floatRange (0.0 :: Double)
      maximumSignificand = (2 :: Integer) ^ digits - 1
   in maximumSignificand * (2 :: Integer) ^ (maximumExponent - digits)
