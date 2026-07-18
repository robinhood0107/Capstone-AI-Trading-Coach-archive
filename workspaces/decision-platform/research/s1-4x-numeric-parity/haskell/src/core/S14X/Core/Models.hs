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

-- | DSR이 effective trial count의 caller 주장을 검증하도록 frozen provenance 전체를 전달한다.
-- schema·method·frequency·registry SHA와 variance DoF를 누락 없이 검증해야 한다.
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

-- | Christoffersen 인접 예외 상태의 @00,01,10,11@ 전이 횟수다.
-- 모든 field는 validation 경계가 만든 비음수 count를 담는다.
data TransitionCounts = TransitionCounts
  { transitionN00 :: Int,
    transitionN01 :: Int,
    transitionN10 :: Int,
    transitionN11 :: Int
  }
  deriving stock (Eq, Show)

-- | Kupiec 단일 자유도 likelihood 결과와 관측 metadata를 결속한다.
-- statistic·p-value·significance는 finite/negative-zero 결과 gate를 통과해야 한다.
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

-- | Christoffersen independence 결과와 사용한 전이 count를 함께 보존한다.
-- conditioning 가능한 표본에서만 core가 이 값을 생성한다.
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

-- | unconditional coverage와 independence 성분을 합친 conditional coverage 결과다.
-- 두 성분과 conditioned 관측 수를 함께 내보내 comparator가 합성 근거를 감사할 수 있게 한다.
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

-- | 20개 kernel의 scalar, vector, 세 가지 likelihood record 출력을 닫힌 합으로 표현한다.
-- transport와 benchmark는 이 shape를 검사한 뒤 frozen JSON 결과로 직렬화한다.
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
