module Negative.AliasedFromLeft (bad) where

import qualified Data.Either as EitherAlias

bad :: Either Int String -> Int
bad = EitherAlias.fromLeft 0
