module S14X.PropertyCases
  ( PropertyCase (..),
    propertyCases,
  )
where

import Data.Text (Text)
import Test.QuickCheck
  ( Gen,
    Property,
    choose,
    counterexample,
    elements,
    forAll,
    vectorOf,
    (===),
  )

import qualified Data.ByteString.Char8 as BS8
import qualified Data.Text as Text
import qualified Data.Vector.Unboxed as U

import S14X.Contract.Process (encodeResultBatch)
import S14X.Contract.Types
  ( CaseResult (CaseSuccess),
    FunctionId
      ( ChristoffersenConditionalCoverageTest,
        ChristoffersenIndependenceTest,
        KupiecUnconditionalCoverageTest,
        LogReturns,
        SimpleReturns
      ),
    ResultBatch (ResultBatch),
  )
import S14X.Core.AdvancedRisk
  ( christoffersenConditionalCoverageTest,
    christoffersenIndependenceTest,
    deflatedSharpeRatio,
    historicalExpectedShortfall,
    kupiecUnconditionalCoverageTest,
    loAdjustedSharpeRatio,
    probabilisticSharpeRatio,
    realizedVariance,
    realizedVolatilityIntraday,
  )
import S14X.Core.Error
  ( StableError
      ( InsufficientSample,
        TrialProvenanceInvalid
      ),
  )
import S14X.Core.Models
  ( ConditionalCoverageResult (ConditionalCoverageResult),
    IndependenceResult (IndependenceResult),
    LikelihoodResult (LikelihoodResult),
    NumericResult
      ( ConditionalCoverageRecord,
        IndependenceRecord,
        LikelihoodRecord,
        ScalarResult,
        VectorResult
      ),
    TransitionCounts (TransitionCounts),
    TrialProvenance (TrialProvenance),
  )
import S14X.Core.NumericPrimitives (normalInverseCdf)
import S14X.Core.ProductionMetrics
  ( annualizedVolatility,
    cagr,
    cumulativeReturn,
    historicalCvar,
    historicalVar,
    logReturns,
    maxDrawdown,
    realizedVolatility,
    sharpeRatio,
    simpleReturns,
    sortinoRatio,
  )

data PropertyCase = PropertyCase
  { propertyIdentifier :: String,
    propertyInvariant :: Property
  }

propertyCases :: [PropertyCase]
propertyCases =
  [ PropertyCase "production.output-finite-or-stable-error" productionOutputFiniteOrStableError,
    PropertyCase "simple-returns.scale-invariant" simpleReturnsScaleInvariant,
    PropertyCase "log-returns.scale-invariant" logReturnsScaleInvariant,
    PropertyCase "cumulative-return.bankruptcy-absorbing" cumulativeReturnBankruptcyAbsorbing,
    PropertyCase "cumulative-return.manual-product-identity" cumulativeReturnManualProductIdentity,
    PropertyCase "volatility.translation-and-scale" volatilityTranslationAndScale,
    PropertyCase "max-drawdown.bounds" maxDrawdownBounds,
    PropertyCase "var-hf7-observation-range" varHf7ObservationRange,
    PropertyCase "var-cvar.shift-and-positive-scale" varCvarShiftAndPositiveScale,
    PropertyCase "cvar-threshold-tail" cvarThresholdTail,
    PropertyCase "expected-shortfall.permutation-invariant" expectedShortfallPermutationInvariant,
    PropertyCase "realized.permutation-invariant" realizedPermutationInvariant,
    PropertyCase "realized.scale-laws" realizedScaleLaws,
    PropertyCase "lo.order-sensitive" loOrderSensitive,
    PropertyCase "psr.benchmark-equality" psrBenchmarkEquality,
    PropertyCase "dsr.benchmark-equality" dsrBenchmarkEquality,
    PropertyCase "dsr.provenance-count-consistency" dsrProvenanceCountConsistency,
    PropertyCase "kupiec.paired-permutation-invariant" kupiecPairedPermutationInvariant,
    PropertyCase "backtest.strict-loss-greater-than-var" backtestStrictLossGreaterThanVar,
    PropertyCase "christoffersen.order-sensitive" christoffersenOrderSensitive,
    PropertyCase "backtest.positive-common-scaling" backtestPositiveCommonScaling,
    PropertyCase "likelihood.record-invariants" likelihoodRecordInvariants,
    PropertyCase "conditional-coverage.component-identity" conditionalCoverageComponentIdentity,
    PropertyCase
      "christoffersen.unidentifiable-transition-rejected"
      christoffersenUnidentifiableTransitionRejected,
    PropertyCase "recursive-negative-zero-normalization" recursiveNegativeZeroNormalization
  ]

