module Negative.UncheckedFolds (badFold, badMaximum, badMinimum) where

import qualified Data.List as List

badFold :: [Int] -> Int
badFold = List.foldl1 (+)

badMaximum :: [Int] -> Int
badMaximum = List.maximum

badMinimum :: [Int] -> Int
badMinimum = List.minimum
