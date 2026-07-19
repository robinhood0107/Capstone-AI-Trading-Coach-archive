module Negative.QualifiedThrowIo (bad) where

import qualified Control.Exception as Exception

bad :: IO ()
bad = Exception.throwIO (userError "forbidden")
