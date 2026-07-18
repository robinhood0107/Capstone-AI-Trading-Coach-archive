module Negative.QualifiedThrow (bad) where

import qualified Control.Exception as Exception

bad :: Int
bad = Exception.throw (userError "forbidden")
