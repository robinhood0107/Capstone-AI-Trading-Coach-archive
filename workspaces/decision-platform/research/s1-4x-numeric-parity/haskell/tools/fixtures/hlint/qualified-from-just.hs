module Negative.QualifiedFromJust (bad) where

import qualified Data.Maybe as Maybe

bad :: Maybe Int -> Int
bad = Maybe.fromJust