productionOutputFiniteOrStableError :: Property
productionOutputFiniteOrStableError =
  forAll positivePrices $ \prices ->
    forAll boundedReturns $ \returns ->
      let priceVector = U.fromList prices
          returnVector = U.fromList returns
          results =
            [ VectorResult <$> simpleReturns priceVector,
              VectorResult <$> logReturns priceVector,
              ScalarResult <$> cumulativeReturn returnVector,
              ScalarResult <$> cagr priceVector 252,
              ScalarResult <$> realizedVolatility returnVector,
              ScalarResult <$> annualizedVolatility returnVector 252,
              ScalarResult <$> maxDrawdown priceVector,
              ScalarResult <$> sharpeRatio returnVector 0.0 252,
              ScalarResult <$> sortinoRatio returnVector 0.0 252,
              ScalarResult <$> historicalVar returnVector 0.95,
              ScalarResult <$> historicalCvar returnVector 0.95
            ]
       in counterexample (show results) (all finiteOrStable results)

simpleReturnsScaleInvariant :: Property
simpleReturnsScaleInvariant =
  forAll positivePrices $ \prices ->
    forAll positivePowerOfTwo $ \scale ->
      let source = U.fromList prices
          scaled = U.map (* scale) source
       in case (simpleReturns source, simpleReturns scaled) of
            (Right left, Right right) ->
              counterexample (show (U.toList left, U.toList right)) (vectorsClose left right)
            result -> counterexample (show result) False

logReturnsScaleInvariant :: Property
logReturnsScaleInvariant =
  forAll positivePrices $ \prices ->
    forAll positivePowerOfTwo $ \scale ->
      let source = U.fromList prices
          scaled = U.map (* scale) source
       in case (logReturns source, logReturns scaled) of
            (Right left, Right right) ->
              counterexample (show (U.toList left, U.toList right)) (vectorsClose left right)
            result -> counterexample (show result) False

cumulativeReturnBankruptcyAbsorbing :: Property
cumulativeReturnBankruptcyAbsorbing =
  forAll boundedReturns $ \suffix ->
    cumulativeReturn (U.fromList (0.1 : -1.0 : suffix)) === Right (-1.0)

cumulativeReturnManualProductIdentity :: Property
cumulativeReturnManualProductIdentity =
  forAll boundedReturns $ \returns ->
    let manual = foldl (\total value -> total * (1.0 + value)) 1.0 returns - 1.0
     in cumulativeReturn (U.fromList returns) === Right manual

volatilityTranslationAndScale :: Property
volatilityTranslationAndScale =
  forAll boundedReturns $ \returns ->
    forAll boundedShift $ \shift ->
      forAll signedPowerOfTwo $ \scale ->
        let values = U.fromList returns
            translated = U.map (+ shift) values
            scaled = U.map (* scale) values
            original = realizedVolatility values
            moved = realizedVolatility translated
            resized = realizedVolatility scaled
            annualized = annualizedVolatility values 4
         in case (original, moved, resized, annualized) of
              (Right base, Right shifted, Right multiplied, Right annual) ->
                counterexample
                  (show (base, shifted, multiplied, annual))
                  ( close base shifted
                      && close multiplied (abs scale * base)
                      && close annual (2.0 * base)
                  )
              result -> counterexample (show result) False

maxDrawdownBounds :: Property
maxDrawdownBounds =
  forAll positivePrices $ \prices ->
    forAll positivePowerOfTwo $ \scale ->
      let values = U.fromList prices
       in case (maxDrawdown values, maxDrawdown (U.map (* scale) values)) of
            (Right value, Right scaled) ->
              counterexample
                (show (value, scaled))
                (value >= -1.0 && value <= 0.0 && close value scaled)
            result -> counterexample (show result) False

