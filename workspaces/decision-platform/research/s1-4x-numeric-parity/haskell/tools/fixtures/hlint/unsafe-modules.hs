module Negative.UnsafeModules
  ( badCoerce,
    badDupable,
    badInterleave,
  )
where

import qualified GHC.IO.Unsafe as GhcUnsafe
import qualified Unsafe.Coerce as Coerce

badDupable :: Int
badDupable = GhcUnsafe.unsafeDupablePerformIO (pure 1)

badInterleave :: IO Int
badInterleave = GhcUnsafe.unsafeInterleaveIO (pure 1)

badCoerce :: Int -> Bool
badCoerce = Coerce.unsafeCoerce
