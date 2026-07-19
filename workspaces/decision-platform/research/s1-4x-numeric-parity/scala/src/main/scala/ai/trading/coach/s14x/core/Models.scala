package ai.trading.coach.s14x.core

final case class TrialProvenance(
    schemaVersion: String,
    method: String,
    rawTrialCount: BigInt,
    effectiveTrialCount: BigInt,
    samplingFrequency: String,
    trialRegistrySha256: String,
    varianceDdof: BigInt
) derives CanEqual

final case class Transitions(n00: Int, n01: Int, n10: Int, n11: Int) derives CanEqual

final case class LikelihoodResult(
    statistic: Double,
    pValue: Double,
    reject: Boolean,
    observations: Int,
    exceptions: Int,
    degreesOfFreedom: Int,
    significance: Double
) derives CanEqual

final case class IndependenceResult(
    statistic: Double,
    pValue: Double,
    reject: Boolean,
    observations: Int,
    exceptions: Int,
    degreesOfFreedom: Int,
    significance: Double,
    transitions: Transitions
) derives CanEqual

final case class ConditionalCoverageResult(
    statistic: Double,
    pValue: Double,
    reject: Boolean,
    observations: Int,
    exceptions: Int,
    degreesOfFreedom: Int,
    significance: Double,
    transitions: Transitions,
    conditionedObservations: Int,
    conditionedExceptions: Int,
    unconditionalComponentStatistic: Double,
    independenceComponentStatistic: Double
) derives CanEqual

sealed trait NumericResult derives CanEqual

object NumericResult:
  final case class Scalar(value: Double) extends NumericResult
  final case class VectorResult(values: Vector[Double]) extends NumericResult
  final case class Likelihood(value: LikelihoodResult) extends NumericResult
  final case class Independence(value: IndependenceResult) extends NumericResult
  final case class Conditional(value: ConditionalCoverageResult) extends NumericResult
