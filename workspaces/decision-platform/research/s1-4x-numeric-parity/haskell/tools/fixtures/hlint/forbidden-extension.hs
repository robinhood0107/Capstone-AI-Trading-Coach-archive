{-# LANGUAGE GeneralizedNewtypeDeriving #-}

module Negative.ForbiddenExtension (Wrapped (..)) where

newtype Wrapped = Wrapped Int
  deriving (Eq)