varHf7ObservationRange :: Property
varHf7ObservationRange =
  forAll quantileSamples $ \samples ->
    forAll frozenConfidence $ \confidence ->
      let values = U.fromList samples
       in case historicalVar values confidence of
            Left stableError -> counterexample (show stableError) False
            Right value ->
              counterexample
                (show (value, minimum samples, maximum samples))
                (finite value && value >= minimum samples && value <= maximum samples)

varCvarShiftAndPositiveScale :: Property
varCvarShiftAndPositiveScale =
  forAll affineQuantileSamples $ \samples ->
    forAll boundedShift $ \shift ->
      forAll positivePowerOfTwo $ \scale ->
        let confidence = 0.75
            values = U.fromList samples
            shifted = U.map (+ shift) values
            scaled = U.map (* scale) values
         in case
              ( historicalVar values confidence,
                historicalCvar values confidence,
                historicalVar shifted confidence,
                historicalCvar shifted confidence,
                historicalVar scaled confidence,
                historicalCvar scaled confidence
              )
              of
                ( Right valueAtRisk,
                  Right conditional,
                  Right shiftedVar,
                  Right shiftedCvar,
                  Right scaledVar,
                  Right scaledCvar
                  ) ->
                    counterexample
                      (show (valueAtRisk, conditional, shiftedVar, shiftedCvar, scaledVar, scaledCvar))
                      ( close shiftedVar (valueAtRisk + shift)
                          && close shiftedCvar (conditional + shift)
                          && close scaledVar (valueAtRisk * scale)
                          && close scaledCvar (conditional * scale)
                      )
                result -> counterexample (show result) False

cvarThresholdTail :: Property
cvarThresholdTail =
  forAll quantileSamples $ \samples ->
    forAll frozenConfidence $ \confidence ->
      let values = U.fromList samples
       in case (historicalVar values confidence, historicalCvar values confidence) of
            (Right threshold, Right actual) ->
              let tailValues = U.filter (<= threshold) values
                  expected =
                    U.foldl' (+) 0.0 tailValues
                      / fromIntegral (U.length tailValues)
               in counterexample
                    (show (threshold, U.toList tailValues, actual, expected))
                    (not (U.null tailValues) && close actual expected)
            result -> counterexample (show result) False

expectedShortfallPermutationInvariant :: Property
expectedShortfallPermutationInvariant =
  forAll quantileSamples $ \losses ->
    forAll frozenConfidence $ \confidence ->
      forAll positivePowerOfTwo $ \scale ->
        let values = U.fromList losses
            reversed = U.reverse values
            scaled = U.map (* scale) values
         in case
              ( historicalExpectedShortfall values confidence,
                historicalExpectedShortfall reversed confidence,
                historicalExpectedShortfall scaled confidence
              )
              of
                (Right original, Right permuted, Right multiplied) ->
                  counterexample
                    (show (original, permuted, multiplied))
                    (close original permuted && close multiplied (scale * original))
                result -> counterexample (show result) False

realizedPermutationInvariant :: Property
realizedPermutationInvariant =
  forAll boundedResearchReturns $ \returns ->
    let values = U.fromList returns
        reversed = U.reverse values
     in case
          ( realizedVariance values,
            realizedVariance reversed,
            realizedVolatilityIntraday values,
            realizedVolatilityIntraday reversed
          )
          of
            (Right variance, Right permutedVariance, Right volatility, Right permutedVolatility) ->
              counterexample
                (show (variance, permutedVariance, volatility, permutedVolatility))
                ( close variance permutedVariance
                    && close volatility permutedVolatility
                    && close (volatility * volatility) variance
                )
            result -> counterexample (show result) False

realizedScaleLaws :: Property
realizedScaleLaws =
  forAll boundedResearchReturns $ \returns ->
    forAll signedPowerOfTwo $ \scale ->
      let values = U.fromList returns
          scaled = U.map (* scale) values
       in case
            ( realizedVariance values,
              realizedVariance scaled,
              realizedVolatilityIntraday values,
              realizedVolatilityIntraday scaled
            )
            of
              (Right variance, Right scaledVariance, Right volatility, Right scaledVolatility) ->
                counterexample
                  (show (variance, scaledVariance, volatility, scaledVolatility))
                  ( close scaledVariance (scale * scale * variance)
                      && close scaledVolatility (abs scale * volatility)
                  )
              result -> counterexample (show result) False

