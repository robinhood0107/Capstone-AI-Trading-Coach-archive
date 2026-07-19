module Negative.DebugTrace (bad) where

import qualified Debug.Trace as Trace

bad :: Int
bad = Trace.trace "forbidden" 1
