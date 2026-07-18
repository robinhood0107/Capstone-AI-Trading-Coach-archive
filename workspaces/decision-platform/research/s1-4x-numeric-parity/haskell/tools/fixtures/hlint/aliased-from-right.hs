module Negative.AliasedFromRight (bad) where

import qualified Data.Either as EitherAlias

bad :: Either String Int -> Int
bad = EitherAlias.fromRight 0
