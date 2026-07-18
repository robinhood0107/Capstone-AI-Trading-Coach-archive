package ai.trading.coach.s14x.core

object AdvancedRisk:
  def historicalExpectedShortfall(
      losses: Vector[Double],
      confidence: Double = 0.95,
  ): Either[StableError, Double] =
    for
      values <- Validation.researchSequence(losses)
      probability <- Validation.researchConfidence(confidence)
      ordered = values.sorted.reverse
      tailMass = ordered.size.toDouble * (1.0 - probability)
      weights = ordered.indices.toVector.map(index =>
        math.min(1.0, math.max(0.0, tailMass - index.toDouble)) / tailMass
      )
      scale = ordered.map(math.abs).foldLeft(0.0)(math.max)
      normalized =
        if scale == 0.0 then 0.0
        else
          NumericPrimitives.compensatedSum(
            ordered.zip(weights).map { case (value, weight) => weight * (value / scale) }
          )
      tolerance = 64.0 * Math.ulp(1.0)
      result <-
        if normalized < -1.0 - tolerance || normalized > 1.0 + tolerance then
          Left(StableError.ResearchResultNonFinite)
        else
          Validation.finiteResearch(math.min(1.0, math.max(-1.0, normalized)) * scale)
    yield result

  def realizedVariance(intradayLogReturns: Vector[Double]): Either[StableError, Double] =
    Validation.researchSequence(intradayLogReturns).flatMap { values =>
      Validation.finiteResearch(
        NumericPrimitives.compensatedSum(values.map(value => value * value))
      )
    }

  def realizedVolatilityIntraday(
      intradayLogReturns: Vector[Double]
  ): Either[StableError, Double] =
    realizedVariance(intradayLogReturns).flatMap(value =>
      Validation.finiteResearch(math.sqrt(value))
    )

  def loAdjustedSharpeRatio(
      returns: Vector[Double],
      aggregationPeriods: BigInt,
      riskFreeRate: Double = 0.0,
  ): Either[StableError, Double] =
    for
      values <- Validation.researchSequence(returns, minimumLength = 2)
      periods <-
        if aggregationPeriods <= 0 then Left(StableError.AggregationPeriodsInvalid)
        else if aggregationPeriods >= values.size then Left(StableError.ResearchInputTooShort)
        else Right(aggregationPeriods.toInt)
      riskFree <-
        if riskFreeRate.isFinite then Right(riskFreeRate)
        else Left(StableError.ResearchInputInvalid)
      excess = values.map(_ - riskFree)
      mean = NumericPrimitives.compensatedSum(excess) / excess.size.toDouble
      centered = excess.map(_ - mean)
      gammaZero =
        NumericPrimitives.compensatedSum(centered.map(value => value * value)) /
          centered.size.toDouble
      result <-
        if !gammaZero.isFinite || gammaZero <= 0.0 then Left(StableError.MomentInvalid)
        else
          val autocorrelation = (1 until periods).toVector.map { lag =>
            val gammaLag = NumericPrimitives.compensatedSum(
              centered.drop(lag).zip(centered.take(centered.size - lag)).map {
                case (current, previous) => current * previous
              }
            ) / centered.size.toDouble
            (1.0 - lag.toDouble / periods.toDouble) * (gammaLag / gammaZero)
          }
          val denominator =
            1.0 + 2.0 * NumericPrimitives.compensatedSum(autocorrelation)
          if !denominator.isFinite || denominator <= 0.0 then Left(StableError.MomentInvalid)
          else
            Validation.finiteResearch(
              mean / math.sqrt(gammaZero) * math.sqrt(periods.toDouble / denominator)
            )
    yield result

  private def validatedPsr(
      observedSharpe: Double,
      benchmarkSharpe: Double,
      sampleSize: BigInt,
      skewness: Double,
      kurtosis: Double,
  ): Either[StableError, (Double, Double, BigInt, Double)] =
    for
      observed <-
        if observedSharpe.isFinite then Right(observedSharpe)
        else Left(StableError.ResearchInputInvalid)
      benchmark <-
        if benchmarkSharpe.isFinite then Right(benchmarkSharpe)
        else Left(StableError.ResearchInputInvalid)
      observations <- Validation.sampleSize(sampleSize)
      _ <- Validation.momentPair(skewness, kurtosis)
      radicand =
        1.0 - skewness * observed +
          ((kurtosis - 1.0) / 4.0) * observed * observed
      _ <-
        if radicand.isFinite && radicand > 0.0 then Right(())
        else Left(StableError.MomentInvalid)
    yield (observed, benchmark, observations, radicand)

  def probabilisticSharpeRatio(
      observedSharpe: Double,
      benchmarkSharpe: Double,
      sampleSize: BigInt,
      skewness: Double,
      kurtosis: Double,
  ): Either[StableError, Double] =
    validatedPsr(observedSharpe, benchmarkSharpe, sampleSize, skewness, kurtosis).flatMap {
      case (observed, benchmark, observations, radicand) =>
        val z =
          (observed - benchmark) * math.sqrt((observations - BigInt(1)).toDouble) /
            math.sqrt(radicand)
        if !z.isFinite then Left(StableError.ResearchResultNonFinite)
        else NumericPrimitives.probability(NumericPrimitives.normalCdf(z))
    }

  def deflatedSharpeRatio(
      observedSharpe: Double,
      sampleSize: BigInt,
      skewness: Double,
      kurtosis: Double,
      trialCount: BigInt,
      sharpeEstimateVariance: Double,
      trialProvenance: TrialProvenance,
  ): Either[StableError, Double] =
    for
      validated <- validatedPsr(observedSharpe, 0.0, sampleSize, skewness, kurtosis)
      trials <- Validation.trialCount(trialCount)
      variance <-
        if !sharpeEstimateVariance.isFinite || sharpeEstimateVariance <= 0.0 then
          Left(StableError.TrialVarianceInvalid)
        else Right(sharpeEstimateVariance)
      _ <- Validation.provenance(trialProvenance, trials)
      reciprocal = 1.0 / trials.toDouble
      firstQuantile = -NumericPrimitives.normalInverseCdf(reciprocal)
      secondQuantile = -NumericPrimitives.normalInverseCdf(reciprocal / math.E)
      benchmark = math.sqrt(variance) * (
        (1.0 - NumericPrimitives.EulerMascheroni) * firstQuantile +
          NumericPrimitives.EulerMascheroni * secondQuantile
      )
      _ <-
        if benchmark.isFinite then Right(())
        else Left(StableError.ResearchResultNonFinite)
      result <- probabilisticSharpeRatio(
        validated._1,
        benchmark,
        validated._3,
        skewness,
        kurtosis,
      )
    yield result

  private final case class Backtest(
      realized: Vector[Double],
      forecast: Vector[Double],
      exceptions: Vector[Int],
  )

  private def validateBacktest(
      realizedLosses: Vector[Double],
      forecastVars: Vector[Double],
      minimumLength: Int,
  ): Either[StableError, Backtest] =
    for
      realized <- Validation.researchSequence(realizedLosses)
      forecast <-
        if forecastVars.exists(value => !value.isFinite) then Left(StableError.ResearchInputInvalid)
        else Right(Vector.from(forecastVars))
      _ <-
        if realized.size == forecast.size then Right(())
        else Left(StableError.ForecastShapeInvalid)
      _ <-
        if realized.size >= minimumLength then Right(())
        else Left(StableError.ResearchInputTooShort)
      _ <-
        if forecast.exists(_ < 0.0) then Left(StableError.ForecastVarNegative)
        else Right(())
      exceptions = realized.zip(forecast).map { case (loss, valueAtRisk) =>
        if loss > valueAtRisk then 1 else 0
      }
    yield Backtest(realized, forecast, exceptions)

  private def transitions(exceptions: Vector[Int]): Transitions =
    exceptions.zip(exceptions.drop(1)).foldLeft(Transitions(0, 0, 0, 0)) {
      case (counts, (0, 0)) => counts.copy(n00 = counts.n00 + 1)
      case (counts, (0, 1)) => counts.copy(n01 = counts.n01 + 1)
      case (counts, (1, 0)) => counts.copy(n10 = counts.n10 + 1)
      case (counts, (1, 1)) => counts.copy(n11 = counts.n11 + 1)
      case (counts, _)      => counts
    }

  private def likelihoods(
      counts: Transitions
  ): Either[StableError, (Double, Double)] =
    val rowZero = counts.n00 + counts.n01
    val rowOne = counts.n10 + counts.n11
    if rowZero == 0 || rowOne == 0 then Left(StableError.InsufficientSample)
    else
      val total = rowZero + rowOne
      val piZeroOne = counts.n01.toDouble / rowZero.toDouble
      val piOneOne = counts.n11.toDouble / rowOne.toDouble
      val pi = (counts.n01 + counts.n11).toDouble / total.toDouble
      for
        independent <- NumericPrimitives.bernoulliLogLikelihood(
          total,
          counts.n01 + counts.n11,
          pi,
        )
        markov00 <- NumericPrimitives.xlogComplement(counts.n00, piZeroOne)
        markov01 <- NumericPrimitives.xlogProbability(counts.n01, piZeroOne)
        markov10 <- NumericPrimitives.xlogComplement(counts.n10, piOneOne)
        markov11 <- NumericPrimitives.xlogProbability(counts.n11, piOneOne)
      yield (independent, markov00 + markov01 + markov10 + markov11)

  private def kupiecStatistic(
      observations: Int,
      exceptions: Int,
      confidence: Double,
  ): Either[StableError, Double] =
    val maximumLikelihood = exceptions.toDouble / observations.toDouble
    val nullLog =
      NumericPrimitives.confidenceExceptionLogLikelihood(observations, exceptions, confidence)
    NumericPrimitives
      .bernoulliLogLikelihood(observations, exceptions, maximumLikelihood)
      .flatMap(alternative => NumericPrimitives.likelihoodRatio(nullLog, alternative))

  def kupiecUnconditionalCoverageTest(
      realizedLosses: Vector[Double],
      forecastVars: Vector[Double],
      confidence: Double,
      significance: Double = 0.05,
  ): Either[StableError, LikelihoodResult] =
    for
      inputs <- validateBacktest(realizedLosses, forecastVars, minimumLength = 1)
      probability <- Validation.researchConfidence(confidence)
      alpha <- Validation.significance(significance)
      exceptionCount = inputs.exceptions.sum
      statistic <- kupiecStatistic(inputs.exceptions.size, exceptionCount, probability)
      pValue <- NumericPrimitives.chiSquareOneSurvival(statistic)
    yield LikelihoodResult(
      statistic,
      pValue,
      pValue < alpha,
      inputs.exceptions.size,
      exceptionCount,
      1,
      alpha,
    )

  def christoffersenIndependenceTest(
      realizedLosses: Vector[Double],
      forecastVars: Vector[Double],
      significance: Double = 0.05,
  ): Either[StableError, IndependenceResult] =
    for
      inputs <- validateBacktest(realizedLosses, forecastVars, minimumLength = 2)
      alpha <- Validation.significance(significance)
      counts = transitions(inputs.exceptions)
      logs <- likelihoods(counts)
      statistic <- NumericPrimitives.likelihoodRatio(logs._1, logs._2)
      pValue <- NumericPrimitives.chiSquareOneSurvival(statistic)
    yield IndependenceResult(
      statistic,
      pValue,
      pValue < alpha,
      inputs.exceptions.size,
      inputs.exceptions.sum,
      1,
      alpha,
      counts,
    )

  /** Conditional coverage는 I_2:T에서 UC와 Markov likelihood를 모두 맞춘다. */
  def christoffersenConditionalCoverageTest(
      realizedLosses: Vector[Double],
      forecastVars: Vector[Double],
      confidence: Double,
      significance: Double = 0.05,
  ): Either[StableError, ConditionalCoverageResult] =
    for
      inputs <- validateBacktest(realizedLosses, forecastVars, minimumLength = 2)
      probability <- Validation.researchConfidence(confidence)
      alpha <- Validation.significance(significance)
      counts = transitions(inputs.exceptions)
      logs <- likelihoods(counts)
      conditionedObservations = inputs.exceptions.size - 1
      conditionedExceptions = counts.n01 + counts.n11
      nullLog = NumericPrimitives.confidenceExceptionLogLikelihood(
        conditionedObservations,
        conditionedExceptions,
        probability,
      )
      unconditional <- NumericPrimitives.likelihoodRatio(nullLog, logs._1)
      independence <- NumericPrimitives.likelihoodRatio(logs._1, logs._2)
      direct <- NumericPrimitives.likelihoodRatio(nullLog, logs._2)
      combined = unconditional + independence
      _ <-
        if math.abs(direct - combined) <= NumericPrimitives.likelihoodTolerance(nullLog, logs._2)
        then Right(())
        else Left(StableError.LikelihoodInvalid)
      statistic <- Validation.finiteResearch(combined)
      pValue <- NumericPrimitives.chiSquareTwoSurvival(statistic)
    yield ConditionalCoverageResult(
      statistic,
      pValue,
      pValue < alpha,
      inputs.exceptions.size,
      inputs.exceptions.sum,
      2,
      alpha,
      counts,
      conditionedObservations,
      conditionedExceptions,
      unconditional,
      independence,
    )
