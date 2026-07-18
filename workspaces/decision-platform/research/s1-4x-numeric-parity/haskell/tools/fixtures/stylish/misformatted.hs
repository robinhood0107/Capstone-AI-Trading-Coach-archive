module Negative.Misformatted (value) where
import Data.Maybe (maybe)
value::Maybe Int->Int
value=maybe 0 id
