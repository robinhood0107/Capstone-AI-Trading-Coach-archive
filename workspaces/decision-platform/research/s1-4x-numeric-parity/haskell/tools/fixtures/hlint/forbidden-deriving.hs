{-# LANGUAGE DeriveAnyClass #-}
{-# LANGUAGE DerivingVia #-}

module Negative.ForbiddenDeriving (MarkerValue (..), ViaValue (..)) where

class Marker value

data MarkerValue = MarkerValue
  deriving anyclass (Marker)

newtype ViaValue = ViaValue Int
  deriving (Eq) via Int
