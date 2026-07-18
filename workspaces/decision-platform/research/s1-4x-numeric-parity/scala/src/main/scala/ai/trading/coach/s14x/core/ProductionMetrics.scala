package ai.trading.coach.s14x.core

object ProductionMetrics:
  /** JSON semantic precedence용 raw boundary이며 성공 시 defensive immutable snapshot을 쓴다. */
  def simpleReturnsRaw(raw: Any): Either[StableError, Vector[Double]] =
    Validation.productionRawSequence(raw, minimumLength = 2).flatMap(simpleReturns)

  def simpleReturns(prices: Vector[Double]): Either[StableError, Vector[Double]] =
    Validation.productionSequence(prices, minimumLength = 2).flatMap { values =>
      if values.exists(_ <= 0.0) then Left(StableError.PricesNonPositive)
      else
        val result = values.zip(values.drop(1)).map { case (previous, current) =>
          NumericPrimitives.normalizeZero(current / previous - 1.0)
        }
        if result.forall(_.isFinite) then Right(result) else Left(StableError.ResultNonFinite)
    }

  def logReturns(prices: Vector[Double]): Either[StableError, Vector[Double]] =
    Validation.productionSequence(prices, minimumLength = 2).flatMap { values =>
      if values.exists(_ <= 0.0) then Left(StableError.PricesNonPositive)
      else
        val result = values.zip(values.drop(1)).map { case (previous, current) =>
          NumericPrimitives.normalizeZero(math.log(current) - math.log(previous))
        }
        if result.forall(_.isFinite) then Right(result) else Left(StableError.ResultNonFinite)
    }

  def cumulativeReturn(returns: Vector[Double]): Either[StableError, Double] =
    Validation.productionSequence(returns, minimumLength = 1).flatMap { values =>
      if values.exists(_ < -1.0) then Left(StableError.SimpleReturnBelowMinusOne)
      else if values.exists(_ == -1.0) then Right(-1.0)
      else
        val result = values.foldLeft(1.0)((product, value) => product * (1.0 + value)) - 1.0
        Validation.finiteProduction(result)
    }

  def cagr(
      prices: Vector[Double],
      periodsPerYear: BigInt = BigInt(252),
  ): Either[StableError, Double] =
    for
      values <- Validation.productionSequence(prices, minimumLength = 2)
      periods <- Validation.positivePeriods(periodsPerYear)
      result <-
        if values.exists(_ <= 0.0) then Left(StableError.PricesNonPositive)
        else
          val first = values.take(1).foldLeft(Double.NaN)((_, value) => value)
          val finalValue = values.drop(values.size - 1).foldLeft(Double.NaN)((_, value) => value)
          val annualization = periods.toDouble / (values.size - 1).toDouble
          Validation.finiteProduction(
            math.expm1(annualization * (math.log(finalValue) - math.log(first)))
          )
    yield result

  def realizedVolatility(logReturns: Vector[Double]): Either[StableError, Double] =
    Validation
      .productionSequence(logReturns, minimumLength = 2)
      .flatMap(values => Validation.finiteProduction(NumericPrimitives.sampleStandardDeviation(values)))

  def annualizedVolatility(
      logReturns: Vector[Double],
      periodsPerYear: BigInt = BigInt(252),
  ): Either[StableError, Double] =
    for
      values <- Validation.productionSequence(logReturns, minimumLength = 2)
      periods <- Validation.positivePeriods(periodsPerYear)
      result <- Validation.finiteProduction(
        NumericPrimitives.sampleStandardDeviation(values) * math.sqrt(periods.toDouble)
      )
    yield result

  def maxDrawdown(equityCurve: Vector[Double]): Either[StableError, Double] =
    Validation.productionSequence(equityCurve, minimumLength = 1).flatMap { values =>
      val first = values.take(1).foldLeft(Double.NaN)((_, value) => value)
      if first <= 0.0 then Left(StableError.EquityInitialNonPositive)
      else if values.drop(1).exists(_ < 0.0) then Left(StableError.EquityNegative)
      else
        val (_, minimumDrawdown) =
          values.foldLeft((first, 0.0)) { case ((runningPeak, minimum), value) =>
            val nextPeak = math.max(runningPeak, value)
            val drawdown = value / nextPeak - 1.0
            (nextPeak, math.min(minimum, drawdown))
          }
        Validation.finiteProduction(minimumDrawdown)
    }

  def sharpeRatio(
      returns: Vector[Double],
      riskFreeRate: Double = 0.0,
      periodsPerYear: BigInt = BigInt(252),
  ): Either[StableError, Double] =
    for
      values <- Validation.productionSequence(returns, minimumLength = 2)
      riskFree <-
        if riskFreeRate.isFinite then Right(riskFreeRate)
        else Left(StableError.RiskFreeRateInvalid)
      periods <- Validation.positivePeriods(periodsPerYear)
      excess = values.map(_ - riskFree)
      denominator = NumericPrimitives.sampleStandardDeviation(excess)
      result <-
        if denominator == 0.0 then Left(StableError.DenominatorZero)
        else
          Validation.finiteProduction(
            NumericPrimitives.compensatedSum(excess) / excess.size.toDouble / denominator *
              math.sqrt(periods.toDouble)
          )
    yield result

  def sortinoRatio(
      returns: Vector[Double],
      targetReturn: Double = 0.0,
      periodsPerYear: BigInt = BigInt(252),
  ): Either[StableError, Double] =
    for
      values <- Validation.productionSequence(returns, minimumLength = 2)
      target <-
        if targetReturn.isFinite then Right(targetReturn)
        else Left(StableError.TargetReturnInvalid)
      periods <- Validation.positivePeriods(periodsPerYear)
      excess = values.map(_ - target)
      downside = excess.map(value => math.min(value, 0.0))
      denominator = math.sqrt(
        NumericPrimitives.compensatedSum(downside.map(value => value * value)) /
          downside.size.toDouble
      )
      result <-
        if denominator == 0.0 then Left(StableError.DenominatorZero)
        else
          Validation.finiteProduction(
            NumericPrimitives.compensatedSum(excess) / excess.size.toDouble / denominator *
              math.sqrt(periods.toDouble)
          )
    yield result

  private def historicalVarKernel(values: Vector[Double], confidence: Double): Double =
    val ordered = values.sorted
    val h = (ordered.size - 1).toDouble * (1.0 - confidence)
    val lowerIndex = math.floor(h).toInt
    val fraction = h - lowerIndex.toDouble
    val lower = ordered.lift(lowerIndex).fold(Double.NaN)(identity)
    val upper = ordered.lift(math.min(lowerIndex + 1, ordered.size - 1)).fold(Double.NaN)(identity)
    lower + fraction * (upper - lower)

  def historicalVar(
      returns: Vector[Double],
      confidence: Double = 0.95,
  ): Either[StableError, Double] =
    for
      values <- Validation.productionSequence(returns, minimumLength = 2)
      probability <- Validation.productionConfidence(confidence)
      result <- Validation.finiteProduction(historicalVarKernel(values, probability))
    yield result

  def historicalCvar(
      returns: Vector[Double],
      confidence: Double = 0.95,
  ): Either[StableError, Double] =
    for
      values <- Validation.productionSequence(returns, minimumLength = 2)
      probability <- Validation.productionConfidence(confidence)
      threshold <- Validation.finiteProduction(historicalVarKernel(values, probability))
      tail = values.filter(_ <= threshold)
      result <-
        if tail.isEmpty then Left(StableError.TailEmpty)
        else
          Validation.finiteProduction(
            NumericPrimitives.compensatedSum(tail) / tail.size.toDouble
          )
    yield result
