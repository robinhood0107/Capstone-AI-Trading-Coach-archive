module S14X.Core.Models
  ( ConditionalCoverageResult (..),
    IndependenceResult (..),
    LikelihoodResult (..),
    NumericResult (..),
    TransitionCounts (..),
    TrialProvenance (..),
  )
where

import Control.DeepSeq (NFData (rnf))
import Data.Vector.Unboxed (Vector)

import qualified Data.Vector.Unboxed as U

-- DSR은 effective trial count가 caller 주장과 같은지 검증할 수 있도록 provenance 전체를
-- 순수 core 경계에 전달한다.
data TrialProvenance = TrialProvenance
  { provenanceSchemaVersion :: String,
    provenanceMethod :: String,
    provenanceRawTrialCount :: Integer,
    provenanceEffectiveTrialCount :: Integer,
    provenanceSamplingFrequency :: String,
    provenanceRegistrySha256 :: String,
    provenanceVarianceDof :: Integer
  }
  deriving stock (Eq, Show)

data TransitionCounts = TransitionCounts
  { transitionN00 :: Int,
    transitionN01 :: Int,
    transitionN10 :: Int,
    transitionN11 :: Int
  }
  deriving stock (Eq, Show)

data LikelihoodResult = LikelihoodResult
  { likelihoodStatistic :: Double,
    likelihoodPValue :: Double,
    likelihoodReject :: Bool,
    likelihoodObservations :: Int,
    likelihoodExceptions :: Int,
    likelihoodDegreesOfFreedom :: Int,
    likelihoodSignificance :: Double
  }
  deriving stock (Eq, Show)

data IndependenceResult = IndependenceResult
  { independenceStatistic :: Double,
    independencePValue :: Double,
    independenceReject :: Bool,
    independenceObservations :: Int,
    independenceExceptions :: Int,
    independenceDegreesOfFreedom :: Int,
    independenceSignificance :: Double,
    independenceTransitions :: TransitionCounts
  }
  deriving stock (Eq, Show)

data ConditionalCoverageResult = ConditionalCoverageResult
  { conditionalStatistic :: Double,
    conditionalPValue :: Double,
    conditionalReject :: Bool,
    conditionalObservations :: Int,
    conditionalExceptions :: Int,
    conditionalDegreesOfFreedom :: Int,
    conditionalSignificance :: Double,
    conditionalTransitions :: TransitionCounts,
    conditionalConditionedObservations :: Int,
    conditionalConditionedExceptions :: Int,
    conditionalUnconditionalComponent :: Double,
    conditionalIndependenceComponent :: Double
  }
  deriving stock (Eq, Show)

data NumericResult
  = ScalarResult Double
  | VectorResult (Vector Double)
  | LikelihoodRecord LikelihoodResult
  | IndependenceRecord IndependenceResult
  | ConditionalCoverageRecord ConditionalCoverageResult
  deriving stock (Eq, Show)

instance NFData TrialProvenance where
  rnf (TrialProvenance schemaVersion method rawCount effectiveCount frequency digest dof) =
    rnf schemaVersion
      `seq` rnf method
      `seq` rnf rawCount
      `seq` rnf effectiveCount
      `seq` rnf frequency
      `seq` rnf digest
      `seq` rnf dof

instance NFData TransitionCounts where
  rnf (TransitionCounts n00 n01 n10 n11) =
    rnf n00 `seq` rnf n01 `seq` rnf n10 `seq` rnf n11

instance NFData LikelihoodResult where
  rnf (LikelihoodResult statistic pValue reject observations exceptions dof significance) =
    rnf statistic
      `seq` rnf pValue
      `seq` rnf reject
      `seq` rnf observations
      `seq` rnf exceptions
      `seq` rnf dof
      `seq` rnf significance

instance NFData IndependenceResult where
  rnf (IndependenceResult statistic pValue reject observations exceptions dof significance counts) =
    rnf statistic
      `seq` rnf pValue
      `seq` rnf reject
      `seq` rnf observations
      `seq` rnf exceptions
      `seq` rnf dof
      `seq` rnf significance
      `seq` rnf counts

instance NFData ConditionalCoverageResult where
  rnf
    ( ConditionalCoverageResult
        statistic
        pValue
        reject
        observations
        exceptions
        dof
        significance
        counts
        conditionedObservations
        conditionedExceptions
        unconditionalComponent
        independenceComponent
      ) =
      rnf statistic
        `seq` rnf pValue
        `seq` rnf reject
        `seq` rnf observations
        `seq` rnf exceptions
        `seq` rnf dof
        `seq` rnf significance
        `seq` rnf counts
        `seq` rnf conditionedObservations
        `seq` rnf conditionedExceptions
        `seq` rnf unconditionalComponent
        `seq` rnf independenceComponent

instance NFData NumericResult where
  rnf numericResult =
    case numericResult of
      ScalarResult value -> rnf value
      VectorResult values -> U.foldl' (\unit value -> rnf value `seq` unit) () values
      LikelihoodRecord value -> rnf value
      IndependenceRecord value -> rnf value
      ConditionalCoverageRecord value -> rnf value
