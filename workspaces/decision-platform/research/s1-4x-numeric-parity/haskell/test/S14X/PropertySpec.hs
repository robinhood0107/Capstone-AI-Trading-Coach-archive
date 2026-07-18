module S14X.PropertySpec (tests) where

import Test.Tasty (TestTree, testGroup)
import Test.Tasty.QuickCheck (testProperty)

import S14X.PropertyCases
  ( PropertyCase (PropertyCase),
    propertyCases,
  )

tests :: TestTree
tests =
  testGroup
    "properties"
    [testProperty propertyId invariant | PropertyCase propertyId invariant <- propertyCases]
