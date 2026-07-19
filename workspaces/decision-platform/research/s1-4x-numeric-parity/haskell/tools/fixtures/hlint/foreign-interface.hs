{-# LANGUAGE ForeignFunctionInterface #-}

module Negative.ForeignInterface (badPointer) where

import qualified Foreign.Ptr as Foreign

foreign import ccall unsafe "forbidden_import"
  forbiddenImport :: IO Int

foreign export ccall "forbidden_export"
  forbiddenExport :: IO Int

forbiddenExport :: IO Int
forbiddenExport = pure 1

badPointer :: Foreign.Ptr value
badPointer = Foreign.nullPtr
