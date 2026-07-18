module S14X.PropertySpec (tests) where

import Test.Tasty (TestTree, testGroup)
import Test.Tasty.QuickCheck
  ( Property,
    Gen,
    choose,
    counterexample,
    forAll,
    property,
    testProperty,
    vectorOf,
    (===),
  )

import qualified Data.Vector.Unboxed as U

import S14X.Core.AdvancedRisk
  ( historicalExpectedShortfall,
    realizedVariance,
    realizedVolatilityIntraday,
  )
import S14X.Core.ProductionMetrics
  ( cumulativeReturn,
    logReturns,
    maxDrawdown,
    simpleReturns,
  )

tests :: TestTree
tests =
  testGroup
    "properties"
    [ testProperty "simple returns are positive-scale invariant" scaleInvariantSimple,
      testProperty "log returns are positive-scale invariant" scaleInvariantLog,
      testProperty "bankruptcy is absorbing" bankruptcyAbsorbing,
      testProperty "drawdown is bounded" drawdownBounds,
      testProperty "realized volatility squared is variance" realizedIdentity,
      testProperty "expected shortfall is permutation invariant" esPermutation
    ]

scaleInvariantSimple :: Property
scaleInvariantSimple =
  forAll positivePrices $ \prices ->
    forAll (choose (0.25, 4.0)) $ \scale ->
      let source = U.fromList prices
          scaled = U.map (* scale) source
       in case (simpleReturns source, simpleReturns scaled) of
            (Right left, Right right) ->
              counterexample (show (U.toList left, U.toList right)) (vectorsClose left right)
            result -> counterexample (show result) False

scaleInvariantLog :: Property
scaleInvariantLog =
  forAll positivePrices $ \prices ->
    forAll (choose (0.25, 4.0)) $ \scale ->
      let source = U.fromList prices
          scaled = U.map (* scale) source
       in case (logReturns source, logReturns scaled) of
            (Right left, Right right) ->
              counterexample (show (U.toList left, U.toList right)) (vectorsClose left right)
            result -> counterexample (show result) False

bankruptcyAbsorbing :: [Double] -> Property
bankruptcyAbsorbing suffix =
  let bounded = fmap (\value -> max (-0.99) (min 10.0 value)) suffix
   in cumulativeReturn (U.fromList (0.1 : -1.0 : bounded)) === Right (-1.0)

drawdownBounds :: Property
drawdownBounds =
  forAll positivePrices $ \prices ->
    case maxDrawdown (U.fromList prices) of
      Left stableError -> counterexample (show stableError) False
      Right value -> property (value >= -1.0 && value <= 0.0)

realizedIdentity :: Property
realizedIdentity =
  forAll finiteReturns $ \returns ->
    let values = U.fromList returns
     in case (realizedVariance values, realizedVolatilityIntraday values) of
          (Right variance, Right volatility) ->
            counterexample
              (show (variance, volatility))
              (close (volatility * volatility) variance)
          result -> counterexample (show result) False

esPermutation :: Property
esPermutation =
  forAll finiteReturns $ \losses ->
    let left = historicalExpectedShortfall (U.fromList losses) 0.8
        right = historicalExpectedShortfall (U.fromList (reverse losses)) 0.8
     in case (left, right) of
          (Right leftValue, Right rightValue) ->
            counterexample (show (leftValue, rightValue)) (close leftValue rightValue)
          result -> counterexample (show result) False

positivePrices :: Gen [Double]
positivePrices = do
  count <- choose (2, 64)
  vectorOf count (choose (1.0, 10000.0))

finiteReturns :: Gen [Double]
finiteReturns = do
  count <- choose (2, 64)
  vectorOf count (choose (-0.5, 0.5))

vectorsClose :: U.Vector Double -> U.Vector Double -> Bool
vectorsClose left right =
  U.length left == U.length right
    && U.and (U.zipWith close left right)

close :: Double -> Double -> Bool
close left right =
  abs (left - right) <= 1.0e-10 * max 1.0 (max (abs left) (abs right))