loOrderSensitive :: Property
loOrderSensitive =
  let ordered = loAdjustedSharpeRatio (U.fromList [-1.0, 0.0, 1.0, 2.0]) 2 0.0
      base = loAdjustedSharpeRatio (U.fromList [-1.0, 0.0, 1.0, 2.0]) 1 0.0
      permuted = loAdjustedSharpeRatio (U.fromList [-1.0, 2.0, 0.0, 1.0]) 2 0.0
   in case (ordered, base, permuted) of
        (Right orderedValue, Right baseValue, Right permutedValue) ->
          counterexample
            (show (orderedValue, baseValue, permutedValue))
            ( close orderedValue 0.565685424949238
                && close baseValue 0.4472135954999579
                && close permutedValue 1.0690449676496974
                && not (close orderedValue permutedValue)
            )
        result -> counterexample (show result) False

psrBenchmarkEquality :: Property
psrBenchmarkEquality =
  forAll boundedSharpe $ \observed ->
    forAll validSampleSize $ \sampleSize ->
      case probabilisticSharpeRatio observed observed sampleSize 0.0 3.0 of
        Right probability ->
          counterexample (show probability) (close probability 0.5)
        Left stableError -> counterexample (show stableError) False

dsrBenchmarkEquality :: Property
dsrBenchmarkEquality =
  forAll frozenTrialCount $ \trialCount ->
    forAll positivePowerOfTwo $ \variance ->
      let benchmark = expectedMaximumSharpe trialCount variance
          provenance = validProvenance trialCount
       in case
            deflatedSharpeRatio
              benchmark
              252
              0.0
              3.0
              trialCount
              variance
              provenance
            of
              Right probability ->
                counterexample (show (trialCount, variance, benchmark, probability)) (close probability 0.5)
              Left stableError -> counterexample (show stableError) False

dsrProvenanceCountConsistency :: Property
dsrProvenanceCountConsistency =
  forAll (choose (0, 8 :: Int)) $ \variant ->
    let trialCount = 2
        provenance = invalidProvenance variant
     in deflatedSharpeRatio 0.5 252 0.0 3.0 trialCount 1.0 provenance
          === Left TrialProvenanceInvalid

kupiecPairedPermutationInvariant :: Property
kupiecPairedPermutationInvariant =
  forAll identifiableExceptionPattern $ \exceptions ->
    forAll frozenConfidence $ \confidence ->
      let (losses, forecasts) = backtestVectors exceptions
          reversedLosses = U.reverse losses
          reversedForecasts = U.reverse forecasts
       in case
            ( kupiecUnconditionalCoverageTest losses forecasts confidence 0.05,
              kupiecUnconditionalCoverageTest reversedLosses reversedForecasts confidence 0.05
            )
            of
              (Right original, Right permuted) ->
                counterexample (show (original, permuted)) (likelihoodClose original permuted)
              result -> counterexample (show result) False

backtestStrictLossGreaterThanVar :: Property
backtestStrictLossGreaterThanVar =
  let losses = U.fromList [0.0, 2.0, 0.0, 1.0, -1.0]
      forecasts = U.fromList [0.0, 1.0, 1.0, 0.0, 0.0]
      kupiec = kupiecUnconditionalCoverageTest losses forecasts 0.6 0.05
      independence = christoffersenIndependenceTest losses forecasts 0.05
      conditional = christoffersenConditionalCoverageTest losses forecasts 0.6 0.05
   in case (kupiec, independence, conditional) of
        ( Right (LikelihoodResult _ _ _ observations exceptions _ _),
          Right (IndependenceResult _ _ _ observationsI exceptionsI _ _ counts),
          Right (ConditionalCoverageResult _ _ _ observationsC exceptionsC _ _ countsC _ _ _ _)
          ) ->
            counterexample
              (show (kupiec, independence, conditional))
              ( observations == 5
                  && observationsI == 5
                  && observationsC == 5
                  && exceptions == 2
                  && exceptionsI == 2
                  && exceptionsC == 2
                  && counts == TransitionCounts 0 2 2 0
                  && countsC == TransitionCounts 0 2 2 0
              )
        result -> counterexample (show result) False

