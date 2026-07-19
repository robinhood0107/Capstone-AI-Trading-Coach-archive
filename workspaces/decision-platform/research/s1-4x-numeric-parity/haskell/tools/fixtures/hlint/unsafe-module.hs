module Negative.UnsafeModule (bad) where

import qualified System.IO.Unsafe as Unsafe

bad :: Int
bad = Unsafe.unsafePerformIO (pure 1)
