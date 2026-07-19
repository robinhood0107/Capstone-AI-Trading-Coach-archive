module Negative.CoreSystemIo (bad) where

import qualified System.IO as IO

bad :: IO.Handle -> IO ()
bad = IO.hClose