christoffersenOrderSensitive :: Property
christoffersenOrderSensitive =
  let forecasts = U.replicate 5 1.0
      ordered = U.fromList [0.0, 2.0, 0.0, 2.0, 0.0]
      permuted = U.fromList [0.0, 0.0, 2.0, 2.0, 0.0]
      independenceOrdered = christoffersenIndependenceTest ordered forecasts 0.05
      independencePermuted = christoffersenIndependenceTest permuted forecasts 0.05
      conditionalOrdered = christoffersenConditionalCoverageTest ordered forecasts 0.6 0.05
      conditionalPermuted = christoffersenConditionalCoverageTest permuted forecasts 0.6 0.05
   in case
        (independenceOrdered, independencePermuted, conditionalOrdered, conditionalPermuted)
        of
          ( Right (IndependenceResult orderedStatistic _ _ _ orderedExceptions _ _ _),
            Right (IndependenceResult permutedStatistic _ _ _ permutedExceptions _ _ _),
            Right (ConditionalCoverageResult orderedConditional _ _ _ _ _ _ _ _ _ _ _),
            Right (ConditionalCoverageResult permutedConditional _ _ _ _ _ _ _ _ _ _ _)
            ) ->
              counterexample
                (show (orderedStatistic, permutedStatistic, orderedConditional, permutedConditional))
                ( orderedExceptions == permutedExceptions
                    && close orderedStatistic 5.545177444479562
                    && close permutedStatistic 0.0
                    && close orderedConditional 5.708465422560582
                    && close permutedConditional 0.16328797808102014
                    && not (close orderedStatistic permutedStatistic)
                )
          result -> counterexample (show result) False

backtestPositiveCommonScaling :: Property
backtestPositiveCommonScaling =
  forAll identifiableExceptionPattern $ \exceptions ->
    forAll positivePowerOfTwo $ \scale ->
      let (losses, forecasts) = backtestVectors exceptions
          scaledLosses = U.map (* scale) losses
          scaledForecasts = U.map (* scale) forecasts
          original =
            ( kupiecUnconditionalCoverageTest losses forecasts 0.75 0.05,
              christoffersenIndependenceTest losses forecasts 0.05,
              christoffersenConditionalCoverageTest losses forecasts 0.75 0.05
            )
          scaled =
            ( kupiecUnconditionalCoverageTest scaledLosses scaledForecasts 0.75 0.05,
              christoffersenIndependenceTest scaledLosses scaledForecasts 0.05,
              christoffersenConditionalCoverageTest scaledLosses scaledForecasts 0.75 0.05
            )
       in counterexample (show (original, scaled)) (backtestTupleClose original scaled)

likelihoodRecordInvariants :: Property
likelihoodRecordInvariants =
  forAll identifiableExceptionPattern $ \exceptions ->
    forAll frozenConfidence $ \confidence ->
      forAll frozenSignificance $ \significance ->
        let (losses, forecasts) = backtestVectors exceptions
         in case
              ( kupiecUnconditionalCoverageTest losses forecasts confidence significance,
                christoffersenIndependenceTest losses forecasts significance,
                christoffersenConditionalCoverageTest losses forecasts confidence significance
              )
              of
                (Right kupiec, Right independence, Right conditional) ->
                  counterexample
                    (show (kupiec, independence, conditional))
                    ( likelihoodInvariant kupiec
                        && independenceInvariant independence
                        && conditionalInvariant conditional
                    )
                result -> counterexample (show result) False

conditionalCoverageComponentIdentity :: Property
conditionalCoverageComponentIdentity =
  forAll identifiableExceptionPattern $ \exceptions ->
    forAll frozenConfidence $ \confidence ->
      let (losses, forecasts) = backtestVectors exceptions
       in case christoffersenConditionalCoverageTest losses forecasts confidence 0.05 of
            Right
              (ConditionalCoverageResult statistic _ _ _ _ _ _ _ _ _ unconditional independence) ->
                counterexample
                  (show (statistic, unconditional, independence))
                  (likelihoodCloseDouble statistic (unconditional + independence))
            Left stableError -> counterexample (show stableError) False

christoffersenUnidentifiableTransitionRejected :: Property
christoffersenUnidentifiableTransitionRejected =
  forAll (elements [replicate 5 False, replicate 5 True]) $ \exceptions ->
    let (losses, forecasts) = backtestVectors exceptions
     in counterexample
          (show exceptions)
          ( christoffersenIndependenceTest losses forecasts 0.05 == Left InsufficientSample
              && christoffersenConditionalCoverageTest losses forecasts 0.75 0.05
                == Left InsufficientSample
          )

