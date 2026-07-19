module Negative.PartialAndUnsafe
  ( badDropLast,
    badError,
    badHead,
    badIndex,
    badInit,
    badLast,
    badRead,
    badTail,
    badUndefined,
  )
where

badUndefined :: Int
badUndefined = undefined

badError :: Int
badError = error "forbidden"

badHead :: [Int] -> Int
badHead = head

badTail :: [Int] -> [Int]
badTail = tail

badInit :: [Int] -> [Int]
badInit = init

badLast :: [Int] -> Int
badLast = last

badIndex :: [Int] -> Int
badIndex values = values !! 0

badRead :: String -> Int
badRead = read

badDropLast :: [Int] -> [Int]
badDropLast values = init values
