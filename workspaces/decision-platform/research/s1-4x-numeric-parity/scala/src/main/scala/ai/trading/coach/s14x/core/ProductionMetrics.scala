package ai.trading.coach.s14x.core

object ProductionMetrics:
  /** JSON semantic precedence용 raw boundary이며 성공 시 defensive immutable snapshot을 쓴다. */
  def simpleReturnsRaw(raw: Any): Either[StableError, Vector[Double]] =
    Validation.productionRawSequence(raw, minimumLength = 2).flatMap(simpleReturns)

  /** 양수 가격 시계열을 받아 인접 단순수익률을 반환하며, frozen production 오류 우선순위를 보존한다. */
  def simpleReturns(prices: Vector[Double]): Either[StableError, Vector[Double]] =
    Validation.productionSequence(prices, minimumLength = 2).flatMap { values =>
      if values.exists(_ <= 0.0) then Left(StableError.PricesNonPositive)
      else
        val result = values.zip(values.drop(1)).map { case (previous, current) =>
          NumericPrimitives.normalizeZero(current / previous - 1.0)
        }
        if result.forall(_.isFinite) then Right(result) else Left(StableError.ResultNonFinite)
    }

  /** 양수 immutable 가격 시계열을 검증한 뒤 인접 로그수익률을 반환한다. */
  def logReturns(prices: Vector[Double]): Either[StableError, Vector[Double]] =
    Validation.productionSequence(prices, minimumLength = 2).flatMap { values =>
      if values.exists(_ <= 0.0) then Left(StableError.PricesNonPositive)
      else
        val result = values.zip(values.drop(1)).map { case (previous, current) =>
          NumericPrimitives.normalizeZero(math.log(current) - math.log(previous))
        }
        if result.forall(_.isFinite) then Right(result) else Left(StableError.ResultNonFinite)
    }

  /** 단순수익률 시계열을 복리 누적하고, -1 미만 입력과 비유한 결과를 stable error로 반환한다. */
  def cumulativeReturn(returns: Vector[Double]): Either[StableError, Double] =
    Validation.productionSequence(returns, minimumLength = 1).flatMap { values =>
      if values.exists(_ < -1.0) then Left(StableError.SimpleReturnBelowMinusOne)
      else if values.exists(_ == -1.0) then Right(-1.0)
      else
        val result = values.foldLeft(1.0)((product, value) => product * (1.0 + value)) - 1.0
        Validation.finiteProduction(result)
    }

  /** 양수 가격 경로와 양의 연환산 주기를 받아 기하 연환산 수익률을 계산한다. */
  def cagr(
      prices: Vector[Double],
      periodsPerYear: BigInt = BigInt(252)
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

  /** 두 개 이상의 로그수익률을 표본 표준편차로 집계해 실현 변동성을 반환한다. */
  def realizedVolatility(logReturns: Vector[Double]): Either[StableError, Double] =
    Validation
      .productionSequence(logReturns, minimumLength = 2)
      .flatMap(values =>
        Validation.finiteProduction(NumericPrimitives.sampleStandardDeviation(values))
      )

  /** 로그수익률 표본 변동성을 양의 연환산 주기의 제곱근으로 확장한다. */
  def annualizedVolatility(
      logReturns: Vector[Double],
      periodsPerYear: BigInt = BigInt(252)
  ): Either[StableError, Double] =
    for
      values <- Validation.productionSequence(logReturns, minimumLength = 2)
      periods <- Validation.positivePeriods(periodsPerYear)
      result <- Validation.finiteProduction(
        NumericPrimitives.sampleStandardDeviation(values) * math.sqrt(periods.toDouble)
      )
    yield result

  /** 비음수 자산곡선의 running peak 대비 최소 수익률을 최대낙폭으로 반환한다. */
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

  /** 수익률·주기당 무위험수익률·연환산 주기를 받아 표본 Sharpe 비율을 반환한다. */
  def sharpeRatio(
      returns: Vector[Double],
      riskFreeRate: Double = 0.0,
      periodsPerYear: BigInt = BigInt(252)
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

  /** 수익률·목표수익률·연환산 주기를 받아 전체 관측치 기준 downside Sortino 비율을 반환한다. */
  def sortinoRatio(
      returns: Vector[Double],
      targetReturn: Double = 0.0,
      periodsPerYear: BigInt = BigInt(252)
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

  /** 수익률 표본과 신뢰수준을 받아 frozen 선형 보간 규칙의 historical VaR를 반환한다. */
  def historicalVar(
      returns: Vector[Double],
      confidence: Double = 0.95
  ): Either[StableError, Double] =
    for
      values <- Validation.productionSequence(returns, minimumLength = 2)
      probability <- Validation.productionConfidence(confidence)
      result <- Validation.finiteProduction(historicalVarKernel(values, probability))
    yield result

  /** 수익률 표본과 신뢰수준을 받아 VaR 이하 관측치 평균인 historical CVaR를 반환한다. */
  def historicalCvar(
      returns: Vector[Double],
      confidence: Double = 0.95
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