recursiveNegativeZeroNormalization :: Property
recursiveNegativeZeroNormalization =
  let encoded =
        encodeResultBatch
          (ResultBatch "negative-zero-property" "haskell" negativeZeroResults)
      forbiddenNumericTokens = [":-0", "[-0", ",-0"]
   in counterexample
        (BS8.unpack encoded)
        (all (\token -> not (token `BS8.isInfixOf` encoded)) forbiddenNumericTokens)

positivePrices :: Gen [Double]
positivePrices = do
  count <- choose (2, 64)
  integers <- vectorOf count (choose (1, 100000 :: Int))
  pure (fmap (\value -> fromIntegral value / 16.0) integers)

boundedReturns :: Gen [Double]
boundedReturns = do
  count <- choose (2, 64)
  integers <- vectorOf count (choose (-250, 250 :: Int))
  pure (fmap (\value -> fromIntegral value / 1000.0) integers)

boundedResearchReturns :: Gen [Double]
boundedResearchReturns = do
  count <- choose (1, 64)
  integers <- vectorOf count (choose (-100, 100 :: Int))
  pure (fmap (\value -> fromIntegral value / 16.0) integers)

quantileSamples :: Gen [Double]
quantileSamples = do
  count <- choose (2, 64)
  integers <- vectorOf count (choose (-1000, 1000 :: Int))
  pure (fmap (\value -> fromIntegral value / 16.0) integers)

affineQuantileSamples :: Gen [Double]
affineQuantileSamples = do
  count <- elements [value | value <- [2 .. 64], (value - 1) `mod` 4 /= 0]
  start <- choose (-1000, 1000 :: Int)
  step <- choose (1, 16 :: Int)
  reversed <- elements [False, True]
  let values =
        [fromIntegral (start + index * step) / 16.0 | index <- [0 .. count - 1]]
  pure (if reversed then reverse values else values)

positivePowerOfTwo :: Gen Double
positivePowerOfTwo = elements [0.25, 0.5, 1.0, 2.0, 4.0]

signedPowerOfTwo :: Gen Double
signedPowerOfTwo = elements [-4.0, -2.0, -0.5, 0.5, 2.0, 4.0]

boundedShift :: Gen Double
boundedShift = elements [-8.0, -2.0, -0.5, 0.0, 0.5, 2.0, 8.0]

frozenConfidence :: Gen Double
frozenConfidence = elements [0.5, 0.75, 0.8, 0.95]

frozenSignificance :: Gen Double
frozenSignificance = elements [0.01, 0.05, 0.2]

boundedSharpe :: Gen Double
boundedSharpe = do
  value <- choose (-32, 32 :: Int)
  pure (fromIntegral value / 16.0)

validSampleSize :: Gen Integer
validSampleSize = toInteger <$> choose (2, 4096 :: Int)

frozenTrialCount :: Gen Integer
frozenTrialCount = elements [2, 100000000000000000000, 10 ^ (308 :: Int)]

identifiableExceptionPattern :: Gen [Bool]
identifiableExceptionPattern =
  elements
    [ [False, True, False, True, False],
      [False, False, True, True, False],
      [True, False, True, True, False],
      [True, True, False, False, True]
    ]

finiteOrStable :: Either StableError NumericResult -> Bool
finiteOrStable result =
  case result of
    Left _ -> True
    Right numericResult -> numericResultFinite numericResult

numericResultFinite :: NumericResult -> Bool
numericResultFinite numericResult =
  case numericResult of
    ScalarResult value -> finite value
    VectorResult values -> U.all finite values
    LikelihoodRecord (LikelihoodResult statistic pValue _ _ _ _ significance) ->
      all finite [statistic, pValue, significance]
    IndependenceRecord
      (IndependenceResult statistic pValue _ _ _ _ significance _) ->
        all finite [statistic, pValue, significance]
    ConditionalCoverageRecord
      ( ConditionalCoverageResult
          statistic
          pValue
          _
          _
          _
          _
          significance
          _
          _
          _
          unconditional
          independence
        ) ->
        all finite [statistic, pValue, significance, unconditional, independence]

finite :: Double -> Bool
finite value = not (isNaN value || isInfinite value)

vectorsClose :: U.Vector Double -> U.Vector Double -> Bool
vectorsClose left right =
  U.length left == U.length right
    && U.and (U.zipWith close left right)

close :: Double -> Double -> Bool
close left right =
  abs (left - right) <= 1.0e-9 * max 1.0 (max (abs left) (abs right))

likelihoodCloseDouble :: Double -> Double -> Bool
likelihoodCloseDouble left right =
  abs (left - right)
    <= 128.0 * encodeFloat 1 (-52) * max 1.0 (max (abs left) (abs right))

expectedMaximumSharpe :: Integer -> Double -> Double
expectedMaximumSharpe trialCount variance =
  let trials = fromInteger trialCount
      reciprocal = 1.0 / trials
      firstQuantile = -normalInverseCdf reciprocal
      secondQuantile = -normalInverseCdf (reciprocal / exp 1.0)
      eulerMascheroni = 0.5772156649015329
   in sqrt variance
        * ( (1.0 - eulerMascheroni) * firstQuantile
              + eulerMascheroni * secondQuantile
          )

validProvenance :: Integer -> TrialProvenance
validProvenance trialCount =
  TrialProvenance
    "s1.4r-effective-trials-v1"
    "externally_estimated_effective_count"
    trialCount
    trialCount
    "daily"
    (replicate 64 'd')
    1

invalidProvenance :: Int -> TrialProvenance
invalidProvenance variant =
  case variant of
    0 ->
      TrialProvenance
        "wrong"
        "externally_estimated_effective_count"
        2
        2
        "daily"
        digest
        1
    1 -> TrialProvenance schema "wrong" 2 2 "daily" digest 1
    2 -> TrialProvenance schema method 1 2 "daily" digest 1
    3 -> TrialProvenance schema method 2 1 "daily" digest 1
    4 -> TrialProvenance schema method 3 3 "daily" digest 1
    5 -> TrialProvenance schema method 2 2 " \t\n" digest 1
    6 -> TrialProvenance schema method 2 2 "daily" (replicate 64 'D') 1
    7 -> TrialProvenance schema method 2 2 "daily" (replicate 63 'd') 1
    _ -> TrialProvenance schema method 2 2 "daily" digest 0
  where
    schema = "s1.4r-effective-trials-v1"
    method = "externally_estimated_effective_count"
    digest = replicate 64 'd'

backtestVectors :: [Bool] -> (U.Vector Double, U.Vector Double)
backtestVectors exceptions =
  ( U.fromList (fmap (\exception -> if exception then 2.0 else 0.0) exceptions),
    U.replicate (length exceptions) 1.0
  )

likelihoodClose :: LikelihoodResult -> LikelihoodResult -> Bool
likelihoodClose
  (LikelihoodResult leftStatistic leftPValue leftReject leftN leftX leftDof leftAlpha)
  (LikelihoodResult rightStatistic rightPValue rightReject rightN rightX rightDof rightAlpha) =
    close leftStatistic rightStatistic
      && close leftPValue rightPValue
      && leftReject == rightReject
      && leftN == rightN
      && leftX == rightX
      && leftDof == rightDof
      && leftAlpha == rightAlpha

independenceClose :: IndependenceResult -> IndependenceResult -> Bool
independenceClose
  (IndependenceResult leftStatistic leftPValue leftReject leftN leftX leftDof leftAlpha leftCounts)
  (IndependenceResult rightStatistic rightPValue rightReject rightN rightX rightDof rightAlpha rightCounts) =
    close leftStatistic rightStatistic
      && close leftPValue rightPValue
      && leftReject == rightReject
      && leftN == rightN
      && leftX == rightX
      && leftDof == rightDof
      && leftAlpha == rightAlpha
      && leftCounts == rightCounts

conditionalClose :: ConditionalCoverageResult -> ConditionalCoverageResult -> Bool
conditionalClose
  ( ConditionalCoverageResult
      leftStatistic
      leftPValue
      leftReject
      leftN
      leftX
      leftDof
      leftAlpha
      leftCounts
      leftConditionedN
      leftConditionedX
      leftUnconditional
      leftIndependence
    )
  ( ConditionalCoverageResult
      rightStatistic
      rightPValue
      rightReject
      rightN
      rightX
      rightDof
      rightAlpha
      rightCounts
      rightConditionedN
      rightConditionedX
      rightUnconditional
      rightIndependence
    ) =
    close leftStatistic rightStatistic
      && close leftPValue rightPValue
      && leftReject == rightReject
      && leftN == rightN
      && leftX == rightX
      && leftDof == rightDof
      && leftAlpha == rightAlpha
      && leftCounts == rightCounts
      && leftConditionedN == rightConditionedN
      && leftConditionedX == rightConditionedX
      && close leftUnconditional rightUnconditional
      && close leftIndependence rightIndependence

backtestTupleClose ::
  ( Either StableError LikelihoodResult,
    Either StableError IndependenceResult,
    Either StableError ConditionalCoverageResult
  ) ->
  ( Either StableError LikelihoodResult,
    Either StableError IndependenceResult,
    Either StableError ConditionalCoverageResult
  ) ->
  Bool
backtestTupleClose original scaled =
  case (original, scaled) of
    ( (Right leftKupiec, Right leftIndependence, Right leftConditional),
      (Right rightKupiec, Right rightIndependence, Right rightConditional)
      ) ->
        likelihoodClose leftKupiec rightKupiec
          && independenceClose leftIndependence rightIndependence
          && conditionalClose leftConditional rightConditional
    _ -> False

likelihoodInvariant :: LikelihoodResult -> Bool
likelihoodInvariant
  (LikelihoodResult statistic pValue reject observations exceptions dof significance) =
    statistic >= 0.0
      && pValue >= 0.0
      && pValue <= 1.0
      && reject == (pValue < significance)
      && observations >= exceptions
      && exceptions >= 0
      && dof == 1

independenceInvariant :: IndependenceResult -> Bool
independenceInvariant
  (IndependenceResult statistic pValue reject observations exceptions dof significance counts) =
    statistic >= 0.0
      && pValue >= 0.0
      && pValue <= 1.0
      && reject == (pValue < significance)
      && observations >= exceptions
      && exceptions >= 0
      && dof == 1
      && transitionTotal counts == observations - 1

conditionalInvariant :: ConditionalCoverageResult -> Bool
conditionalInvariant
  ( ConditionalCoverageResult
      statistic
      pValue
      reject
      observations
      exceptions
      dof
      significance
      counts
      conditionedObservations
      conditionedExceptions
      unconditional
      independence
    ) =
    statistic >= 0.0
      && pValue >= 0.0
      && pValue <= 1.0
      && reject == (pValue < significance)
      && observations >= exceptions
      && exceptions >= 0
      && dof == 2
      && transitionTotal counts == conditionedObservations
      && conditionedObservations == observations - 1
      && conditionedObservations >= conditionedExceptions
      && conditionedExceptions >= 0
      && likelihoodCloseDouble statistic (unconditional + independence)

transitionTotal :: TransitionCounts -> Int
transitionTotal (TransitionCounts n00 n01 n10 n11) = n00 + n01 + n10 + n11

negativeZeroResults :: [CaseResult]
negativeZeroResults =
  fmap negativeZeroResult [minBound .. maxBound]

negativeZeroResult :: FunctionId -> CaseResult
negativeZeroResult functionId =
  CaseSuccess (functionFixtureId functionId) functionId (resultFor functionId)
  where
    resultFor selected
      | selected `elem` [SimpleReturns, LogReturns] =
          VectorResult (U.fromList [negativeZero, 0.0])
      | selected == KupiecUnconditionalCoverageTest =
          LikelihoodRecord
            (LikelihoodResult negativeZero negativeZero False 1 0 1 negativeZero)
      | selected == ChristoffersenIndependenceTest =
          IndependenceRecord
            ( IndependenceResult
                negativeZero
                negativeZero
                False
                5
                2
                1
                negativeZero
                (TransitionCounts 0 2 2 0)
            )
      | selected == ChristoffersenConditionalCoverageTest =
          ConditionalCoverageRecord
            ( ConditionalCoverageResult
                negativeZero
                negativeZero
                False
                5
                2
                2
                negativeZero
                (TransitionCounts 0 2 2 0)
                4
                2
                negativeZero
                negativeZero
            )
      | otherwise = ScalarResult negativeZero

functionFixtureId :: FunctionId -> Text
functionFixtureId functionId = Text.pack ("negative-zero-" <> show (fromEnum functionId))

negativeZero :: Double
negativeZero = -0.0
